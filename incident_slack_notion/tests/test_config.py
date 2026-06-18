import unittest

from incident_slack_notion.config import _normalize_notion_database_id


class ConfigTest(unittest.TestCase):
    def test_normalizes_compact_database_id(self) -> None:
        self.assertEqual(
            _normalize_notion_database_id("2a373fbd19518026ad6fc42b369368fc"),
            "2a373fbd-1951-8026-ad6f-c42b369368fc",
        )

    def test_extracts_database_id_from_notion_url(self) -> None:
        self.assertEqual(
            _normalize_notion_database_id(
                "https://app.notion.com/p/2a373fbd19518026ad6fc42b369368fc"
                "?v=2a373fbd19518059ac57000c75065807"
            ),
            "2a373fbd-1951-8026-ad6f-c42b369368fc",
        )

    def test_rejects_invalid_database_id(self) -> None:
        with self.assertRaises(RuntimeError):
            _normalize_notion_database_id("not-a-database-id")


if __name__ == "__main__":
    unittest.main()
