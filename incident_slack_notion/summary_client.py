"""Gemini-backed incident summary generation."""

from __future__ import annotations

import logging
from typing import Any

import requests

from .models import Incident

LOGGER = logging.getLogger(__name__)


class GeminiIncidentSummarizer:
    def __init__(self, api_key: str, model: str) -> None:
        self.api_key = api_key.strip()
        self.model = (model or "gemini-2.0-flash").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def summarize(self, incident: Incident) -> str:
        if not self.enabled:
            LOGGER.warning("GEMINI_API_KEY가 없어 규칙 기반 장애 요약을 사용합니다.")
            return fallback_summary(incident)
        prompt = _build_prompt(incident)
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        try:
            response = requests.post(
                endpoint,
                params={"key": self.api_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "maxOutputTokens": 360,
                    },
                },
                timeout=12,
            )
            response.raise_for_status()
            text = _extract_text(response.json())
            summary = _normalize_summary(text)
            if summary:
                return summary
            LOGGER.warning("Gemini 응답에 요약 텍스트가 없어 규칙 기반 장애 요약을 사용합니다.")
        except Exception:
            LOGGER.exception("Gemini 장애 요약 생성 실패")
        return fallback_summary(incident)


def _build_prompt(incident: Incident) -> str:
    return f"""
너는 한국어 IT 운영 장애 리포트 작성자다.
아래 Slack 장애 원문과 스레드 진행 이력을 근거로 Notion 장애 보고서 최상단에 넣을 요약을 작성해라.

조건:
- 한국어 2~3줄
- 각 줄은 35자 이내로 짧고 명확하게 작성
- Markdown 제목 없이 bullet만 사용
- 원문에 없는 원인/조치 내용을 추정하지 말 것
- 포함할 내용: 대상/증상, 한패스 영향 여부, 상태/장애 시간
- Slack 링크 안내 같은 메타 설명은 쓰지 말 것

[기본 정보]
제목: {incident.title}
상태: {incident.status}
발생 일시: {incident.occurred_at}
정상화 일시: {incident.recovered_at}
장애 지속 시간: {incident.duration_text}
심각도: {incident.severity or "Minor"}
영향 범위: {incident.scope}
장애 구분: {incident.category}

[상세 내용]
{incident.details}

[복구 내용]
{incident.recovery_details}

[Slack 원문]
{incident.raw_message}

[진행 이력]
{incident.thread_summary}
""".strip()


def _extract_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates", []):
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        text = "\n".join(str(part.get("text", "")) for part in parts).strip()
        if text:
            return text
    return ""


def _normalize_summary(text: str) -> str:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        line = line.lstrip("-•* ").strip()
        if line:
            lines.append(f"- {line}")
        if len(lines) == 3:
            break
    return "\n".join(lines)


def fallback_summary(incident: Incident) -> str:
    """Create a deterministic short incident summary when Gemini is unavailable."""
    lines: list[str] = []
    title = _clean_title(incident.title.strip() or "장애")
    status = incident.status.strip() or ("정상화" if incident.recovered_at else "모니터링 중")
    started_at = incident.occurred_at.strftime("%H:%M:%S")
    recovered_at = incident.recovered_at.strftime("%H:%M:%S") if incident.recovered_at else ""

    lines.append(f"- {title} {started_at} 발생, 상태 {status}")
    lines.append(f"- 영향: {incident.scope or '확인 중'} / {incident.severity or 'Minor'} / {incident.category or '확인 중'}")
    if recovered_at:
        duration = incident.duration_text or "확인 중"
        lines.append(f"- {recovered_at} 정상화, 장애시간 {duration}")
    elif incident.recovery_details:
        lines.append(f"- 복구: {_truncate(' '.join(incident.recovery_details.split()), 40)}")
    else:
        lines.append("- 한패스 직접 영향 없음, 모니터링 필요")
    return "\n".join(lines[:3])


def _clean_title(value: str) -> str:
    value = value.replace("[", "").replace("]", "")
    return _truncate(" ".join(value.split()), 32)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"
