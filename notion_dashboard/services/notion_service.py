"""Notion API collection with schema-aware field mapping."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from notion_client import Client
from notion_client.errors import APIResponseError

LOGGER = logging.getLogger(__name__)

FIELD_ALIASES = {
    "title": ("제목", "이름", "Name", "Title", "장애명"),
    "registered_at": ("등록일", "등록 일시", "생성일", "Created", "created_at"),
    "started_at": (
        "장애 시작시간", "장애 발생 일시", "발생 일시", "시작시간", "Start", "occurred_at"
    ),
    "ended_at": (
        "장애 종료시간", "장애 정상화 일시", "정상화 일시", "복구 일시", "End", "recovered_at"
    ),
    "category": ("장애 구분", "구분", "유형", "Category", "Type", "영향 서비스"),
    "impact": ("영향도", "영향", "Impact", "Severity"),
    "grade": ("등급", "우선순위", "Priority", "Grade"),
    "status": ("상태", "진행 상태", "Status"),
    "assignee": ("담당자", "담당", "Assignee", "Owner", "최초 공지자"),
    "cause": ("원인", "장애 원인", "Root Cause", "Cause"),
    "action": ("조치내용", "조치 내용", "조치", "Action", "Resolution", "상세 내용"),
    "url": ("Slack 링크", "링크", "URL"),
}

TYPE_HINTS = {
    "title": ("title",),
    "registered_at": ("created_time", "date"),
    "started_at": ("date",),
    "ended_at": ("date",),
    "category": ("select", "multi_select", "rich_text"),
    "impact": ("select", "status", "rich_text"),
    "grade": ("select", "status", "rich_text"),
    "status": ("status", "select", "rich_text"),
    "assignee": ("people", "rich_text", "created_by"),
    "cause": ("rich_text",),
    "action": ("rich_text",),
    "url": ("url",),
}


@dataclass(slots=True)
class NotionSnapshot:
    rows: list[dict[str, Any]]
    property_map: dict[str, str]
    data_source_id: str


class NotionService:
    def __init__(self, token: str, database_id: str) -> None:
        self.client = Client(auth=token)
        self.database_id = normalize_notion_id(database_id)
        database = self._call(
            self.client.databases.retrieve, database_id=self.database_id
        )
        data_sources = database.get("data_sources", [])
        if data_sources:
            self.data_source_id = str(data_sources[0]["id"])
            source = self._call(
                self.client.data_sources.retrieve,
                data_source_id=self.data_source_id,
            )
        else:
            # Legacy Notion API response compatibility.
            self.data_source_id = self.database_id
            source = database
        self.schema: dict[str, dict[str, Any]] = source.get("properties", {})
        if not self.schema:
            raise RuntimeError("Notion 장애 DB의 속성 스키마를 읽을 수 없습니다.")
        self.property_map = self._map_properties()
        LOGGER.info("Notion Dashboard 컬럼 매핑: %s", self.property_map)

    def fetch_all(self) -> NotionSnapshot:
        pages: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            if hasattr(self.client, "data_sources"):
                response = self._call(
                    self.client.data_sources.query,
                    data_source_id=self.data_source_id,
                    page_size=100,
                    start_cursor=cursor,
                )
            else:
                response = self._call(
                    self.client.databases.query,
                    database_id=self.database_id,
                    page_size=100,
                    start_cursor=cursor,
                )
            pages.extend(response.get("results", []))
            cursor = response.get("next_cursor")
            if not response.get("has_more") or not cursor:
                break
        return NotionSnapshot(
            rows=[self._page_to_row(page) for page in pages],
            property_map=self.property_map,
            data_source_id=self.data_source_id,
        )

    def _map_properties(self) -> dict[str, str]:
        mapped: dict[str, str] = {}
        normalized_names = {
            _normalize_name(name): name for name in self.schema
        }
        for field, aliases in FIELD_ALIASES.items():
            for alias in aliases:
                matched = normalized_names.get(_normalize_name(alias))
                if matched:
                    mapped[field] = matched
                    break

        # Type inference fills gaps after exact/normalized name matching.
        used = set(mapped.values())
        for field, allowed_types in TYPE_HINTS.items():
            if field in mapped:
                continue
            candidates = [
                name
                for name, definition in self.schema.items()
                if name not in used and definition.get("type") in allowed_types
            ]
            if len(candidates) == 1:
                mapped[field] = candidates[0]
                used.add(candidates[0])

        if "title" not in mapped:
            title = next(
                (
                    name
                    for name, definition in self.schema.items()
                    if definition.get("type") == "title"
                ),
                None,
            )
            if title:
                mapped["title"] = title
        return mapped

    def _page_to_row(self, page: dict[str, Any]) -> dict[str, Any]:
        properties = page.get("properties", {})
        row = {
            field: _property_value(properties.get(property_name, {}))
            for field, property_name in self.property_map.items()
        }
        row["notion_url"] = page.get("url", "")
        row["page_id"] = page.get("id", "")
        row.setdefault("registered_at", page.get("created_time"))
        return row

    @staticmethod
    def _call(method: Callable[..., Any], **kwargs: Any) -> Any:
        for attempt in range(4):
            try:
                clean_kwargs = {key: value for key, value in kwargs.items() if value is not None}
                return method(**clean_kwargs)
            except APIResponseError as exc:
                if exc.status not in {409, 429, 500, 502, 503, 504} or attempt == 3:
                    raise
                time.sleep(min(2**attempt, 8))
        raise RuntimeError("Notion API 재시도 횟수를 초과했습니다.")


def normalize_notion_id(value: str) -> str:
    compact = value.strip().replace("-", "")
    match = re.search(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])", compact)
    if not match:
        raise RuntimeError("NOTION_DATABASE_ID 형식이 올바르지 않습니다.")
    raw = match.group(1).lower()
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s_\-()/]", "", value).lower()


def _property_value(prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    value = prop.get(kind) if kind else None
    if kind in {"title", "rich_text"}:
        return "".join(item.get("plain_text", "") for item in (value or []))
    if kind in {"select", "status"}:
        return (value or {}).get("name", "")
    if kind == "multi_select":
        return ", ".join(item.get("name", "") for item in (value or []))
    if kind == "date":
        return (value or {}).get("start")
    if kind == "people":
        return ", ".join(
            person.get("name") or person.get("person", {}).get("email", "")
            for person in (value or [])
        )
    if kind == "created_by":
        return (value or {}).get("name", "")
    if kind in {"created_time", "last_edited_time", "url", "email", "phone_number", "number"}:
        return value
    if kind == "formula":
        formula = value or {}
        return formula.get(formula.get("type"))
    if kind == "rollup":
        rollup = value or {}
        return rollup.get(rollup.get("type"))
    if kind == "checkbox":
        return bool(value)
    return ""

