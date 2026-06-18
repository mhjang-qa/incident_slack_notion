"""Polling orchestration for Slack collection and Notion synchronization."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import Settings
from .notion_client import NotionIncidentClient
from .parser import apply_thread, is_incident_candidate, parse_incident
from .slack_client import SlackIncidentClient
from .storage import Storage

LOGGER = logging.getLogger(__name__)


class IncidentSynchronizer:
    def __init__(
        self,
        settings: Settings,
        slack: SlackIncidentClient,
        notion: NotionIncidentClient,
        storage: Storage,
    ) -> None:
        self.settings = settings
        self.slack = slack
        self.notion = notion
        self.storage = storage

    def run_once(self) -> None:
        """Run one idempotent collection/update cycle."""
        try:
            self._collect_new_incidents()
        except Exception:
            LOGGER.exception("Slack 신규 메시지 수집 실패")

        # Track all known threads independently, so a late recovery update is
        # still processed even when channel history lookback no longer includes it.
        for mapping in self.storage.tracked_incidents():
            try:
                self._refresh_thread(mapping.slack_ts, mapping.thread_ts, mapping.notion_page_id)
            except Exception:
                LOGGER.exception("스레드 조회/업데이트 실패: %s", mapping.thread_ts)

    def _collect_new_incidents(self) -> None:
        oldest = (
            datetime.now(tz=self.settings.tz)
            - timedelta(hours=self.settings.slack_lookback_hours)
        ).timestamp()
        messages = self.slack.fetch_channel_messages(oldest)
        LOGGER.info("Slack 신규 메시지 수집: %s건", len(messages))

        for message in messages:
            if message.is_thread_reply or not is_incident_candidate(message.text):
                continue
            existing = self.storage.get(message.ts, message.thread_ts)
            if existing:
                continue

            incident = parse_incident(message)
            thread = self.slack.fetch_thread(message.thread_ts)
            apply_thread(incident, thread)
            page_id = self.notion.create_incident(incident)
            last_ts = thread[-1].ts if thread else message.ts
            self.storage.upsert(message.ts, message.thread_ts, page_id, last_ts)
            LOGGER.info("신규 장애 등록: %s", incident.title)

    def _refresh_thread(self, slack_ts: str, thread_ts: str, page_id: str) -> None:
        thread = self.slack.fetch_thread(thread_ts)
        if not thread:
            return
        mapping = self.storage.get(slack_ts, thread_ts)
        last_ts = thread[-1].ts
        if mapping and mapping.last_thread_ts == last_ts:
            return

        root = thread[0]
        incident = parse_incident(root)
        previous_status = incident.status
        apply_thread(incident, thread)
        self.notion.update_incident(page_id, incident)
        self.storage.upsert(slack_ts, thread_ts, page_id, last_ts)
        if previous_status != incident.status and incident.status == "정상화":
            LOGGER.info("정상화 감지: %s", incident.title)
        else:
            LOGGER.info("기존 장애 업데이트: %s", incident.title)


def run_scheduler(settings: Settings, synchronizer: IncidentSynchronizer) -> None:
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

