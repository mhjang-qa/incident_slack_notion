import tempfile
import unittest
from pathlib import Path
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

    def test_does_not_post_heartbeat_when_collection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            slack = Mock()
            slack.fetch_channel_messages.side_effect = RuntimeError("Slack unavailable")
            settings = self.settings(str(Path(directory) / "mapping.db"))
            synchronizer = IncidentSynchronizer(
                settings, slack, Mock(), Storage(settings.database_path)
            )

            synchronizer.run_once()

            slack.post_no_incident_notification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
