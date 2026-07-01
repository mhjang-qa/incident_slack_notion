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
            return ""
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
            return _normalize_summary(text)
        except Exception:
            LOGGER.exception("Gemini 장애 요약 생성 실패")
            return ""


def _build_prompt(incident: Incident) -> str:
    return f"""
너는 한국어 IT 운영 장애 리포트 작성자다.
아래 Slack 장애 원문과 스레드 진행 이력을 근거로 Notion 장애 보고서 최상단에 넣을 요약을 작성해라.

조건:
- 한국어 3~5줄
- 각 줄은 짧고 명확하게 작성
- Markdown 제목 없이 bullet만 사용
- 원문에 없는 원인/조치 내용을 추정하지 말 것
- 포함할 내용: 대상/증상, 한패스 영향 여부, 상태/정상화 시간, 장애 시간, 대응 참고사항

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
        if len(lines) == 5:
            break
    return "\n".join(lines)
