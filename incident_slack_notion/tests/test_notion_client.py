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

        page_id = client.create_incident(incident)

        self.assertEqual(client.data_source_id, "data-source-id")
        self.assertEqual(
            client.client.data_sources.last_kwargs,
            {"data_source_id": "data-source-id"},
        )
        self.assertEqual(page_id, "page-id")
        self.assertEqual(
            client.client.pages.last_kwargs["parent"],
            {"type": "data_source_id", "data_source_id": "data-source-id"},
        )


if __name__ == "__main__":
    unittest.main()
