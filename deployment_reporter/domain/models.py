from datetime import datetime, timezone
from enum import Enum
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


def now():
    return datetime.now(timezone.utc).isoformat()


class Component(str, Enum):
    NATIVE = "NATIVE"
    AOS = "NATIVE-AOS"
    IOS = "NATIVE-IOS"
    COMMON = "NATIVE-COMMON"
    FE = "FE"
    BE = "BE"
    BO = "BO"
    ETC = "ETC"


class SlackMessage(BaseModel):
    channel: str
    ts: str = Field(pattern=r"^\d+\.\d+$")
    text: str
    user: str = ""
    thread_ts: str | None = None
    message_id: str | None = None
    url: str = ""
    collected_at: str = Field(default_factory=now)
    payload: dict = Field(default_factory=dict)

    @property
    def key(self):
        return f"{self.channel}:{self.ts}"


class Evidence(BaseModel):
    message: SlackMessage
    message_type: Literal["Announcement", "Thread", "Follow-up", "Issue", "QA", "Other"]
    relevance_score: float = Field(ge=0, le=1)
    included: bool
    reasons: list[str] = Field(default_factory=list)


class DeploymentReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_deployment: StrictBool
    service: str | None
    project: str | None
    title: str
    primary_component: Component | None
    components: list[Component]
    component_reason: str
    classification_confidence: float = Field(ge=0, le=1)
    environment: str | None
    version: str | None
    build: str | None
    scheduled_at: datetime | None
    estimated_duration_minutes: int | None = Field(ge=0)
    summary: str
    changes: list[str]
    issues: list[str]
    hotfix: StrictBool
    qa_related: list[str]
    result: str
    risk: str
    participants: list[str]
    links: list[str]
    deployment_type: str | None = None
    deployed_at: datetime | None = None
    # These must be supported by direct Slack quotes; deployed != verified.
    deployment_evidence: str | None = None
    verification_evidence: str | None = None

    @field_validator("scheduled_at", "deployed_at")
    @classmethod
    def aware_dates(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("datetime must include timezone")
        return value

    @model_validator(mode="after")
    def coherent_components(self):
        if len(set(self.components)) != len(self.components):
            raise ValueError("components must be unique")
        if self.primary_component and self.primary_component not in self.components:
            raise ValueError("primary_component must belong to components")
        if self.classification_confidence >= .7 and self.is_deployment and not self.components:
            raise ValueError("confident classification requires components")
        return self

    @property
    def review_status(self):
        if self.classification_confidence < .7:
            return "Review Required"
        return "확인 권장" if self.classification_confidence < .9 else "자동 확정"

    @property
    def effective_components(self):
        return [Component.ETC] if self.classification_confidence < .7 else self.components

    @property
    def effective_primary(self):
        return Component.ETC if self.classification_confidence < .7 else self.primary_component


def status(report: DeploymentReport, evidence: list[Evidence]):
    texts = [e.message.text for e in evidence if e.included]
    def supported(quote):
        uncertain = r"예정|예상|요청|필요|계획|미확인|실패|않|아직|못|되면|하면|오류|불가"
        return bool(quote and len(quote.strip()) >= 4 and not re.search(uncertain, quote) and any(quote in t for t in texts))
    if report.issues:
        return "Issue Found"
    if report.hotfix:
        return "Hotfix"
    if supported(report.verification_evidence) and re.search(r"정상|문제\s*없|검증\s*완료|테스트\s*(?:통과|완료)|verified|passed", report.verification_evidence, re.I):
        return "Completed"
    if supported(report.deployment_evidence) and re.search(r"배포\s*(?:완료|했|하였|됨)|재배포\s*(?:했|완료)|deployed|released", report.deployment_evidence, re.I):
        return "Deployed"
    return "Detected"
