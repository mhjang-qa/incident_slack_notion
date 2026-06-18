"""Incident normalization and dashboard analytics payload generation."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from dateutil import parser as date_parser

KST = ZoneInfo("Asia/Seoul")

STATUS_MAP = {
    "발생": "발생",
    "신규": "발생",
    "open": "발생",
    "분석중": "분석중",
    "진행중": "분석중",
    "확인중": "분석중",
    "investigating": "분석중",
    "조치중": "조치중",
    "처리중": "조치중",
    "in progress": "조치중",
    "정상화": "정상화",
    "복구": "정상화",
    "resolved": "정상화",
    "완료": "완료",
    "종료": "완료",
    "done": "완료",
    "closed": "완료",
}


def normalize_incidents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source in rows:
        started = _parse_datetime(source.get("started_at") or source.get("registered_at"))
        ended = _parse_datetime(source.get("ended_at"))
        if started and ended and ended < started:
            ended += timedelta(days=1)
        mttr = (
            max(0, round((ended - started).total_seconds() / 60))
            if started and ended
            else None
        )
        status = normalize_status(source.get("status"), ended)
        registered = _parse_datetime(source.get("registered_at")) or started
        normalized.append(
            {
                "id": source.get("page_id", ""),
                "url": source.get("notion_url") or source.get("url", ""),
                "title": _text(source.get("title")) or "제목 없음",
                "registered_at": _iso(registered),
                "date": registered.strftime("%Y-%m-%d") if registered else "",
                "month": registered.strftime("%Y-%m") if registered else "",
                "started_at": _iso(started),
                "ended_at": _iso(ended),
                "category": normalize_category(source.get("category")),
                "impact": normalize_impact(source.get("impact")),
                "grade": normalize_grade(source.get("grade")),
                "status": status,
                "assignee": _text(source.get("assignee")) or "미지정",
                "cause": _text(source.get("cause")),
                "action": _text(source.get("action")),
                "mttr_minutes": mttr,
            }
        )
    normalized.sort(key=lambda item: item["registered_at"] or "", reverse=True)
    return normalized


def build_payload(
    rows: list[dict[str, Any]],
    property_map: dict[str, str],
    synced_at: datetime | None = None,
) -> dict[str, Any]:
    incidents = normalize_incidents(rows)
    frame = pd.DataFrame(incidents)
    summary = {
        "total": len(incidents),
        "months": int(frame["month"].nunique()) if not frame.empty else 0,
        "mapped_fields": len(property_map),
    }
    return {
        "incidents": incidents,
        "summary": summary,
        "property_map": property_map,
        "synced_at": _iso(synced_at or datetime.now(tz=KST)),
    }


def normalize_status(value: Any, ended_at: datetime | None = None) -> str:
    text = _text(value).lower()
    for key, target in STATUS_MAP.items():
        if key in text:
            return target
    if ended_at:
        return "정상화"
    return "발생" if not text else "분석중"


def normalize_category(value: Any) -> str:
    text = _text(value).upper()
    rules = (
        ("API", ("API", "인터페이스")),
        ("WEB", ("WEB", "웹")),
        ("APP", ("APP", "앱", "모바일")),
        ("INFRA", ("INFRA", "인프라", "서버", "NETWORK", "네트워크")),
        ("DB", ("DB", "DATABASE", "데이터베이스")),
    )
    for category, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category
    return text or "ETC"


def normalize_impact(value: Any) -> str:
    text = _text(value).lower()
    if any(key in text for key in ("critical", "치명", "최고", "매우 높")):
        return "Critical"
    if any(key in text for key in ("high", "높", "major")):
        return "High"
    if any(key in text for key in ("medium", "중", "보통")):
        return "Medium"
    if any(key in text for key in ("low", "낮", "minor", "영향 없음", "문제 없")):
        return "Low"
    return "Unknown"


def normalize_grade(value: Any) -> str:
    text = _text(value).upper().replace(" ", "")
    match = re.search(r"P[1-4]", text)
    return match.group(0) if match else "미지정"


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, "", float("nan")):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = date_parser.parse(str(value))
        except (ValueError, TypeError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()

