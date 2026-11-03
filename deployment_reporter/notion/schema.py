from ..domain.models import Component, status
from zoneinfo import ZoneInfo


def rich(text):
    text = str(text or "")
    return [{"type": "text", "text": {"content": text[i:i + 1900]}} for i in range(0, len(text), 1900)]


def rt(value):
    parts = rich(value)
    if len(parts) > 100:
        raise ValueError("Notion property exceeds 190000 characters; full original remains in SQLite")
    return {"rich_text": parts}


def select(value):
    return {"select": {"name": str(value)[:100]} if value else None}


def schema():
    summary = {name: {"rich_text": {}} for name in (
        "Source Key", "Service", "Project", "Environment", "Version", "Build", "Title", "Summary", "Changes", "Risk", "Issue", "QA Related", "Component Reason", "Time", "Deployment Type")}
    summary.update({
        "Deployment ID": {"title": {}}, "Date": {"date": {}},
        "Component": {"select": {"options": [{"name": c.value} for c in Component]}},
        "Components": {"multi_select": {"options": [{"name": c.value} for c in Component]}},
        "Status": {"select": {"options": [{"name": x} for x in ("Detected", "Analyzing", "Deployed", "Issue Found", "Hotfix", "Completed", "Unknown", "Rejected")]}},
        "QA Status": {"select": {"options": [{"name": x} for x in ("확인 필요", "Review Required")]}},
        "Classification": {"select": {"options": [{"name": x} for x in ("자동 확정", "확인 권장", "Review Required")]}},
        "Confidence": {"number": {"format": "percent"}}, "Hotfix": {"checkbox": {}},
        "Issue Count": {"number": {"format": "number"}}, "Slack": {"url": {}}, "Notion Detail": {"url": {}},
        "Created At": {"created_time": {}}, "Updated At": {"last_edited_time": {}},
    })
    raw = {name: {"rich_text": {}} for name in ("Record Key", "Deployment ID", "Slack Channel", "Slack Message ID", "Slack Timestamp", "Thread TS", "User", "Message", "Revision")}
    raw.update({"Name": {"title": {}}, "Slack URL": {"url": {}}, "Summary": {"url": {}},
                "Message Type": {"select": {"options": [{"name": x} for x in ("Announcement", "Thread", "Follow-up", "Issue", "QA", "Other")]}},
                "Relevance Score": {"number": {"format": "number"}}, "LLM Included": {"checkbox": {}}, "Collected At": {"date": {}}})
    return summary, raw


def summary_properties(event, report, evidence, timezone):
    root = next((e.message for e in evidence if e.message_type == "Announcement"), evidence[0].message)
    date = report.deployed_at or report.scheduled_at
    date = date.astimezone(ZoneInfo(timezone)) if date else None
    props = {"Deployment ID": {"title": rich(event["id"])},
             "Source Key": rt(f'{event["workspace"]}:{event["channel"]}:{event["ts"]}'),
             "Date": {"date": {"start": date.isoformat()} if date else None},
             "Time": rt(date.strftime("%H:%M") if date else "미확인"),
             "Component": select(report.effective_primary.value if report.effective_primary else None),
             "Components": {"multi_select": [{"name": c.value} for c in report.effective_components]},
             "Status": select(status(report, evidence) if report.is_deployment else "Rejected"),
             "QA Status": select("Review Required" if report.classification_confidence < .7 else "확인 필요"),
             "Classification": select(report.review_status),
             "Confidence": {"number": report.classification_confidence}, "Hotfix": {"checkbox": report.hotfix},
             "Issue Count": {"number": len(report.issues)}, "Slack": {"url": root.url or None}}
    for name, value in {"Service": report.service, "Project": report.project, "Environment": report.environment,
                        "Version": report.version, "Build": report.build, "Title": report.title,
                        "Summary": report.summary, "Changes": "\n".join(report.changes), "Risk": report.risk,
                        "Issue": "\n".join(report.issues), "QA Related": "\n".join(report.qa_related),
                        "Component Reason": report.component_reason, "Deployment Type": report.deployment_type}.items():
        props[name] = rt(value)
    return props


def block(kind, text):
    return {"object": "block", "type": kind, kind: {"rich_text": rich(text)}}


def text_blocks(text):
    value = str(text or "미확인")
    return [block("paragraph", value[i:i + 1900]) for i in range(0, len(value), 1900)]


def detail_blocks(report, evidence, event_id, raw_url):
    sections = {
        "배포 요약": report.summary,
        "주요 변경사항": "\n".join(report.changes),
        "버전 정보": f'{report.service or "미확인"} / {report.environment or "미확인"} / {report.version or "미확인"} / {report.build or "미확인"}',
        "기술 영역 및 판정 근거": " + ".join(c.value for c in report.effective_components) + "\n" + report.component_reason + f"\n신뢰도 {report.classification_confidence:.2f} / {report.review_status}",
        "배포 결과": report.result,
        "배포 후 Issue": "\n".join(report.issues),
        "QA 관련 내용": "\n".join(report.qa_related),
        "Slack 원문": "\n".join(e.message.url for e in evidence if e.included and e.message.url),
        "Raw Data": f"Deployment ID: {event_id}\nRaw Data에서 Deployment ID로 필터링: {raw_url}",
    }
    result = []
    for name, text in sections.items():
        result.append(block("heading_2", name))
        result.extend(text_blocks(text))
    return result
