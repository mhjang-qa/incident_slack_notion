"""Slack Web API adapter with pagination and rate-limit handling."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .models import Incident, SlackMessage

LOGGER = logging.getLogger(__name__)


class SlackIncidentClient:
    def __init__(self, token: str, channel_id: str, timezone) -> None:
        self.client = WebClient(token=token)
        self.channel_id = channel_id
        self.timezone = timezone
        self._user_cache: dict[str, str] = {}
        self._channel_cache: dict[str, str] = {}

    def validate_bot_identity(self, expected_name: str) -> str:
        """Log the installed bot identity without overriding message authorship."""
        auth = self._call(self.client.auth_test)
        user_id = str(auth.get("user_id") or "")
        if not user_id:
            raise RuntimeError(
                "SLACK_BOT_TOKEN에서 Bot User ID를 확인할 수 없습니다. "
                "Incoming Webhook이나 App Token이 아닌 xoxb Bot User OAuth Token을 사용하세요."
            )

        user = self._call(self.client.users_info, user=user_id).get("user", {})
        profile = user.get("profile", {})
        profile_name = str(
            profile.get("display_name")
            or profile.get("real_name")
            or user.get("real_name")
            or user.get("name")
            or auth.get("user")
            or ""
        ).strip()
        bot_id = str(auth.get("bot_id") or profile.get("bot_id") or "")
        bot_info: dict[str, Any] = {}
        if bot_id:
            try:
                bot_info = self._call(self.client.bots_info, bot=bot_id).get("bot", {})
            except SlackApiError:
                LOGGER.warning("Slack bots.info 조회 실패: bot_id=%s", bot_id)
        bot_name = str(bot_info.get("name") or "").strip()
        actual_name = bot_name or profile_name
        LOGGER.info(
            "Slack Bot identity: app_id=%s, bot_id=%s, user_id=%s, "
            "bot_name=%s, profile_name=%s",
            bot_info.get("app_id") or profile.get("api_app_id") or "",
            bot_id,
            user_id,
            bot_name,
            profile_name,
        )

        if expected_name and actual_name.casefold() != expected_name.casefold():
            # App Home's display name and the installed bot user's profile can
            # briefly disagree. chat.postMessage remains username-free so Slack
            # can apply the app identity configured on its side.
            LOGGER.warning(
                "Slack Bot 표시 이름 불일치: 현재 '%s', 예상 '%s'. "
                "전송은 계속하며 Slack 응답의 최종 username을 기록합니다.",
                actual_name,
                expected_name,
            )
        return actual_name

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
        try:
            response = self._call(
                self.client.chat_postMessage,
                channel=channel_id,
                text=(
                    f":white_check_mark: 장애 모니터링 정상 확인\n"
                    f"- 확인 시각: {checked_at_text}\n"
                    f"- Slack 조회 범위: 최근 {lookback_hours}시간\n"
                    f"- 이번 실행 신규 등록/상태 업데이트: 없음"
                ),
            )
            message = response.get("message", {})
            LOGGER.info(
                "Slack message posted: channel=%s, ts=%s, username=%s, bot_id=%s",
                response.get("channel") or channel_id,
                response.get("ts") or message.get("ts"),
                message.get("username") or "",
                message.get("bot_id") or "",
            )
        except SlackApiError as exc:
            error = exc.response.get("error", "unknown_error")
            guidance = {
                "missing_scope": "Slack App에 chat:write 권한을 추가하고 앱을 재설치하세요.",
                "not_in_channel": "Slack App을 알림 채널에 초대하세요.",
                "channel_not_found": "SLACK_NOTIFICATION_CHANNEL에 정확한 채널 ID를 설정하세요.",
                "invalid_auth": "SLACK_BOT_TOKEN을 다시 확인하세요.",
            }.get(error, "Slack App 권한과 채널 설정을 확인하세요.")
            raise RuntimeError(f"Slack 알림 전송 실패({error}). {guidance}") from exc
        LOGGER.info("장애 없음 알림 전송: %s", target)

    def post_incident_created_notification(
        self,
        target: str,
        incident: Incident,
        notion_url: str,
    ) -> None:
        """Notify after the Notion incident report page has been created."""
        if not target:
            return
        channel_id = self._resolve_channel(target)
        occurred_at = (
            incident.occurred_at.strftime("%Y-%m-%d %H:%M:%S %Z")
            if incident.occurred_at
            else "확인 중"
        )
        notion_line = f"- Notion: {notion_url}" if notion_url else "- Notion: 생성 완료"
        try:
            response = self._call(
                self.client.chat_postMessage,
                channel=channel_id,
                text=(
                    f":rotating_light: 장애 보고서 생성 완료\n"
                    f"- 제목: {incident.title}\n"
                    f"- 상태: {incident.status or '모니터링 중'}\n"
                    f"- 발생 일시: {occurred_at}\n"
                    f"{notion_line}\n"
                    f"- Slack 원문: {incident.slack_link or '링크 없음'}"
                ),
            )
            message = response.get("message", {})
            LOGGER.info(
                "Slack incident notification posted: channel=%s, ts=%s, username=%s, bot_id=%s",
                response.get("channel") or channel_id,
                response.get("ts") or message.get("ts"),
                message.get("username") or "",
                message.get("bot_id") or "",
            )
        except SlackApiError as exc:
            error = exc.response.get("error", "unknown_error")
            guidance = {
                "missing_scope": "Slack App에 chat:write 권한을 추가하고 앱을 재설치하세요.",
                "not_in_channel": "Slack App을 알림 채널에 초대하세요.",
                "channel_not_found": "SLACK_NOTIFICATION_CHANNEL에 정확한 채널 ID를 설정하세요.",
                "invalid_auth": "SLACK_BOT_TOKEN을 다시 확인하세요.",
            }.get(error, "Slack App 권한과 채널 설정을 확인하세요.")
            raise RuntimeError(f"Slack 장애 보고서 알림 전송 실패({error}). {guidance}") from exc
        LOGGER.info("장애 보고서 생성 알림 전송: %s", target)

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
            try:
                response = self._call(
                    self.client.conversations_list,
                    types="public_channel,private_channel",
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor,
                )
            except SlackApiError as exc:
                error = exc.response.get("error", "unknown_error")
                raise RuntimeError(
                    f"Slack 알림 채널 검색 실패({error}). 채널명을 검색하려면 "
                    "channels:read 권한이 필요하며, 비공개 채널은 groups:read 권한과 "
                    "앱 초대가 필요합니다. 가능하면 채널 ID를 사용하세요."
                ) from exc
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
            "채널 ID를 SLACK_NOTIFICATION_CHANNEL에 설정하고 앱을 채널에 초대하세요."
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
