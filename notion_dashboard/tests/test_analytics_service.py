import unittest

from notion_dashboard.services.analytics_service import (
    normalize_category,
    normalize_grade,
    normalize_impact,
    normalize_incidents,
    normalize_status,
)


class AnalyticsServiceTest(unittest.TestCase):
    def test_normalizes_cross_midnight_mttr(self) -> None:
        incidents = normalize_incidents(
            [
                {
                    "title": "자정 장애",
                    "registered_at": "2026-06-18T23:50:00+09:00",
                    "started_at": "2026-06-18T23:50:00+09:00",
                    "ended_at": "2026-06-18T00:20:00+09:00",
                }
            ]
        )
        self.assertEqual(incidents[0]["mttr_minutes"], 30)
        self.assertEqual(incidents[0]["status"], "정상화")

    def test_status_and_labels(self) -> None:
        self.assertEqual(normalize_status("처리중"), "조치중")
        self.assertEqual(normalize_status("Resolved"), "정상화")
        self.assertEqual(normalize_category("데이터베이스"), "DB")
        self.assertEqual(normalize_impact("한패스 영향 없음"), "Low")
        self.assertEqual(normalize_grade("Priority P2"), "P2")

    def test_missing_end_is_open(self) -> None:
        incident = normalize_incidents(
            [{"title": "진행 장애", "registered_at": "2026-06-18", "status": ""}]
        )[0]
        self.assertIsNone(incident["mttr_minutes"])
        self.assertEqual(incident["status"], "발생")


if __name__ == "__main__":
    unittest.main()

