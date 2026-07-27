"""Polling orchestration for Slack collection and Notion synchronization."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .config import Settings
from .parser import apply_thread, is_incident_candidate, is_recovery_message, parse_incident
from .storage import Storage

if TYPE_CHECKING:
    from .notion_client import NotionIncidentClient
    from .slack_client import SlackIncidentClient
    from .summary_client import GeminiIncidentSummarizer

LOGGER = logging.getLogger(__name__)


class IncidentSynchronizer:
    def __init__(
        self,
        settings: Settings,
        slack: SlackIncidentClient,
        notion: NotionIncidentClient,
        storage: Storage,
        summarizer: GeminiIncidentSummarizer | None = None,
    ) -> None:
        self.settings = settings
        self.slack = slack
        self.notion = notion
        self.storage = storage
        self.summarizer = summarizer

    def run_once(self) -> None:
        """Run one idempotent collection/update cycle."""
        try:
            changed_count = self._collect_new_incidents()
        except Exception:
            LOGGER.exception("Slack 신규 메시지 수집 실패")
            # A failed collection must never be reported as "no incidents."
            raise

        # Track all known threads independently, so a late recovery update is
        # still processed even when channel history lookback no longer includes it.
        refreshed_count = 0
        for mapping in self.storage.tracked_incidents():
            try:
                if self._refresh_thread(
                    mapping.slack_ts, mapping.thread_ts, mapping.notion_page_id
                ):
                    refreshed_count += 1
            except Exception:
                LOGGER.exception("스레드 조회/업데이트 실패: %s", mapping.thread_ts)

        if (
            changed_count == 0
            and refreshed_count == 0
            and self.settings.slack_notification_channel
            and self.settings.post_no_incident_heartbeat
        ):
            self.slack.post_no_incident_notification(
                self.settings.slack_notification_channel,
                datetime.now(tz=self.settings.tz),
                self.settings.slack_lookback_hours,
            )

    def _collect_new_incidents(self) -> int:
        oldest = (
            datetime.now(tz=self.settings.tz)
            - timedelta(hours=self.settings.slack_lookback_hours)
        ).timestamp()
        messages = self.slack.fetch_channel_messages(oldest)
        LOGGER.info("Slack 신규 메시지 수집: %s건", len(messages))
        changed_count = 0

        for message in messages:
            if message.is_thread_reply:
                continue
            if is_recovery_message(message.text) and not is_incident_candidate(message.text):
                if self._apply_standalone_recovery(message):
                    changed_count += 1
                continue
            if not is_incident_candidate(message.text):
                continue
            existing = self.storage.get(message.ts, message.thread_ts)
            if existing:
                continue

            incident = parse_incident(message)
            thread = self.slack.fetch_thread(message.thread_ts)
            apply_thread(incident, thread)
            self._summarize(incident)
            last_ts = thread[-1].ts if thread else message.ts

            existing_page = self.notion.find_existing_incident(incident)
            if existing_page:
                # The report body is already present. Do not append another
                # update section merely because a later scheduled run found
                # a manually created or previously synced page.
                self.notion.update_incident(
                    existing_page.id,
                    incident,
                    append_thread_update=bool(incident.recovery_details),
                )
                self.storage.upsert(message.ts, message.thread_ts, existing_page.id, last_ts)
                LOGGER.info("기존 Notion 장애 보고서 업데이트: %s", incident.title)
                changed_count += 1
                continue

            page = self.notion.create_incident(incident)
            self.storage.upsert(message.ts, message.thread_ts, page.id, last_ts)
            changed_count += 1
            if self.settings.slack_notification_channel:
                self.slack.post_incident_created_notification(
                    self.settings.slack_notification_channel,
                    incident,
                    page.url,
                )
            LOGGER.info("신규 장애 등록: %s", incident.title)
        return changed_count

    def _apply_standalone_recovery(self, message) -> bool:
        incident = parse_incident(message)
        apply_thread(incident, [message])
        existing_page = self.notion.find_recoverable_incident(incident)
        if not existing_page:
            LOGGER.info("정상화 공지 대상 장애를 찾지 못해 건너뜀: %s", incident.title)
            return False
        if existing_page.title:
            incident.title = existing_page.title
        self._summarize(incident)
        self.notion.update_incident(existing_page.id, incident, append_thread_update=True)
        self.storage.upsert(message.ts, message.thread_ts, existing_page.id, message.ts)
        LOGGER.info("정상화 단독 공지 반영: %s", incident.title)
        return True

    def _refresh_thread(self, slack_ts: str, thread_ts: str, page_id: str) -> bool:
        thread = self.slack.fetch_thread(thread_ts)
        if not thread:
            return False
        mapping = self.storage.get(slack_ts, thread_ts)
        last_ts = thread[-1].ts

        root = thread[0]
        incident = parse_incident(root)
        previous_status = incident.status
        apply_thread(incident, thread)
        self._summarize(incident)
        body_backfilled = self.notion.ensure_report_body(page_id, incident)
        if body_backfilled and self.settings.slack_notification_channel:
            self.slack.post_incident_created_notification(
                self.settings.slack_notification_channel,
                incident,
                self.notion.page_url(page_id),
            )
        if mapping and mapping.last_thread_ts == last_ts:
            return body_backfilled
        self.notion.update_incident(
            page_id,
            incident,
            append_thread_update=bool(incident.recovery_details),
        )
        self.storage.upsert(slack_ts, thread_ts, page_id, last_ts)
        if previous_status != incident.status and incident.status == "정상화":
            LOGGER.info("정상화 감지: %s", incident.title)
        else:
            LOGGER.info("기존 장애 업데이트: %s", incident.title)
        return True

    def _summarize(self, incident) -> None:
        if not self.summarizer:
            return
        incident.llm_summary = self.summarizer.summarize(incident)


def run_scheduler(settings: Settings, synchronizer: IncidentSynchronizer) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler(timezone=settings.timezone)
    scheduler.add_job(
        synchronizer.run_once,
        trigger=IntervalTrigger(seconds=settings.poll_interval_seconds),
        id="slack-notion-incident-sync",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=max(30, settings.poll_interval_seconds),
        next_run_time=datetime.now(tz=settings.tz),
    )
    LOGGER.info("%s초 간격으로 스케줄러 시작", settings.poll_interval_seconds)
    scheduler.start()
