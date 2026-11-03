from pathlib import Path
import json
import os
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator


def notion_id(value: str) -> str:
    path = urlparse(value).path if "://" in value else value
    match = re.search(r"([a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})$", path.rstrip("/"))
    if not match:
        raise ValueError("NOTION_PAGE_ID must be a page UUID or page URL")
    return match[1]


class Settings(BaseModel):
    slack_bot_token: SecretStr = SecretStr("")
    slack_thread_token: SecretStr = SecretStr("")
    slack_workspace: str = ""
    slack_channel_ids: str = ""
    notion_token: SecretStr = SecretStr("")
    notion_page_id: str = ""
    notion_version: str = "2025-09-03"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.5-flash"
    timezone: str = "Asia/Seoul"
    database_path: str = "data/deployment_reporter.db"
    window_hours: int = Field(48, ge=1, le=168)
    lookback_days: int = Field(7, ge=1, le=90)
    poll_interval_seconds: int = Field(300, ge=60, le=86400)
    auto_sync: bool = False
    max_retries: int = Field(3, ge=1, le=5)
    service_aliases_path: str = "config/service_aliases.json"

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator("notion_page_id")
    @classmethod
    def valid_page(cls, value):
        return notion_id(value) if value else ""

    @property
    def channels(self):
        return list(dict.fromkeys(x.strip() for x in self.slack_channel_ids.split(",") if x.strip()))

    @property
    def aliases(self):
        path = Path(self.service_aliases_path)
        return json.loads(path.read_text()) if path.exists() else {}

    def public(self):
        return {k: v for k, v in self.model_dump(mode="json").items() if not any(s in k for s in ("token", "api_key"))}


def load_settings():
    load_dotenv()
    return Settings(**{k: os.environ[k.upper()] for k in Settings.model_fields if k.upper() in os.environ})
