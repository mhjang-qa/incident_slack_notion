"""Application entry point."""

from __future__ import annotations

import argparse
import logging

from .config import load_settings
from .logger import configure_logging
from .notion_client import NotionIncidentClient
from .scheduler import IncidentSynchronizer, run_scheduler
from .slack_client import SlackIncidentClient
from .storage import Storage

LOGGER = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Slack → Notion 장애 리포트 자동화")
    parser.add_argument("--once", action="store_true", help="한 번만 동기화하고 종료")
    args = parser.parse_args()

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        storage = Storage(settings.database_path)
        slack = SlackIncidentClient(
            settings.slack_bot_token, settings.slack_channel_id, settings.tz
        )
        notion = NotionIncidentClient(
            settings.notion_token, settings.notion_database_id
        )
        synchronizer = IncidentSynchronizer(settings, slack, notion, storage)
        if args.once:
            synchronizer.run_once()
        else:
            run_scheduler(settings, synchronizer)
    except KeyboardInterrupt:
        LOGGER.info("사용자 요청으로 종료")
    except Exception:
        LOGGER.exception("애플리케이션 시작 실패")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

