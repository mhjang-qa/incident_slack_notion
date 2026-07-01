"""Notion database adapter with schema-aware property mapping."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from notion_client import Client
from notion_client.errors import APIResponseError

from .models import Incident

LOGGER = logging.getLogger(__name__)
NOTION_PAGE_ID_RE = re.compile(r"(?<![0-9a-fA-F])([0-9a-fA-F]{32})(?![0-9a-fA-F])")


@dataclass(frozen=True, slots=True)
class NotionPageResult:
    id: str
    url: str

# Logical field -> preferred Notion property name. Edit this map when the DB uses
# names not covered by the aliases below.
PROPERTY_MAP = {
    "title": "제목",
    "status": "상태",
    "occurred_at": "장애 발생 일시",
    "recovered_at": "장애 정상화 일시",
    "duration": "장애 지속 시간",
    "service": "영향 서비스",
    "category": "장애 구분",
    "severity": "심각도 (Severity)",
    "scope": "영향 범위",
    "impact": "영향도",
    "details": "상세 내용",
    "reporter": "최초 공지자",
    "slack_link": "Slack",
    "raw_message": "원문 메시지",
    "thread_summary": "스레드 요약",
    "created_at": "등록 일시",
    "updated_at": "최종 업데이트 일시",
}

PROPERTY_ALIASES = {
    "title": ("이름", "Name", "Title"),
    "status": ("진행 상태", "Status"),
    "occurred_at": ("발생 일시", "발생일시", "장애 발생일"),
    "recovered_at": ("정상화 일시", "복구 일시", "장애 종료 일시"),
    "duration": ("지속 시간", "장애시간", "Duration"),
    "service": ("대상 서비스", "서비스", "영향서비스"),
    "category": ("장애구분", "장애 유형", "장애 타입", "구분", "Category"),
    "severity": ("심각도", "Severity", "등급", "Grade"),
    "scope": ("영향범위", "영향 서비스", "영향서비스", "대상 서비스", "서비스"),
    "impact": ("영향", "Impact"),
    "details": ("장애 내용", "내용", "Details"),
    "reporter": ("공지자", "작성자", "Reporter"),
    "slack_link": ("Slack 링크", "Slack URL", "슬랙 링크", "링크"),
    "raw_message": ("원문", "Slack 원문"),
    "thread_summary": ("스레드", "진행 이력"),
    "created_at": ("등록일시", "생성 일시"),
    "updated_at": ("수정 일시", "업데이트 일시"),
}


class NotionIncidentClient:
    def __init__(self, token: str, database_id: str) -> None:
        self.client = Client(auth=token)
        self.database_id = database_id
        self.data_source_id: str | None = None
        try:
            database = self._call(
                self.client.databases.retrieve, database_id=database_id
            )
        except APIResponseError as exc:
            if exc.code == "object_not_found":
                raise RuntimeError(
                    "Notion Database를 찾을 수 없습니다. NOTION_DATABASE_ID가 실제 "
                    "Database ID인지 확인하고, Database의 연결(Connections)에 "
                    "Integration을 초대하세요."
                ) from exc
            if exc.code == "unauthorized":
                raise RuntimeError(
                    "NOTION_TOKEN 인증에 실패했습니다. Internal Integration Secret을 "
                    "다시 확인하세요."
                ) from exc
            raise
        # Since Notion API 2025-09-03, a database is a container and its
        # properties belong to a child data source. Preserve the legacy branch
        # for older response shapes.
        if "properties" in database:
            self.schema = database["properties"]
        else:
            data_sources = database.get("data_sources", [])
            if not data_sources:
                raise RuntimeError(
                    "Notion Database에 접근 가능한 Data Source가 없습니다. "
                    "Database 연결 권한을 확인하세요."
                )
            self.data_source_id = str(data_sources[0]["id"])
            data_source = self._call(
                self.client.data_sources.retrieve,
                data_source_id=self.data_source_id,
            )
            self.schema = data_source["properties"]
        self.resolved_names = self._resolve_property_names()
        LOGGER.info(
            "Notion DB 컬럼 매핑(data_source=%s): %s",
            self.data_source_id or "legacy",
            self.resolved_names,
        )

    def create_incident(self, incident: Incident) -> NotionPageResult:
        now = datetime.now(tz=incident.occurred_at.tzinfo if incident.occurred_at else None)
        properties = self._build_properties(incident, now, include_created=True)
        parent = (
            {"type": "data_source_id", "data_source_id": self.data_source_id}
            if self.data_source_id
            else {"database_id": self.database_id}
        )
        response = self._call(
            self.client.pages.create,
            parent=parent,
            properties=properties,
            children=self._build_report_children(incident),
        )
        return NotionPageResult(id=str(response["id"]), url=str(response.get("url") or ""))

    def update_incident(self, page_id: str, incident: Incident) -> None:
        now = datetime.now(tz=incident.occurred_at.tzinfo if incident.occurred_at else None)
        properties = self._build_properties(incident, now, include_created=False)
        self._call(self.client.pages.update, page_id=page_id, properties=properties)
        self._append_thread_update(page_id, incident)

    def ensure_report_body(self, page_id: str, incident: Incident) -> bool:
        """Backfill generated body blocks when an existing page is title-only."""
        response = self._call(
            self.client.blocks.children.list,
            block_id=page_id,
            page_size=1,
        )
        if response.get("results"):
            return False
        self._call(
            self.client.blocks.children.append,
            block_id=page_id,
            children=self._build_report_children(incident),
        )
        LOGGER.info("비어 있는 Notion 장애 보고서 본문 보정: %s", page_id)
        return True

    def append_report_body(self, page_id: str, incident: Incident) -> None:
        """Append a generated incident report body regardless of existing blocks."""
        self._call(
            self.client.blocks.children.append,
            block_id=page_id,
            children=self._build_report_children(incident),
        )

    @staticmethod
    def page_url(page_id: str) -> str:
        compact_id = page_id.replace("-", "")
        return f"https://www.notion.so/{compact_id}" if compact_id else ""

    def _build_report_children(self, incident: Incident) -> list[dict[str, Any]]:
        """Create the page body equivalent to the Notion '장애 보고서' template."""
        rows = [
            ("상태", incident.status or "모니터링 중"),
            ("발생 일시", _format_datetime(incident.occurred_at)),
            ("정상화 일시", _format_datetime(incident.recovered_at) or "진행 중"),
            ("장애 지속 시간", incident.duration_text or "진행 중"),
            ("심각도", incident.severity or incident.impact or "확인 중"),
            ("영향 범위", incident.scope or incident.service or "확인 중"),
            ("장애 구분", incident.category or "확인 중"),
            ("최초 공지자", incident.reporter or "확인 중"),
        ]
        children: list[dict[str, Any]] = [
            _heading_2("장애 보고서"),
            _callout(
                "🚨",
                f"{incident.title}\n"
                f"상태: {incident.status or '모니터링 중'}\n"
                f"발생: {_format_datetime(incident.occurred_at) or '확인 중'}",
            ),
            _heading_3("1. 장애 개요"),
            *[_bulleted_item(f"{label}: {value}") for label, value in rows],
            _heading_3("2. 상세 내용"),
            _paragraph(incident.details or "장애 상세 내용은 확인 중입니다."),
            _heading_3("3. 조치 및 복구"),
            _paragraph(incident.recovery_details or "현재 모니터링 및 원인 확인 중입니다."),
            _heading_3("4. Slack 원문"),
            _paragraph(incident.raw_message or "원문 메시지가 없습니다."),
        ]
        if incident.thread_summary:
            children.extend(
                [
                    _heading_3("5. 진행 이력"),
                    _paragraph(incident.thread_summary),
                ]
            )
        if incident.slack_link:
            children.append(_bookmark_or_link("Slack 원문 바로가기", incident.slack_link))
        return children[:100]

    def _append_thread_update(self, page_id: str, incident: Incident) -> None:
        """Append recovery/thread changes below the initial generated report."""
        if not incident.thread_summary and not incident.recovery_details:
            return
        children = [
            {"object": "block", "type": "divider", "divider": {}},
            _heading_3(f"업데이트 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
            _paragraph(incident.recovery_details or incident.thread_summary),
        ]
        if incident.thread_summary:
            children.append(_paragraph(incident.thread_summary))
        self._call(
            self.client.blocks.children.append,
            block_id=page_id,
            children=children[:100],
        )

    def _resolve_property_names(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for logical_name, preferred in PROPERTY_MAP.items():
            candidates = (preferred, *PROPERTY_ALIASES.get(logical_name, ()))
            matched = next((candidate for candidate in candidates if candidate in self.schema), None)
            if matched:
                result[logical_name] = matched

        # Every database has exactly one title property; use it even if renamed.
        if "title" not in result:
            title_name = next(
                (name for name, definition in self.schema.items() if definition["type"] == "title"),
                None,
            )
            if title_name:
                result["title"] = title_name
        if "title" not in result:
            raise RuntimeError("Notion DB에서 title 속성을 찾을 수 없습니다.")
        return result

    def _build_properties(
        self, incident: Incident, now: datetime, include_created: bool
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "title": incident.title,
            "status": incident.status,
            "occurred_at": incident.occurred_at,
            "recovered_at": incident.recovered_at,
            "duration": incident.duration_text,
            "service": incident.service,
            "category": incident.category,
            "severity": incident.severity,
            "scope": incident.scope,
            "impact": incident.impact,
            "details": _join_nonempty(incident.details, incident.recovery_details),
            "reporter": incident.reporter,
            "slack_link": incident.slack_link,
            "raw_message": incident.raw_message,
            "thread_summary": incident.thread_summary,
            "updated_at": now,
        }
        values["severity"] = values.get("severity") or "Minor"
        if include_created:
            values["created_at"] = now

        properties: dict[str, Any] = {}
        for logical_name, value in values.items():
            actual_name = self.resolved_names.get(logical_name)
            if not actual_name or value in (None, ""):
                continue
            definition = self.schema[actual_name]
            if logical_name == "duration" and definition["type"] == "number":
                value = incident.duration_minutes
                if value is None:
                    continue
            encoded = self._encode(definition["type"], value)
            if encoded is not None:
                properties[actual_name] = encoded
        return properties

    @staticmethod
    def _encode(property_type: str, value: Any) -> dict[str, Any] | None:
        if property_type == "title":
            return {"title": _rich_text(str(value))}
        if property_type == "rich_text":
            return {"rich_text": _rich_text(str(value))}
        if property_type == "url":
            return {"url": str(value)}
        if property_type == "date" and isinstance(value, datetime):
            return {"date": {"start": value.isoformat()}}
        if property_type in {"select", "status"}:
            return {property_type: {"name": str(value)}}
        if property_type == "multi_select":
            names = [part.strip() for part in str(value).split("/") if part.strip()]
            return {"multi_select": [{"name": name} for name in names]}
        if property_type == "number":
            number = value if isinstance(value, (int, float)) else _first_number(str(value))
            return {"number": number} if number is not None else None
        if property_type == "email":
            return {"email": str(value)}
        if property_type == "phone_number":
            return {"phone_number": str(value)}
        LOGGER.warning("지원하지 않는 Notion 속성 타입 건너뜀: %s", property_type)
        return None

    @staticmethod
    def _call(method: Callable[..., Any], **kwargs: Any) -> Any:
        for attempt in range(4):
            try:
                return method(**kwargs)
            except APIResponseError as exc:
                retryable = exc.status in {409, 429, 500, 502, 503, 504}
                if not retryable or attempt == 3:
                    raise
                delay = min(2**attempt, 8)
                LOGGER.warning("Notion API 오류, %s초 후 재시도: %s", delay, exc)
                time.sleep(delay)
        raise RuntimeError("Notion API 재시도 횟수를 초과했습니다.")


def _rich_text(value: str) -> list[dict[str, Any]]:
    # Notion limits each rich_text text.content fragment to 2,000 characters.
    return [
        {"type": "text", "text": {"content": value[index : index + 2000]}}
        for index in range(0, len(value), 2000)
    ][:100]


def _heading_2(value: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": _rich_text(value)},
    }


def _heading_3(value: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": _rich_text(value)},
    }


def _paragraph(value: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(value)},
    }


def _bulleted_item(value: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": _rich_text(value)},
    }


def _callout(icon: str, value: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": icon},
            "rich_text": _rich_text(value),
        },
    }


def _bookmark_or_link(label: str, url: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {"type": "text", "text": {"content": label, "link": {"url": url}}}
            ]
        },
    }


def _format_datetime(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def _first_number(value: str) -> float | None:
    import re

    match = re.search(r"\d+(?:\.\d+)?", value)
    return float(match.group(0)) if match else None


def _join_nonempty(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part)


def normalize_page_id(value: str) -> str:
    cleaned = value.strip().replace("-", "")
    match = NOTION_PAGE_ID_RE.search(cleaned)
    if not match:
        raise RuntimeError("Notion page ID 또는 page URL에서 32자리 ID를 찾을 수 없습니다.")
    compact = match.group(1).lower()
    return (
        f"{compact[:8]}-{compact[8:12]}-{compact[12:16]}-"
        f"{compact[16:20]}-{compact[20:]}"
    )
