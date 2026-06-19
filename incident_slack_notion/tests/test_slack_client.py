import sys
import types
import unittest


class FakeSlackApiError(Exception):
    pass


class FakeWebClient:
    def __init__(self, token: str) -> None:
        self.token = token

    def auth_test(self):
        return {"ok": True, "user_id": "U123", "bot_id": "B123", "user": "bot"}

    def users_info(self, user: str):
        return {
            "ok": True,
            "user": {
                "id": user,
                "profile": {
                    "display_name": self.display_name,
                    "real_name": self.display_name,
                    "bot_id": "B123",
                },
            },
        }

    def bots_info(self, bot: str):
        return {
            "ok": True,
            "bot": {
                "id": bot,
                "name": self.bot_name,
                "app_id": "A123",
                "user_id": "U123",
            },
        }


fake_slack_sdk = types.ModuleType("slack_sdk")
fake_slack_sdk.WebClient = FakeWebClient
fake_errors = types.ModuleType("slack_sdk.errors")
fake_errors.SlackApiError = FakeSlackApiError
sys.modules.setdefault("slack_sdk", fake_slack_sdk)
sys.modules.setdefault("slack_sdk.errors", fake_errors)

from incident_slack_notion.slack_client import SlackIncidentClient


class SlackIdentityTest(unittest.TestCase):
    def test_accepts_expected_bot_name(self) -> None:
        client = SlackIncidentClient("xoxb-test", "C123", None)
        client.client.display_name = "Hanpass QA Bot"
        client.client.bot_name = "Hanpass QA Bot"
        self.assertEqual(
            client.validate_bot_identity("Hanpass QA Bot"),
            "Hanpass QA Bot",
        )

    def test_warns_but_allows_stale_profile_name(self) -> None:
        client = SlackIncidentClient("xoxb-test", "C123", None)
        client.client.display_name = "GO Hanpass QA Bot"
        client.client.bot_name = "Hanpass QA Bot"
        self.assertEqual(
            client.validate_bot_identity("Hanpass QA Bot"),
            "Hanpass QA Bot",
        )

    def test_allows_mismatch_for_slack_to_resolve_at_post_time(self) -> None:
        client = SlackIncidentClient("xoxb-test", "C123", None)
        client.client.display_name = "GO Hanpass QA Bot"
        client.client.bot_name = "GO Hanpass QA Bot"
        self.assertEqual(
            client.validate_bot_identity("Hanpass QA Bot"),
            "GO Hanpass QA Bot",
        )


if __name__ == "__main__":
    unittest.main()
