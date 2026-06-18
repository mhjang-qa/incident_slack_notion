import tempfile
import unittest
from pathlib import Path

from incident_slack_notion.storage import Storage


class StorageTest(unittest.TestCase):
    def test_mapping_is_upserted_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(str(Path(directory) / "mapping.db"))
            storage.upsert("1.0", "1.0", "page-1", "1.0")
            storage.upsert("1.0", "1.0", "page-1", "2.0")

            mapping = storage.get(slack_ts="1.0")
            self.assertIsNotNone(mapping)
            self.assertEqual(mapping.notion_page_id, "page-1")
            self.assertEqual(mapping.last_thread_ts, "2.0")
            self.assertEqual(len(storage.tracked_incidents()), 1)


if __name__ == "__main__":
    unittest.main()
