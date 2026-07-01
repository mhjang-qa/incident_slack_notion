import sys
import types
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo


class FakeAPIResponseError(Exception):
    status = 400
    code = "validation_error"


class FakeEndpoint:
    def __init__(self, response):
        self.response = response
        self.last_kwargs = None

    def retrieve(self, **kwargs):
        self.last_kwargs = kwargs
        return self.response

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return {"id": "page-id"}

    def update(self, **kwargs):
        self.last_kwargs = kwargs
        return {"id": kwargs["page_id"]}

    def append(self, **kwargs):
        self.last_kwargs = kwargs
        return {"id": kwargs["block_id"]}

    def list(self, **kwargs):
        self.last_kwargs = kwargs
        return {"results": self.response.get("results", [])}


class FakeClient:
    def __init__(self, auth):
        self.auth = auth
        self.databases = FakeEndpoint(
            {"data_sources": [{"id": "data-source-id", "name": "Incidents"}]}
        )
        self.data_sources = FakeEndpoint(
            {"properties": {"제목": {"type": "title", "title": {}}}}
        )
        self.pages = FakeEndpoint({})
        self.block_children = FakeEndpoint({"results": []})
        self.blocks = types.SimpleNamespace(children=self.block_children)


fake_package = types.ModuleType("notion_client")
fake_package.Client = FakeClient
fake_errors = types.ModuleType("notion_client.errors")
fake_errors.APIResponseError = FakeAPIResponseError
sys.modules.setdefault("notion_client", fake_package)
sys.modules.setdefault("notion_client.errors", fake_errors)

from incident_slack_notion.models import Incident
from incident_slack_notion.notion_client import NotionIncidentClient


class NotionClientTest(unittest.TestCase):
    def test_uses_first_data_source_for_schema_and_page_parent(self) -> None:
        client = NotionIncidentClient("token", "database-id")
        incident = Incident(
            title="테스트 장애",
            occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        page = client.create_incident(incident)

        self.assertEqual(client.data_source_id, "data-source-id")
        self.assertEqual(
            client.client.data_sources.last_kwargs,
            {"data_source_id": "data-source-id"},
        )
        self.assertEqual(page.id, "page-id")
        self.assertEqual(
            client.client.pages.last_kwargs["parent"],
            {"type": "data_source_id", "data_source_id": "data-source-id"},
        )
        self.assertGreater(len(client.client.pages.last_kwargs["children"]), 0)

    def test_backfills_empty_page_body(self) -> None:
        client = NotionIncidentClient("token", "database-id")
        incident = Incident(
            title="본문 없는 장애",
            occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )

        backfilled = client.ensure_report_body("page-id", incident)

        self.assertTrue(backfilled)
        self.assertEqual(client.client.block_children.last_kwargs["block_id"], "page-id")
        self.assertGreater(len(client.client.block_children.last_kwargs["children"]), 0)

    def test_adds_llm_summary_near_top_of_report(self) -> None:
        client = NotionIncidentClient("token", "database-id")
        incident = Incident(
            title="요약 장애",
            occurred_at=datetime(2026, 6, 18, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            llm_summary="- 외부 은행 타임아웃 발생\n- 한패스 직접 영향 없음",
        )

        client.create_incident(incident)
        children = client.client.pages.last_kwargs["children"]

        self.assertEqual(children[0]["heading_2"]["rich_text"][0]["text"]["content"], "장애 보고서")
        self.assertEqual(children[1]["heading_3"]["rich_text"][0]["text"]["content"], "LLM 요약")
        self.assertIn("외부 은행", children[2]["bulleted_list_item"]["rich_text"][0]["text"]["content"])


if __name__ == "__main__":
    unittest.main()
