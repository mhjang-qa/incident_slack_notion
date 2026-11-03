import json
import logging
import re
from pydantic import ValidationError

from ..domain.models import DeploymentReport
from ..domain.relevance import rule_hints
from ..http import APIError, Transport
from .parser import parse_report
from .prompts import SYSTEM_PROMPT, PROMPT_VERSION

LOG = logging.getLogger(__name__)


class GeminiClient:
    prompt_version = PROMPT_VERSION

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.model = settings.gemini_model
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", self.model):
            raise ValueError("GEMINI_MODEL must be a model name")
        self.http = transport or Transport(retries=settings.max_retries)

    def analyze(self, evidence):
        key = self.settings.gemini_api_key.get_secret_value()
        if not key:
            raise APIError("GEMINI_API_KEY is required")
        messages = [{"id": e.message.key, "type": e.message_type, "text": e.message.text, "user": e.message.user, "timestamp": e.message.ts, "url": e.message.url} for e in evidence if e.included]
        content = json.dumps({"timezone": self.settings.timezone, "service_aliases": self.settings.aliases,
                              "rule_hints": rule_hints("\n".join(m["text"] for m in messages)), "messages": messages}, ensure_ascii=False)
        # Never silently truncate the audit trail. An overlarge event needs operator review.
        if len(content) > 500_000:
            raise APIError("Event exceeds 500000 character analysis limit; narrow the window and retry")
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": content}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json", "responseJsonSchema": DeploymentReport.model_json_schema()},
        }
        LOG.info("[Gemini] structured summary requested")
        for attempt in range(self.settings.max_retries):
            response = self.http.request("POST", f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent", headers={"x-goog-api-key": key}, json=payload)
            try:
                candidates = response.get("candidates", [])
                if not candidates or candidates[0].get("finishReason") not in {None, "STOP"}:
                    raise ValueError("Blocked or incomplete model response")
                text = "".join(p.get("text", "") for p in candidates[0].get("content", {}).get("parts", []) if not p.get("thought"))
                report = parse_report(text)
                LOG.info("[Validation] LLM schema validated")
                return report, text
            except (ValidationError, ValueError):
                LOG.warning("[Validation] invalid LLM response attempt=%d", attempt + 1)
                if attempt + 1 == self.settings.max_retries:
                    raise APIError("Gemini returned invalid or incomplete JSON; retry limit reached") from None
                # Repeat with an explicit repair instruction; never echo untrusted output into system prompt.
                payload["contents"] = [{"role": "user", "parts": [{"text": content + "\n이전 응답이 스키마 검증에 실패했습니다. 모든 필수 필드, ISO offset, component 일관성을 확인하고 JSON만 반환하세요."}]}]
        raise APIError("Gemini retry limit reached")

    def test(self):
        key = self.settings.gemini_api_key.get_secret_value()
        if not key:
            raise APIError("GEMINI_API_KEY is required")
        data = self.http.request("GET", f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}", headers={"x-goog-api-key": key})
        return {"ok": True, "model": data.get("name"), "note": "Model metadata read only; generation quota not tested"}
