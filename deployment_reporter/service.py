import logging
import time
from zoneinfo import ZoneInfo

from .domain.models import SlackMessage, DeploymentReport
from .slack.client import SlackClient
from .slack.collector import Collector
from .slack.detector import candidates
from .llm.gemini import GeminiClient
from .notion.client import NotionClient
from .notion.sync import NotionSync
from .notion.schema import summary_properties
from .storage.repository import Repository, evidence_digest

LOG = logging.getLogger(__name__)
RUNTIME_FIELDS = {"slack_channel_ids", "window_hours", "lookback_days", "poll_interval_seconds", "auto_sync"}


class Reporter:
    def __init__(self, settings, repo=None, slack=None, gemini=None, notion=None):
        self.settings = settings
        self.repo = repo or Repository(settings.database_path)
        runtime = self.repo.get_value("runtime", {})
        self.apply_settings(runtime, persist=False)
        self.slack = slack or SlackClient(self.settings)
        self.gemini = gemini or GeminiClient(self.settings)
        self.notion = notion or NotionClient(self.settings)

    def apply_settings(self, changes, persist=True):
        if not changes.keys() <= RUNTIME_FIELDS:
            raise ValueError("Only channel/window/lookback/interval/auto-sync can change here; credentials belong in .env")
        updated = self.settings.model_validate({**self.settings.model_dump(), **changes})
        if persist:
            with self.repo.lock():
                self.repo.set_value("runtime", {k: getattr(updated, k) for k in RUNTIME_FIELDS})
                for k in changes:
                    setattr(self.settings, k, getattr(updated, k))
        else:
            for k in changes:
                setattr(self.settings, k, getattr(updated, k))
        return self.settings.public()

    def _workspace(self):
        auth = self.slack.test()
        workspace = auth["team_id"]
        saved = self.repo.get_value("workspace")
        if saved and saved != workspace:
            raise ValueError("This SQLite database belongs to another Slack workspace; use a separate DATABASE_PATH")
        self.repo.set_value("workspace", workspace)
        return workspace

    def _fail(self, event_id, exc):
        # Provider error bodies and original Slack text never enter operational logs.
        from .http import APIError, RateLimitError
        message = str(exc) if isinstance(exc, APIError) else type(exc).__name__ + ": operation failed; check configuration and retry"
        event = self.repo.event(event_id)
        deferred = isinstance(exc, RateLimitError)
        self.repo.update(event_id, state="Deferred" if deferred else "Failed", sync_state="Pending" if deferred else "Failed", error=message, retries=event["retries"] + 1)
        self.repo.log("Error", message, event_id)
        LOG.error("event=%s %s", event_id, message)

    def _analyze(self, event_id, evidence, force=False):
        fingerprint = evidence_digest(evidence)
        event = self.repo.event(event_id)
        if force or event["analyzed_digest"] != fingerprint:
            from .http import RateLimitError
            cooldown_key = "gemini_cooldown:" + self.gemini.model
            until = self.repo.get_value(cooldown_key, 0)
            if until > time.time():
                raise RateLimitError("Gemini quota cooldown; retained for automatic retry", until - time.time())
            self.repo.update(event_id, state="Analyzing")
            self.repo.log("Gemini", "Structured analysis requested", event_id)
            try:
                report, response = self.gemini.analyze(evidence)
            except RateLimitError as exc:
                self.repo.set_value(cooldown_key, time.time() + exc.retry_after)
                raise
            self.repo.add_analysis(event_id, report, response, self.gemini.model, self.gemini.prompt_version, fingerprint)
            self.repo.log("Validation", "Schema validated; analysis revision saved", event_id)
        elif event["state"] == "Failed":
            previous = DeploymentReport.model_validate_json(self.repo.analyses(event_id)[0]["report"])
            self.repo.update(event_id, state="Analyzed" if previous.is_deployment else "Rejected", error=None)

    def _sync(self, event_id):
        event = self.repo.event(event_id)
        analyses = self.repo.analyses(event_id)
        # Raw-only changes can require sync even if LLM input didn't change.
        if event["sync_state"] != "Synced" or (analyses and event["notion_revision"] != analyses[0]["id"]):
            return NotionSync(self.notion, self.repo, self.settings).sync(event_id)

    def batch(self, *, days=None, analyze=True, sync=None, clock=None):
        self.apply_settings(self.repo.get_value("runtime", {}), persist=False)
        current = time.time() if clock is None else clock
        sync = self.settings.auto_sync if sync is None else sync
        results = []
        with self.repo.lock():
            workspace = self._workspace()
            channels = self.settings.channels
            if not channels:
                raise ValueError("Select at least one Slack channel")
            for channel in channels:
                known = [e for e in self.repo.events() if e["channel"] == channel and e["workspace"] == workspace]
                # Revisit active events even when batch lookback is smaller than the tracking window.
                active = [e for e in known if float(e["ts"]) + self.settings.window_hours * 3600 >= current or e["state"] in {"Failed", "Deferred", "Detected", "Analyzing"} or (e["stop_reason"] and not e["stop_reason"].startswith("window closed"))]
                oldest = min([current - (days or self.settings.lookback_days) * 86400] + [float(e["ts"]) for e in active])
                try:
                    messages = self.slack.history(channel, oldest, current)
                except Exception as exc:
                    self.repo.log("Slack", "Channel collection failed; no events marked complete")
                    results.append({"channel": channel, "error": type(exc).__name__})
                    continue
                event_ids = {e["id"] for e in active}
                for root in candidates(messages):
                    old = next((e for e in known if e["ts"] == root.ts), None)
                    edited = old and SlackMessage.model_validate_json(old["root"]).text != root.text
                    if old is None or old["id"] in event_ids or edited:
                        event_ids.add(self.repo.detect(root, workspace, ZoneInfo(self.settings.timezone)))
                self.repo.log("Slack", f"Detected/tracked {len(event_ids)} candidate events")
                LOG.info("[Slack] detected/tracked=%d", len(event_ids))
                for event_id in sorted(event_ids):
                    try:
                        event = self.repo.event(event_id)
                        root = SlackMessage.model_validate_json(event["root"])
                        if not self.repo.evidence(event_id):
                            from .domain.relevance import score
                            self.repo.save_evidence(event_id, [score(root, root, self.settings.aliases)])
                        evidence, stop = Collector(self.slack, self.settings).collect(root, messages, current)
                        previous_raw = len(self.repo.raw(event_id))
                        self.repo.save_evidence(event_id, evidence)
                        self.repo.update(event_id, stop_reason=stop)
                        if len(self.repo.raw(event_id)) != previous_raw:
                            self.repo.update(event_id, sync_state="Pending")
                        self.repo.log("Collector", f"Collected {len(evidence)} messages; relevance scored", event_id)
                        if analyze:
                            self._analyze(event_id, evidence)
                            if sync:
                                self._sync(event_id)
                        elif sync:
                            raise ValueError("Cannot sync an unanalysed search")
                        self.repo.log("Complete", "Event processing complete", event_id)
                        LOG.info("[Complete] event=%s", event_id)
                        results.append({"id": event_id, "state": self.repo.event(event_id)["state"]})
                    except Exception as exc:
                        self._fail(event_id, exc)
                        from .http import RateLimitError
                        results.append({"id": event_id, "error": self.repo.event(event_id)["error"], "deferred": isinstance(exc, RateLimitError)})
        return results

    def analyze(self, event_id, recollect=False):
        with self.repo.lock():
            try:
                event = self.repo.event(event_id)
                if recollect:
                    if self._workspace() != event["workspace"]:
                        raise ValueError("Workspace mismatch")
                    root = SlackMessage.model_validate_json(event["root"])
                    current = time.time()
                    messages = self.slack.history(root.channel, float(root.ts), min(current, float(root.ts) + self.settings.window_hours * 3600))
                    evidence, stop = Collector(self.slack, self.settings).collect(root, messages, current)
                    self.repo.save_evidence(event_id, evidence)
                    self.repo.update(event_id, stop_reason=stop)
                evidence = self.repo.evidence(event_id)
                if not evidence:
                    raise ValueError("Collect raw messages first")
                self._analyze(event_id, evidence, force=True)
                return self.preview(event_id)
            except Exception as exc:
                self._fail(event_id, exc)
                raise

    def sync(self, event_id):
        with self.repo.lock():
            try:
                # Explicit sync always retries remote reconciliation.
                return NotionSync(self.notion, self.repo, self.settings).sync(event_id)
            except Exception as exc:
                self._fail(event_id, exc)
                raise

    def preview(self, event_id):
        event = self.repo.event(event_id)
        evidence = self.repo.evidence(event_id)
        analyses = self.repo.analyses(event_id)
        report = DeploymentReport.model_validate_json(analyses[0]["report"]) if analyses else None
        return {"event": event, "evidence": [e.model_dump(mode="json") for e in evidence],
                "report": report.model_dump(mode="json") if report else None,
                "review_status": report.review_status if report else None,
                "notion_properties": summary_properties(event, report, evidence, self.settings.timezone) if report and evidence else None,
                "will_register": bool(report and (report.is_deployment or event["notion_page_id"])),
                "raw_revisions": self.repo.raw(event_id), "analysis_revisions": analyses}
