"""Deterministic counts over published deployment records; no LLM calls."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from calendar import monthrange
from zoneinfo import ZoneInfo

UNKNOWN = "미확인"


def plain(parts):
    return "".join(p.get("plain_text", p.get("text", {}).get("content", "")) for p in parts)


def property_text(properties, name):
    prop = properties.get(name, {})
    if prop.get("type") in {"select", "status"} or "select" in prop:
        return (prop.get(prop.get("type", "select")) or {}).get("name", "")
    return plain(prop.get("rich_text", prop.get("title", [])))


def label(value):
    value = (value or "").strip()
    return UNKNOWN if value.casefold() in {"", "null", "none", "unknown", "미확인", "확인 중", "-"} else value


def deployment_day(value, timezone):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
        return parsed.astimezone(ZoneInfo(timezone)).date()
    except (ValueError, TypeError, AttributeError):
        return None


@dataclass(frozen=True)
class Record:
    key: str
    day: date | None
    service: str
    project: str
    environment: str
    components: tuple[str, ...]
    status: str
    hotfix: bool
    issue: bool

    def payload(self):
        return {**asdict(self), "day": self.day.isoformat() if self.day else None}


class Statistics:
    def __init__(self, pages, timezone="Asia/Seoul", aliases=None, today=None, jobs=None):
        self.today = today or datetime.now(ZoneInfo(timezone)).date()
        self.jobs = [j for j in (jobs or []) if j["request"].get("sync", True)]
        self.records = []
        self.duplicates = 0
        self.rejected = 0
        canonical = {name.casefold(): service for service, names in (aliases or {}).items() for name in [service, *names]}
        unique = {}
        for page in sorted(pages, key=lambda p: (p.get("last_edited_time", ""), p["id"])):
            if page.get("archived") or page.get("in_trash"):
                continue
            properties = page.get("properties", {})
            key = property_text(properties, "Source Key").strip() or "page:" + page["id"]
            if key in unique:
                self.duplicates += 1
            unique[key] = page
        environments = {"prod": "Production", "production": "Production", "운영": "Production", "stage": "Stage", "staging": "Stage", "stg": "Stage", "dev": "DEV", "development": "DEV", "개발": "DEV"}
        for key, page in sorted(unique.items()):
            p = page["properties"]
            status = property_text(p, "Status")
            if status == "Rejected":
                self.rejected += 1
                continue
            service = label(property_text(p, "Service"))
            service = canonical.get(service.casefold(), service)
            environment = label(property_text(p, "Environment"))
            components = {x["name"] for x in p.get("Components", {}).get("multi_select", [])}
            if not components:
                components = {property_text(p, "Component") or "ETC"}
            self.records.append(Record(key, deployment_day((p.get("Date", {}).get("date") or {}).get("start"), timezone),
                                       service, label(property_text(p, "Project")), environments.get(environment.casefold(), environment),
                                       tuple(sorted(components)), status, bool(p.get("Hotfix", {}).get("checkbox")),
                                       (p.get("Issue Count", {}).get("number") or 0) > 0))

    @property
    def undated(self):
        return sum(r.day is None for r in self.records)

    @staticmethod
    def interval(kind, day):
        if kind == "month":
            return day.replace(day=1), day.replace(day=monthrange(day.year, day.month)[1])
        beginning = day - timedelta(days=day.weekday())
        return beginning, beginning + timedelta(days=6)

    def periods(self, kind):
        days = {r.day for r in self.records if r.day}
        days.add(self.today)
        days.add(self.today.replace(day=1) - timedelta(days=1) if kind == "month" else self.today - timedelta(days=7))
        for job in self.jobs:
            first = date.fromisoformat(job["request"]["start_date"])
            last = date.fromisoformat(job["request"]["end_date"])
            while first <= last:
                days.add(first)
                first += timedelta(days=1)
        return sorted({self.interval(kind, d) for d in days}, reverse=True)

    def within(self, start, end):
        return [r for r in self.records if r.day is not None and start <= r.day <= end]

    @staticmethod
    def counts(records):
        return {"count": len(records), "verified": sum(r.status == "Completed" for r in records),
                "hotfix": sum(r.hotfix for r in records), "issues": sum(r.issue for r in records)}

    @staticmethod
    def ranking(records, dimension):
        counter = Counter()
        for r in records:
            if dimension == "component":
                # One deployment can belong to FE+BO. Native subtypes roll up once per deployment.
                counter.update({"NATIVE" if c.startswith("NATIVE") else c for c in r.components})
            elif dimension == "component_detail":
                counter.update(set(r.components))
            else:
                counter.update([getattr(r, dimension)])
        return sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold()))

    def winner(self, records, dimension):
        ranked = self.ranking(records, dimension)
        known = [(name, count) for name, count in ranked if name not in {UNKNOWN, "ETC"}]
        ranked = known or ranked
        if not ranked:
            return "—"
        names = [name for name, count in ranked if count == ranked[0][1]]
        return " / ".join(names) + f" ({ranked[0][1]}건)"

    def coverage(self, start, end):
        overlapping = [j for j in self.jobs if date.fromisoformat(j["request"]["start_date"]) <= end and date.fromisoformat(j["request"]["end_date"]) >= start]
        if any(j["state"] == "Completed" and date.fromisoformat(j["request"]["start_date"]) <= start and date.fromisoformat(j["request"]["end_date"]) >= end for j in overlapping):
            return "기간 수집 완료"
        pending = [j for j in overlapping if j["state"] != "Completed" and date.fromisoformat(j["cursor"]) <= end]
        if pending:
            return "쿼터 대기 / 수집 중" if any(j["state"] == "WaitingQuota" for j in pending) else "과거 수집 중"
        return "진행 중인 기간" if start <= self.today <= end else "현재 확인 데이터"
