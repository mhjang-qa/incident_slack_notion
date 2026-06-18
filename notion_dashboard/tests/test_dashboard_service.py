import unittest
from pathlib import Path

from notion_dashboard.services.dashboard_service import DashboardService


class DashboardServiceTest(unittest.TestCase):
    def test_render_embeds_payload_and_assets(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        html = DashboardService(project_dir).render(
            {"incidents": [], "synced_at": "2026-06-18T12:00:00+09:00"},
            static_mode=True,
        )
        self.assertIn("Notion Incident Intelligence", html)
        self.assertIn("window.DASHBOARD_DATA", html)
        self.assertIn("window.STATIC_MODE = true", html)
        self.assertIn("chart.umd.min.js", html)

    def test_render_escapes_script_terminator_in_embedded_data(self) -> None:
        project_dir = Path(__file__).resolve().parents[1]
        html = DashboardService(project_dir).render(
            {"incidents": [{"title": "</script><script>alert(1)</script>"}]},
            static_mode=True,
        )
        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("\\u003c/script\\u003e", html)


if __name__ == "__main__":
    unittest.main()
