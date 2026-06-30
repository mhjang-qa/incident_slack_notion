import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from incident_slack_notion.config import Settings
from incident_slack_notion.scheduler import IncidentSynchronizer
from incident_slack_notion.storage import Storage


class SchedulerTest(unittest.TestCase):
    def settings(self, database_path: str) -> Settings:
        return Settings(
            slack_bot_token="test",
            slack_channel_id="C123",
            notion_token="test",
            notion_database_id="db",
            database_path=database_path,
            slack_notification_channel="slice_gh-test",
        )

    def test_posts_heartbeat_when_no_new_incident(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            slack = Mock()
            slack.fetch_channel_messages.return_value = []
            settings = self.settings(str(Path(directory) / "mapping.db"))
            synchronizer = IncidentSynchronizer(
                settings, slack, Mock(), Storage(settings.database_path)
            )

            synchronizer.run_once()

            slack.post_no_incident_notification.assert_called_once()

    def test_collection_failure_is_propagated_and_does_not_post_heartbeat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            slack = Mock()
            slack.fetch_channel_messages.side_effect = RuntimeError("Slack unavailable")
            settings = self.settings(str(Path(directory) / "mapping.db"))
            synchronizer = IncidentSynchronizer(
                settings, slack, Mock(), Storage(settings.database_path)
            )

            with self.assertRaises(RuntimeError):
                synchronizer.run_once()

            slack.post_no_incident_notification.assert_not_called()

    def test_posts_incident_notification_after_notion_page_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            from incident_slack_notion.models import SlackMessage

            root = SlackMessage(
                ts="1000.1",
                thread_ts="1000.1",
                user_id="U1",
                author="작성자",
                text="[오픈뱅킹 타임아웃 발생중]\n- 16:30 부터 장애 발생",
                permalink="https://slack.example/archives/C123/p10001",
                posted_at=datetime(2026, 6, 19, 16, 31, tzinfo=ZoneInfo("Asia/Seoul")),
            )
            slack = Mock()
            slack.fetch_channel_messages.return_value = [root]
            slack.fetch_thread.return_value = [root]
            notion = Mock()
            notion.ensure_report_body.return_value = False
            notion.create_incident.return_value = SimpleNamespace(
                id="page-id",
                url="https://notion.so/page-id",
            )
            settings = self.settings(str(Path(directory) / "mapping.db"))
            synchronizer = IncidentSynchronizer(
                settings, slack, notion, Storage(settings.database_path)
            )

            synchronizer.run_once()

            notion.create_incident.assert_called_once()
            slack.post_incident_created_notification.assert_called_once()
            slack.post_no_incident_notification.assert_not_called()

    def test_posts_incident_notification_after_empty_page_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            from incident_slack_notion.models import SlackMessage

            root = SlackMessage(
                ts="1000.1",
                thread_ts="1000.1",
                user_id="U1",
                author="작성자",
                text="[오픈뱅킹 부산은행 불안정]\n- 16:30 부터 장애 발생",
                permalink="https://slack.example/archives/C123/p10001",
                posted_at=datetime(2026, 6, 19, 16, 31, tzinfo=ZoneInfo("Asia/Seoul")),
            )
            slack = Mock()
            slack.fetch_channel_messages.return_value = []
            slack.fetch_thread.return_value = [root]
            notion = Mock()
            notion.ensure_report_body.return_value = True
            notion.page_url.return_value = "https://www.notion.so/page-id"
            settings = self.settings(str(Path(directory) / "mapping.db"))
            storage = Storage(settings.database_path)
            storage.upsert("1000.1", "1000.1", "page-id", "1000.1")
            synchronizer = IncidentSynchronizer(settings, slack, notion, storage)

            synchronizer.run_once()

            notion.ensure_report_body.assert_called_once()
            slack.post_incident_created_notification.assert_called_once()
            slack.post_no_incident_notification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
