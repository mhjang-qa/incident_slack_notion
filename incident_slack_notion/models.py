"""Shared domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class SlackMessage:
    ts: str
    thread_ts: str
    user_id: str
    author: str
    text: str
    permalink: str
    posted_at: datetime
    is_thread_reply: bool = False


@dataclass(slots=True)
class Incident:
    title: str
    occurred_at: datetime | None
    recovered_at: datetime | None = None
    service: str = ""
    status: str = "모니터링 중"
    impact: str = ""
    details: str = ""
    reporter: str = ""
    slack_link: str = ""
    raw_message: str = ""
    thread_summary: str = ""
    duration_minutes: int | None = None
    duration_text: str = ""
    recovery_details: str = ""
    source_ts: str = ""
    thread_ts: str = ""
    thread_messages: list[SlackMessage] = field(default_factory=list)

