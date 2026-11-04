"""Unattended Actions entry point, reusing the existing incident integration."""
import logging
import os
import sys
import time
from pathlib import Path

from .config import load_settings, notion_id
from .http import APIError
from .notion.bootstrap import bootstrap
from .notion.client import NotionClient
from .service import Reporter
from .slack.client import SlackClient
from .slack.detector import candidates
from .storage.repository import Repository
from .storage.encrypted import restore_state, save_state
from .backfill import Backfills, progress_text
from .domain.period import DateRange
from .notion.statistics import StatisticsSync


def page_title(page):
    return "".join(t.get("plain_text", t.get("text", {}).get("content", ""))
                   for p in page.get("properties", {}).values() if p.get("type") == "title"
                   for t in p.get("title", []))


def discover_qa(client, incident_database=""):
    # Prefer QA in the ancestor chain of the existing incident DB.
    if incident_database:
        parent = client.call("GET", "/databases/" + notion_id(incident_database)).get("parent", {})
        seen = set()
        while parent.get("type") == "page_id" and parent["page_id"] not in seen:
            seen.add(parent["page_id"])
            page = client.call("GET", "/pages/" + parent["page_id"])
            if page_title(page).strip().casefold() == "qa":
                return page["id"]
            parent = page.get("parent", {})
    matches, cursor = [], None
    while True:
        payload = {"query": "QA", "filter": {"property": "object", "value": "page"}, "page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        data = client.call("POST", "/search", payload)
        matches.extend(p for p in data.get("results", []) if page_title(p).strip().casefold() == "qa")
        if not data.get("has_more"):
            break
        next_cursor = data.get("next_cursor")
        if not next_cursor or next_cursor == cursor:
            raise APIError("QA page search pagination incomplete")
        cursor = next_cursor
    if len(matches) != 1:
        raise APIError("QA parent is missing or ambiguous; set DEPLOYMENT_NOTION_PAGE_ID in Actions variables")
    return matches[0]["id"]


def discover_channels(slack, days, fallback=""):
    # Search only channels the existing bot has already joined; never join new ones.
    try:
        channels = [c for c in slack.channels() if c.get("is_member") and not c.get("is_archived")]
    except APIError:
        if not fallback:
            raise APIError("Cannot list existing Slack channels; set DEPLOYMENT_SLACK_CHANNEL_IDS") from None
        channels = [{"id": x.strip()} for x in fallback.split(",") if x.strip()]
    if not channels:
        raise APIError("Existing bot has no joined channels; set DEPLOYMENT_SLACK_CHANNEL_IDS")
    now = time.time()
    selected = []
    for channel in channels:
        # Do not output channel names, message bodies, or titles in public workflow logs.
        try:
            messages = slack.history(channel["id"], now - days * 86400, now)
        except APIError:
            continue
        if candidates(messages):
            selected.append(channel["id"])
    if not selected:
        # Retain joined channels so future announcements can be found on the next scheduled run.
        selected = [c["id"] for c in channels]
    return selected


def run():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    settings = load_settings()
    start_date = os.getenv("BACKFILL_START_DATE", "")
    end_date = os.getenv("BACKFILL_END_DATE", "")
    requested = os.getenv("BACKFILL_REQUESTED", "").lower() == "true"
    period = DateRange(start_date=start_date, end_date=end_date).require_past(settings.timezone) if requested or start_date or end_date else None
    # Deployment-specific overrides never overwrite the incident workflow's configuration.
    settings.slack_channel_ids = os.getenv("DEPLOYMENT_SLACK_CHANNEL_IDS", "")
    parent = os.getenv("DEPLOYMENT_NOTION_PAGE_ID", "")
    settings.notion_page_id = notion_id(parent) if parent else ""
    for field in ("slack_bot_token", "notion_token", "gemini_api_key"):
        if not getattr(settings, field).get_secret_value():
            raise APIError("Existing Actions secret is missing: " + field.upper())
    secret = os.getenv("DEPLOYMENT_STATE_KEY") or settings.notion_token.get_secret_value()
    encrypted = os.getenv("ENCRYPTED_STATE_PATH", "cloud-state/deployment-state.enc")
    restore_state(encrypted, settings.database_path, secret)
    repo = Repository(settings.database_path)
    layout = None
    try:
        slack = SlackClient(settings)
        slack.test()
        notion = NotionClient(settings)
        if not settings.notion_page_id:
            settings.notion_page_id = repo.get_value("cloud_qa_page") or discover_qa(notion, os.getenv("INCIDENT_NOTION_DATABASE_ID", ""))
        notion.test()
        repo.set_value("cloud_qa_page", settings.notion_page_id)
        if not settings.channels:
            settings.slack_channel_ids = ",".join(discover_channels(slack, settings.lookback_days, os.getenv("INCIDENT_SLACK_CHANNEL_ID", "")))
        # Scheduled operation always syncs; local Preview preferences do not turn off cloud reporting.
        repo.set_value("runtime", {"slack_channel_ids": settings.slack_channel_ids, "auto_sync": True})
        settings.auto_sync = True
        reporter = Reporter(settings, repo=repo, slack=slack, notion=notion)
        history = Backfills(reporter)
        if period:
            if os.getenv("DEPLOYMENT_SLACK_CHANNEL_IDS"):
                historical_channels = settings.channels
            else:
                # Include joined channels with no recent notices; October-only channels must remain eligible.
                try:
                    historical_channels = [c["id"] for c in slack.channels() if c.get("is_member") and not c.get("is_archived")]
                except APIError:
                    historical_channels = settings.channels
            history.create(period.start_date, period.end_date, channels=historical_channels, sync=True)
        with repo.lock():
            layout = bootstrap(notion, repo, settings.notion_page_id)
        reporter.gemini.test()
        results = reporter.batch(sync=True)
        historical = history.run_pending(max_days=3, max_events=10, budget_seconds=600)
        if historical:
            print(f'Backfill: state={historical["state"]} processed={historical["processed"]} discovered={historical["discovered"]} next_date={historical["cursor"]}')
            failures_in_history = historical["last_result"].get("failed", 0)
            # Actual failures remain visible; quota deferral is a persisted waiting state.
            if failures_in_history:
                results.append({"error": "Historical collection failed; saved for automatic retry"})
        jobs = repo.backfills()
        if jobs:
            notion.current_sections(layout["lowdata"], {"과거 수집 진행": progress_text(jobs)})
        failures = sum("error" in r and not r.get("deferred") for r in results)
        deferred = sum(bool(r.get("deferred")) for r in results)
        print(f"Deployment sync: checked={len(results)} failed={failures} deferred_for_quota={deferred}")
        # Summary URLs are not emitted to public logs. Authenticated Notion is the output surface.
        repo.set_value("cloud_last_result", {"checked": len(results), "failed": failures, "deferred": deferred, "report_root": layout["root"]})
        return 1 if failures else 0
    finally:
        try:
            if layout:
                with repo.lock():
                    StatisticsSync(notion, repo, settings).refresh(layout)
        finally:
            save_state(settings.database_path, encrypted, secret)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception as exc:
        # No traceback/provider response/Slack content in public Actions logs.
        print("::error::" + (str(exc) if isinstance(exc, APIError) else type(exc).__name__ + ": deployment sync failed; inspect authenticated integrations"))
        sys.exit(1)
