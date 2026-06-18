"""Slack Web API adapter with pagination and rate-limit handling."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .models import SlackMessage

LOGGER = logging.getLogger(__name__)


class SlackIncidentClient:
    def __init__(self, token: str, channel_id: str, timezone) -> None:
        self.client = WebClient(token=token)
        self.channel_id = channel_id
        self.timezone = timezone
        self._user_cache: dict[str, str] = {}
        self._channel_cache: dict[str, str] = {}

    def fetch_channel_messages(self, oldest: float) -> list[SlackMessage]:
        raw_messages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            response = self._call(
                self.client.conversations_history,
                channel=self.channel_id,
                oldest=str(oldest),
                limit=200,
                cursor=cursor,
                inclusive=True,
            )
            raw_messages.extend(response.get("messages", []))
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        return [self._convert(item) for item in reversed(raw_messages) if item.get("subtype") is None]

    def fetch_thread(self, thread_ts: str) -> list[SlackMessage]:
        raw_messages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            response = self._call(
                self.client.conversations_replies,
                channel=self.channel_id,
                ts=thread_ts,
                limit=200,
                cursor=cursor,
                inclusive=True,
            )
            raw_messages.extend(response.get("messages", []))
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        return [self._convert(item) for item in raw_messages if item.get("subtype") is None]

    def post_no_incident_notification(
        self,
        target: str,
        checked_at: datetime,
        lookback_hours: int,
    ) -> None:
        """Post a heartbeat only after a successful cycle with no new incidents."""
        if not target:
            return
        channel_id = self._resolve_channel(target)
        checked_at_text = checked_at.strftime("%Y-%m-%d %H:%M:%S %Z")
        self._call(
            self.client.chat_postMessage,
            channel=channel_id,
            text=(
                f":white_check_mark: 장애 모니터링 정상 확인\n"
                f"- 확인 시각: {checked_at_text}\n"
                f"- 최근 {lookback_hours}시간 내 신규 장애: 없음"
            ),
        )
        LOGGER.info("장애 없음 알림 전송: %s", target)

    def _convert(self, item: dict[str, Any]) -> SlackMessage:
        ts = str(item["ts"])
        thread_ts = str(item.get("thread_ts") or ts)
        user_id = str(item.get("user") or item.get("bot_id") or "unknown")
        return SlackMessage(
            ts=ts,
            thread_ts=thread_ts,
            user_id=user_id,
            author=self._resolve_user(user_id),
            text=str(item.get("text", "")),
            permalink=self._permalink(ts),
            posted_at=datetime.fromtimestamp(float(ts), tz=self.timezone),
            is_thread_reply=thread_ts != ts,
        )

    def _resolve_user(self, user_id: str) -> str:
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            response = self._call(self.client.users_info, user=user_id)
            profile = response.get("user", {}).get("profile", {})
            name = profile.get("display_name") or profile.get("real_name") or user_id
        except SlackApiError:
            name = user_id
        self._user_cache[user_id] = name
        return name

    def _permalink(self, message_ts: str) -> str:
        try:
            response = self._call(
                self.client.chat_getPermalink,
                channel=self.channel_id,
                message_ts=message_ts,
            )
            return str(response.get("permalink", ""))
        except SlackApiError:
            return ""

    def _resolve_channel(self, target: str) -> str:
        normalized = target.lstrip("#")
        if normalized.startswith(("C", "G", "D")):
            return normalized
        if normalized in self._channel_cache:
            return self._channel_cache[normalized]

        cursor: str | None = None
        while True:
            response = self._call(
                self.client.conversations_list,
                types="public_channel,private_channel",
                exclude_archived=True,
                limit=200,
                cursor=cursor,
            )
            for channel in response.get("channels", []):
                if channel.get("name") == normalized:
                    channel_id = str(channel["id"])
                    self._channel_cache[normalized] = channel_id
                    return channel_id
            cursor = response.get("response_metadata", {}).get("next_cursor") or None
            if not cursor:
                break
        raise RuntimeError(
            f"Slack 알림 채널을 찾을 수 없습니다: {target}. "
            "채널 ID를 SLACK_NOTIFICATION_CHANNEL에 설정하거나 앱을 채널에 초대하세요."
        )

    @staticmethod
    def _call(method, **kwargs):
        for attempt in range(4):
            try:
                return method(**kwargs)
            except SlackApiError as exc:
                if exc.response.status_code != 429 or attempt == 3:
                    raise
                retry_after = int(exc.response.headers.get("Retry-After", "1"))
                LOGGER.warning("Slack rate limit: %s초 후 재시도", retry_after)
                time.sleep(retry_after)
        raise RuntimeError("Slack API 재시도 횟수를 초과했습니다.")
