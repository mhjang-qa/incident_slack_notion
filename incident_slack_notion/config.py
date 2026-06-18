"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    slack_bot_token: str
    slack_channel_id: str
    notion_token: str
    notion_database_id: str
    poll_interval_seconds: int = 60
    timezone: str = "Asia/Seoul"
    database_path: str = "incident_mapping.db"
    slack_lookback_hours: int = 72
    slack_notification_channel: str = "slice_gh-test"
    log_level: str = "INFO"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def load_settings(env_file: str | Path | None = None) -> Settings:
    load_dotenv(dotenv_path=env_file)
    required = {
        "SLACK_BOT_TOKEN": os.getenv("SLACK_BOT_TOKEN"),
        "SLACK_CHANNEL_ID": os.getenv("SLACK_CHANNEL_ID"),
        "NOTION_TOKEN": os.getenv("NOTION_TOKEN"),
        "NOTION_DATABASE_ID": os.getenv("NOTION_DATABASE_ID"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"필수 환경변수가 없습니다: {', '.join(missing)}")

    interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
    if interval < 10:
        raise RuntimeError("POLL_INTERVAL_SECONDS는 10 이상이어야 합니다.")

    return Settings(
        slack_bot_token=required["SLACK_BOT_TOKEN"] or "",
        slack_channel_id=required["SLACK_CHANNEL_ID"] or "",
        notion_token=required["NOTION_TOKEN"] or "",
        notion_database_id=required["NOTION_DATABASE_ID"] or "",
        poll_interval_seconds=interval,
        timezone=os.getenv("TIMEZONE", "Asia/Seoul"),
        database_path=os.getenv("DATABASE_PATH", "incident_mapping.db"),
        slack_lookback_hours=int(os.getenv("SLACK_LOOKBACK_HOURS", "72")),
        slack_notification_channel=os.getenv(
            "SLACK_NOTIFICATION_CHANNEL", "slice_gh-test"
        ).strip(),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
    )
