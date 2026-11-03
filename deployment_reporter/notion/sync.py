import json
import logging
from ..domain.models import DeploymentReport, Evidence
from ..storage.repository import digest, evidence_digest
from .bootstrap import bootstrap
from .schema import summary_properties, rt, rich, select, detail_blocks, text_blocks

LOG = logging.getLogger(__name__)


class NotionSync:
    def __init__(self, client, repo, settings):
        self.client, self.repo, self.settings = client, repo, settings

    def sync(self, event_id):
        event = self.repo.event(event_id)
        analyses = self.repo.analyses(event_id)
        if not analyses:
            raise ValueError("Analyze before syncing")
        analysis = analyses[0]
        evidence = self.repo.evidence(event_id)
        if analysis["fingerprint"] != evidence_digest(evidence):
            raise ValueError("Raw messages changed since analysis; analyze the current evidence before syncing")
        report = DeploymentReport.model_validate_json(analysis["report"])
        if not report.is_deployment and not event["notion_page_id"]:
            self.repo.update(event_id, sync_state="Skipped")
            return None
        layout = bootstrap(self.client, self.repo, self.settings.notion_page_id)
        props = summary_properties(event, report, evidence, self.settings.timezone)
        source_key = f'{event["workspace"]}:{event["channel"]}:{event["ts"]}'
        LOG.info("[Notion] Summary upsert event=%s", event_id)
        page = self.client.upsert(layout["summary"]["source"], "Source Key", source_key, props)
        self.repo.update(event_id, notion_page_id=page["id"])
        # Immutable raw revisions remain separately addressable; excluded messages are also retained.
        for row in self.repo.raw(event_id):
            item = Evidence.model_validate_json(row["data"])
            msg = item.message
            # Inclusion may change on reanalysis; preserve the original message/collection time.
            current = next((e for e in evidence if e.message.key == msg.key and e.message.text == msg.text and e.message.payload == msg.payload), None)
            if current:
                item = item.model_copy(update={"included": current.included, "relevance_score": current.relevance_score, "message_type": current.message_type})
            record_key = digest([source_key, row["message_key"], row["revision"]])
            raw_props = {"Name": {"title": rich(event_id + " · " + msg.ts)},
                         "Record Key": rt(record_key), "Deployment ID": rt(event_id), "Slack Channel": rt(msg.channel),
                         "Slack Message ID": rt(msg.message_id or msg.ts), "Slack Timestamp": rt(msg.ts),
                         "Thread TS": rt(msg.thread_ts), "User": rt(msg.user), "Revision": rt(row["revision"]),
                         # A property is a short preview; full unmodified message/payload lives in content pages.
                         "Message": rt(msg.text[:1800]), "Slack URL": {"url": msg.url or None}, "Summary": {"url": page["url"]},
                         "Message Type": select(item.message_type), "Relevance Score": {"number": item.relevance_score},
                         "LLM Included": {"checkbox": item.included}, "Collected At": {"date": {"start": msg.collected_at}}}
            raw_page = self.client.upsert(layout["raw"]["source"], "Record Key", record_key, raw_props)
            self.client.content_pages(raw_page["id"], "Slack original", text_blocks(msg.text) + text_blocks(json.dumps(msg.payload, ensure_ascii=False, indent=2)))
        LOG.info("[Notion] Raw Data synced event=%s", event_id)
        # Deterministic analysis content identity survives restart and local analysis-row renumbering.
        revision = digest([analysis["fingerprint"], analysis["report"], analysis["model"], analysis["created_at"]])[:16]
        detail = self.client.content_pages(page["id"], "Analysis " + revision, detail_blocks(report, evidence, event_id, layout["raw"]["url"]) + text_blocks("Gemini response / " + analysis["model"] + " / " + analysis["prompt_version"]) + text_blocks(analysis["response"]))
        self.client.current_sections(page["id"], {
            "배포 요약": report.summary, "주요 변경사항": "\n".join(report.changes),
            "버전 정보": f'{report.service or "미확인"} / {report.environment or "미확인"} / {report.version or "미확인"} / {report.build or "미확인"}',
            "기술 영역": " + ".join(c.value for c in report.effective_components) + "\n" + report.component_reason + f"\n{report.review_status} ({report.classification_confidence:.2f})",
            "배포 결과": report.result, "배포 후 Issue": "\n".join(report.issues), "QA 관련 내용": "\n".join(report.qa_related),
            "Slack 원문": "\n".join(e.message.url for e in evidence if e.included and e.message.url),
            "Raw Data 및 분석 이력": f'{event_id}\nRaw Data: {layout["raw"]["url"]}\n최신 분석: {detail[0]["url"]}',
        })
        self.client.call("PATCH", f'/pages/{page["id"]}', {"properties": {"Notion Detail": {"url": detail[0]["url"]}}})
        self.repo.update(event_id, sync_state="Synced", notion_revision=analysis["id"], error=None)
        self.repo.log("Notion", "Summary + Raw Data synced", event_id)
        return page["url"]
