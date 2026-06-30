"""Manual repair command for a specific Notion incident report page."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime

from .config import load_settings
from .logger import configure_logging
from .models import Incident
from .notion_client import NotionIncidentClient, normalize_page_id
from .slack_client import SlackIncidentClient

LOGGER = logging.getLogger(__name__)


DEFAULT_TITLE = "[오픈뱅킹] 부산은행 불안정"
DEFAULT_STARTED_AT = "2026-06-30 13:41:12"
DEFAULT_BODY = (
    "2026-06-30 13:41:12부터 오픈뱅킹 부산은행에서 간헐적으로 "
    "타임아웃이 발생중(15건)에 있습니다.\nCS 대응시 참고 바랍니다."
)


def main() -> None:
    parser = argparse.ArgumentParser(description="특정 Notion 장애 페이지 본문 보정")
    parser.add_argument("--page-id", required=True, help="Notion page ID 또는 page URL")
    parser.add_argument("--title", default=DEFAULT_TITLE)
    parser.add_argument("--started-at", default=DEFAULT_STARTED_AT)
    parser.add_argument("--body", default=DEFAULT_BODY)
    parser.add_argument("--notify", action="store_true", help="Slack 완료 메시지 전송")
    args = parser.parse_args()

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        page_id = normalize_page_id(args.page_id)
        occurred_at = datetime.strptime(args.started_at, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=settings.tz
        )
        raw_message = f"{args.title}\n- {args.body}"
        incident = Incident(
            title=args.title,
            occurred_at=occurred_at,
            service="오픈뱅킹",
            status="모니터링 중",
            impact="영향도 확인 중",
            details=args.body,
            reporter="강훈주 (Tony Kang)",
            raw_message=raw_message,
            source_ts="manual-repair",
            thread_ts="manual-repair",
        )

        notion = NotionIncidentClient(settings.notion_token, settings.notion_database_id)
        notion.update_incident(page_id, incident)
        notion.append_report_body(page_id, incident)
        page_url = notion.page_url(page_id)
        LOGGER.info("Notion 장애 보고서 수동 보정 완료: %s", page_url)

        if args.notify and settings.slack_notification_channel:
            slack = SlackIncidentClient(
                settings.slack_bot_token, settings.slack_channel_id, settings.tz
            )
            slack.validate_bot_identity(settings.slack_expected_bot_name)
            slack.post_incident_created_notification(
                settings.slack_notification_channel,
                incident,
                page_url,
            )
    except Exception as exc:
        LOGGER.exception("Notion 페이지 수동 보정 실패")
        if os.getenv("GITHUB_ACTIONS") == "true":
            message = str(exc).replace("\r", " ").replace("\n", " ")
            print(f"::error title=Incident page repair failed::{type(exc).__name__}: {message}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
