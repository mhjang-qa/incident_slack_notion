"""Korean incident message parsing and report washing."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from .models import Incident, SlackMessage

INCIDENT_KEYWORDS = (
    "장애", "타임아웃", "서비스 오류", "거래 지연", "복구", "정상화", "모니터링", "영향 없음"
)
RECOVERY_KEYWORDS = ("정상화", "복구 완료", "조치 완료", "모니터링 종료")

TITLE_RE = re.compile(r"^\s*\[([^\]]+)]", re.MULTILINE)
TIME_RE = re.compile(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)")
RANGE_RE = re.compile(
    r"(?P<start>[0-2]?\d:[0-5]\d(?::[0-5]\d)?)\s*[~～\-]\s*"
    r"(?P<end>[0-2]?\d:[0-5]\d(?::[0-5]\d)?)"
)
DURATION_RE = re.compile(
    r"약?\s*(?:(?P<hours>\d+)\s*시간)?\s*(?:(?P<minutes>\d+)\s*분)?(?:간)?"
)


def is_incident_candidate(text: str) -> bool:
    normalized = text.strip()
    return bool(normalized and any(keyword in normalized for keyword in INCIDENT_KEYWORDS))


def is_recovery_message(text: str) -> bool:
    return any(keyword in text for keyword in RECOVERY_KEYWORDS)


def parse_incident(message: SlackMessage) -> Incident:
    text = message.text.strip()
    title_source = _title_source(text)
    occurred_at = _extract_first_datetime(text, message.posted_at)
    service = _extract_service(title_source)
    impact = _extract_impact(text)
    recovery = is_recovery_message(text)

    return Incident(
        title=_wash_title(title_source),
        occurred_at=occurred_at,
        service=service,
        status="정상화" if recovery else "모니터링 중",
        impact=impact,
        details=_wash_details(text, occurred_at, service, impact),
        reporter=message.author,
        slack_link=message.permalink,
        raw_message=text,
        source_ts=message.ts,
        thread_ts=message.thread_ts,
    )


def apply_thread(incident: Incident, messages: list[SlackMessage]) -> Incident:
    incident.thread_messages = messages
    incident.thread_summary = summarize_thread(messages)
    recovery_messages = [message for message in messages if is_recovery_message(message.text)]
    if not recovery_messages:
        return incident

    recovery_message = recovery_messages[-1]
    start, end = _extract_range(recovery_message.text, recovery_message.posted_at)
    incident.occurred_at = start or incident.occurred_at
    incident.recovered_at = end or _extract_first_datetime(
        recovery_message.text, recovery_message.posted_at
    )
    if incident.recovered_at and incident.occurred_at and incident.recovered_at < incident.occurred_at:
        incident.recovered_at += timedelta(days=1)

    explicit_minutes = _extract_explicit_duration(recovery_message.text)
    if explicit_minutes is not None:
        incident.duration_minutes = explicit_minutes
    elif incident.occurred_at and incident.recovered_at:
        incident.duration_minutes = max(
            0, round((incident.recovered_at - incident.occurred_at).total_seconds() / 60)
        )
    incident.duration_text = format_duration(incident.duration_minutes)
    incident.status = "정상화"
    incident.recovery_details = _wash_recovery(recovery_message, incident)
    return incident


def summarize_thread(messages: list[SlackMessage]) -> str:
    lines: list[str] = []
    for message in messages:
        body = re.sub(r"\s+", " ", message.text).strip()
        if not body:
            continue
        timestamp = message.posted_at.strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"- {timestamp} {message.author}: {body[:500]}")
    return "\n".join(lines)


def format_duration(minutes: int | None) -> str:
    if minutes is None:
        return ""
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"약 {hours}시간 {remainder}분"
    if hours:
        return f"약 {hours}시간"
    return f"약 {remainder}분"


def _title_source(text: str) -> str:
    match = TITLE_RE.search(text)
    if match:
        return match.group(1).strip()
    return next((line.strip() for line in text.splitlines() if line.strip()), "장애 공지")


def _wash_title(source: str) -> str:
    source = re.sub(r"\s+", " ", source).strip("[] ")
    service = _extract_service(source)
    subject = source
    if service:
        subject = re.sub(re.escape(service), "", subject, count=1).strip(" /-")
    subject = re.sub(r"\b(발생중|모니터링 중)\b", "", subject).strip()
    return f"[{service}] {subject}".strip() if service else source


def _extract_service(title: str) -> str:
    known = ("오픈뱅킹", "펌뱅킹", "한패스", "송금", "결제", "회원", "인증")
    found = [name for name in known if name in title]
    return "/".join(found)


def _extract_impact(text: str) -> str:
    if re.search(r"(한패스|서비스).{0,15}(영향|문제).{0,8}(없|무)", text):
        return "한패스 서비스 영향 없음"
    for line in text.splitlines():
        if "영향" in line:
            return line.strip(" -*")
    return "영향도 확인 중"


def _extract_first_datetime(text: str, base: datetime) -> datetime | None:
    match = TIME_RE.search(text)
    if not match:
        return base
    parsed_time = _parse_time(match.group(0))
    return datetime.combine(base.date(), parsed_time, tzinfo=base.tzinfo)


def _extract_range(text: str, base: datetime) -> tuple[datetime | None, datetime | None]:
    match = RANGE_RE.search(text)
    if not match:
        return None, None
    start = datetime.combine(base.date(), _parse_time(match.group("start")), tzinfo=base.tzinfo)
    # A recovery message posted shortly after midnight can contain a start time
    # from the previous day (for example, posted 00:21 with start 23:50).
    if start > base:
        start -= timedelta(days=1)
    end = datetime.combine(base.date(), _parse_time(match.group("end")), tzinfo=base.tzinfo)
    if end < start:
        end += timedelta(days=1)
    return start, end


def _parse_time(value: str) -> time:
    parts = [int(part) for part in value.split(":")]
    return time(parts[0], parts[1], parts[2] if len(parts) == 3 else 0)


def _extract_explicit_duration(text: str) -> int | None:
    for match in DURATION_RE.finditer(text):
        hours = match.group("hours")
        minutes = match.group("minutes")
        if hours or minutes:
            return int(hours or 0) * 60 + int(minutes or 0)
    return None


def _wash_details(text: str, occurred_at: datetime | None, service: str, impact: str) -> str:
    when = occurred_at.strftime("%Y-%m-%d %H:%M:%S") if occurred_at else "확인 시점"
    topic = service or "대상 서비스"
    summary = f"{when}부터 {topic} 관련 장애 징후가 확인되어 모니터링이 시작되었습니다."
    if impact == "한패스 서비스 영향 없음":
        summary += "\n확인 결과 한패스 서비스에 직접적인 영향은 없는 것으로 공유되었습니다."
    notes = [line.strip(" -*") for line in text.splitlines() if "CS" in line or "참고" in line]
    if notes:
        summary += f"\n비고: {' '.join(notes)}"
    return summary


def _wash_recovery(message: SlackMessage, incident: Incident) -> str:
    when = (
        incident.recovered_at.strftime("%Y-%m-%d %H:%M:%S")
        if incident.recovered_at
        else message.posted_at.strftime("%Y-%m-%d %H:%M:%S")
    )
    result = f"{when} 기준 정상화가 확인되었습니다."
    if incident.duration_text:
        result += f"\n총 장애 시간은 {incident.duration_text}입니다."
    return result
