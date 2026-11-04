from datetime import date, datetime
import re
from zoneinfo import ZoneInfo

from ..domain.statistics import Statistics, plain
from ..http import APIError
from ..storage.repository import digest
from .bootstrap import MARKER, REPORT_INTRO, bootstrap
from .schema import rich, text_blocks, block

STATS_MARKER = "Managed by Deployment Reporter / statistics-v1"


def notion_url(page_id):
    return "https://www.notion.so/" + page_id.replace("-", "")


def cells(values):
    result = []
    for value in values:
        if isinstance(value, dict):
            result.append([{"type": "text", "text": {"content": str(value["text"])[:1900], "link": {"url": value["url"]}}}])
        else:
            result.append(rich(str(value)[:1900]))
    return result


def cell_signature(values):
    return [[(p.get("plain_text", p.get("text", {}).get("content", "")),
              (p.get("text", {}).get("link") or {}).get("url", p.get("href"))) for p in value] for value in values]


def upsert_table(client, page_id, label, headers, rows):
    """Only modify the table adjacent to our reserved heading on an owned statistics page."""
    heading = label + " · Deployment Reporter"
    children = client.children(page_id)
    found = [i for i, b in enumerate(children) if b["type"] == "heading_2" and plain(b["heading_2"]["rich_text"]) == heading]
    if len(found) > 1:
        raise APIError("Duplicate managed statistics headings; review manually")
    desired = [cells(headers)] + [cells(row) for row in rows]
    if not found:
        # Create the header first; recover its marker if a response is lost.
        client.call("PATCH", f"/blocks/{page_id}/children", {"children": [block("heading_2", heading)]}, retry_safe=False)
        return upsert_table(client, page_id, label, headers, rows)
    index = found[0]
    if index + 1 < len(children) and children[index + 1]["type"] == "table":
        table = children[index + 1]
        if table["table"]["table_width"] != len(headers):
            raise APIError("Managed statistics table width mismatch; source records were not changed")
    else:
        # A missing table is recoverable after the heading-only checkpoint.
        response = client.call("PATCH", f"/blocks/{page_id}/children", {
            "after": children[index]["id"], "children": [{"object": "block", "type": "table", "table": {
                "table_width": len(headers), "has_column_header": True, "has_row_header": False,
                "children": [{"object": "block", "type": "table_row", "table_row": {"cells": desired[0]}}]}}]}, retry_safe=False)
        table = response["results"][0]
    current = client.children(table["id"])
    for old, wanted in zip(current, desired):
        if old["type"] != "table_row":
            raise APIError("Unexpected statistics table content; review manually")
        if cell_signature(old["table_row"]["cells"]) != cell_signature(wanted):
            client.call("PATCH", f'/blocks/{old["id"]}', {"table_row": {"cells": wanted}})
    for offset in range(len(current), len(desired), 100):
        extra = [{"object": "block", "type": "table_row", "table_row": {"cells": row}} for row in desired[offset:offset + 100]]
        client.call("PATCH", f'/blocks/{table["id"]}/children', {"children": extra}, retry_safe=False)
    # Removed aggregate rows are generated output only; no deployment records are archived.
    for old in current[len(desired):]:
        client.call("DELETE", f'/blocks/{old["id"]}')


class StatisticsSync:
    def __init__(self, client, repo, settings):
        self.client, self.repo, self.settings = client, repo, settings

    def owned_page(self, parent, title, scope):
        key = "statistics_page:" + parent + ":" + scope
        cached = self.repo.get_value(key)
        if cached:
            return cached
        matches = []
        for b in self.client.children(parent):
            if b["type"] == "child_page" and b["child_page"]["title"] == title:
                if any(x["type"] == "paragraph" and plain(x["paragraph"]["rich_text"]) == STATS_MARKER for x in self.client.children(b["id"])):
                    matches.append(b["id"])
        if len(matches) > 1:
            raise APIError("Duplicate managed statistics pages; review manually")
        if matches:
            page_id = matches[0]
        else:
            page = self.client.call("POST", "/pages", {"parent": {"page_id": parent},
                                    "properties": {"title": {"title": rich(title)}}, "children": text_blocks(STATS_MARKER)}, retry_safe=False)
            page_id = page["id"]
        self.repo.set_value(key, page_id)
        return page_id

    def prepare(self, layout):
        database_id = layout["summary"]["database"]
        key = "statistics_history_name:" + database_id
        if not self.repo.get_value(key):
            database = self.client.call("GET", "/databases/" + database_id)
            if plain(database.get("description", [])) != MARKER:
                raise APIError("Deployment history ownership marker missing; rename refused")
            if plain(database.get("title", [])) == "📊 Summary":
                self.client.call("PATCH", "/databases/" + database_id, {"title": rich("📋 배포 이력")})
            source = self.client.call("GET", "/data_sources/" + layout["summary"]["source"])
            if plain(source.get("title", [])) == "📊 Summary":
                self.client.call("PATCH", "/data_sources/" + layout["summary"]["source"], {"title": rich("📋 배포 이력")})
            self.repo.set_value(key, True)
        candidates = self.client.children(layout["root"]) + self.client.children(layout["lowdata"])
        for child in candidates:
            if child["type"] == "child_page" and child["child_page"]["title"] == "📊 Summary" and any(b["type"] == "paragraph" and plain(b["paragraph"]["rich_text"]) == STATS_MARKER for b in self.client.children(child["id"])):
                self.repo.set_value("statistics_page:" + layout["lowdata"] + ":summary", child["id"])
        page_id = self.owned_page(layout["lowdata"], "📊 Summary", "summary")
        parent = self.client.call("GET", "/pages/" + page_id).get("parent", {})
        if parent.get("page_id") != layout["lowdata"]:
            self.client.call("POST", "/pages/" + page_id + "/move", {"parent": {"type": "page_id", "page_id": layout["lowdata"]}}, retry_safe=False)
        self.repo.set_value("statistics_page:" + layout["lowdata"] + ":summary", page_id)
        return page_id

    def root_link(self, root, statistics_page):
        children = self.client.children(root)
        old_intro = "📊 Summary: 배포별 요약과 분석 이력. 🗃 Raw Data: 원문과 분류 근거. QA는 기본 확인 필요이며 배포 완료와 검증 완료는 구분합니다."
        for child in children:
            if child["type"] == "paragraph" and plain(child["paragraph"]["rich_text"]) == old_intro:
                self.client.call("PATCH", f'/blocks/{child["id"]}', {"paragraph": {"rich_text": rich(REPORT_INTRO)}})
        marker = "📊 Summary 통계 · Deployment Reporter\n"
        wanted = rich(marker) + [{"type": "text", "text": {"content": "월간·주간 통계와 배포가 많은 서비스·프로젝트 보기", "link": {"url": notion_url(statistics_page)}}}]
        matches = [b for b in children if b["type"] == "callout" and plain(b["callout"]["rich_text"]).startswith(marker)]
        if len(matches) > 1:
            raise APIError("Duplicate statistics links; review manually")
        if matches:
            if cell_signature([matches[0]["callout"]["rich_text"]]) != cell_signature([wanted]):
                self.client.call("PATCH", f'/blocks/{matches[0]["id"]}', {"callout": {"rich_text": wanted}})
        else:
            payload = {"children": [{"object": "block", "type": "callout", "callout": {"rich_text": wanted}}]}
            owner = next((b for b in children if b["type"] == "paragraph" and plain(b["paragraph"]["rich_text"]) == MARKER), None)
            if owner and owner.get("id"):
                payload["after"] = owner["id"]
            self.client.call("PATCH", "/blocks/" + root + "/children", payload, retry_safe=False)

    def render_rankings(self, page_id, statistics, records):
        total = len(records)
        dimensions = [("서비스별 배포 순위", "service"), ("프로젝트별 배포 순위", "project"),
                      ("기술 영역별 배포 순위", "component"), ("환경별 배포 순위", "environment")]
        for title, dimension in dimensions:
            ranked = statistics.ranking(records, dimension)
            rows = [[i, name, count, f"{count / total:.1%}" if total else "0%"] for i, (name, count) in enumerate(ranked, 1)]
            upsert_table(self.client, page_id, title, ["순위", "구분", "건수", "해당 배포 중 비율"], rows)

    def refresh(self, layout=None, today=None):
        layout = layout or bootstrap(self.client, self.repo, self.settings.notion_page_id)
        summary = self.prepare(layout)
        source = self.client.query_all(layout["summary"]["source"])
        statistics = Statistics(source, self.settings.timezone, self.settings.aliases, today, self.repo.backfills())
        from .dashboard import refresh_dashboard
        refresh_dashboard(self.client, self.repo, layout["root"], layout["lowdata"], statistics, source)
        payload = {"version": 1, "date": statistics.today.isoformat(), "records": [r.payload() for r in statistics.records],
                   "duplicates": statistics.duplicates, "rejected": statistics.rejected,
                   "jobs": [{k: j[k] for k in ("request", "state", "cursor", "discovered", "processed")} for j in statistics.jobs]}
        fingerprint = digest(payload)
        key = "statistics_fingerprint:" + summary
        if self.repo.get_value(key) == fingerprint:
            return {"url": notion_url(layout["root"]), "count": len(statistics.records), "updated": False}
        period_rows = {"month": [], "week": []}
        manifest_key = "statistics_periods:" + summary
        manifest = self.repo.get_value(manifest_key, {"month": [], "week": []})
        if not any(manifest.values()):
            # Recover old period pages after local-state loss so a corrected Date cannot leave stale totals.
            for child in self.client.children(summary):
                if child["type"] != "child_page":
                    continue
                title = child["child_page"]["title"]
                kind = "month" if re.fullmatch(r"월간 \d{4}-\d{2}", title) else "week" if re.match(r"주간 \d{4}-W\d{2} \(", title) else None
                if not kind or not any(b["type"] == "paragraph" and plain(b["paragraph"]["rich_text"]) == STATS_MARKER for b in self.client.children(child["id"])):
                    continue
                try:
                    d = date.fromisoformat(title[3:10] + "-01") if kind == "month" else date.fromisocalendar(int(title[3:7]), int(title[9:11]), 1)
                    a, b = statistics.interval(kind, d)
                    manifest[kind].append([a.isoformat(), b.isoformat()])
                except ValueError:
                    continue
        for kind in ("month", "week"):
            intervals = set(statistics.periods(kind)) | {(date.fromisoformat(a), date.fromisoformat(b)) for a,b in manifest[kind]}
            for start, end in sorted(intervals, reverse=True):
                records = statistics.within(start, end)
                counts = statistics.counts(records)
                name = start.strftime("%Y-%m") if kind == "month" else f'{start.isocalendar().year}-W{start.isocalendar().week:02d}'
                caption = name if kind == "month" else f'{name} ({start:%m/%d}~{end:%m/%d})'
                coverage = statistics.coverage(start, end)
                link = caption
                # Empty historical periods remain visibly pending; create details once data arrives.
                existing = [start.isoformat(), end.isoformat()] in manifest[kind]
                if records or existing or start <= statistics.today <= end:
                    detail = self.owned_page(summary, ("월간 " if kind == "month" else "주간 ") + caption, kind + ":" + name)
                    link = {"text": caption, "url": notion_url(detail)}
                    detail_hash = digest({"version": 1, "records": [r.payload() for r in records], "coverage": coverage})
                    detail_key = "statistics_fingerprint:" + detail
                    if self.repo.get_value(detail_key) != detail_hash:
                        self.client.current_sections(detail, {
                            "기간 통계": f'{start.isoformat()} ~ {end.isoformat()}\n배포 공지 {counts["count"]}건 · 검증 완료 {counts["verified"]}건 · Hotfix {counts["hotfix"]}건 · Issue 배포 {counts["issues"]}건\n{coverage}',
                            "집계 안내": "등록된 배포 이력의 Date 기준이며 예정 공지도 포함합니다. 월은 달력 기준, 주는 월요일~일요일입니다. 복수 기술 영역은 각 영역에 한 건씩 포함됩니다."})
                        self.render_rankings(detail, statistics, records)
                        self.repo.set_value(detail_key, detail_hash)
                    if not existing:
                        manifest[kind].append([start.isoformat(), end.isoformat()])
                period_rows[kind].append([link, counts["count"], counts["verified"], counts["hotfix"], counts["issues"], statistics.winner(records, "service"), coverage])
        month = statistics.within(*statistics.interval("month", statistics.today))
        week = statistics.within(*statistics.interval("week", statistics.today))
        overall = statistics.counts(statistics.records)
        updated_at = datetime.now(ZoneInfo(self.settings.timezone)).strftime("%Y-%m-%d %H:%M %Z")
        self.client.current_sections(summary, {
            "배포 통계": f'전체 등록 배포 {overall["count"]}건 · 이번 달 {len(month)}건 · 이번 주 {len(week)}건\n검증 완료 {overall["verified"]}건 · Hotfix {overall["hotfix"]}건 · Issue 배포 {overall["issues"]}건\n집계 날짜 미확인 {statistics.undated}건',
            "배포가 많은 곳": f'이번 달 서비스: {statistics.winner(month, "service")}\n이번 달 프로젝트: {statistics.winner(month, "project")}\n이번 달 기술 영역: {statistics.winner(month, "component")}\n이번 주 서비스: {statistics.winner(week, "service")}',
            "집계 기준 및 데이터 상태": f'Notion 배포 이력을 원본별 1건으로 집계합니다. 예정 공지도 포함하며 실제 검증 완료와 구분합니다. 날짜 미확인 항목은 월·주간 합계에서 제외하고 전체/분류 통계에는 포함합니다. 과거 수집이 진행 중인 기간의 건수는 현재까지 분석·등록한 데이터입니다.\n기술 영역이 FE+BO이면 각 영역에 1건씩 포함되므로 영역 합계는 전체 건수보다 클 수 있습니다. Native 세부 유형은 NATIVE로 합산합니다.\n비배포 제외 {statistics.rejected}건 · 중복 원본 제외 {statistics.duplicates}건 · 갱신 {updated_at}'})
        headers = ["기간 / 상세 보기", "배포 공지", "검증 완료", "Hotfix", "Issue 배포", "최다 서비스", "데이터 상태"]
        upsert_table(self.client, summary, "월간 통계", headers, period_rows["month"])
        upsert_table(self.client, summary, "주간 통계", headers, period_rows["week"])
        self.render_rankings(summary, statistics, statistics.records)
        self.repo.set_value(manifest_key, manifest)
        self.repo.set_value(key, fingerprint)
        self.repo.set_value("statistics_latest", {"page_id": layout["root"], "detail_page_id": summary, "count": len(statistics.records), "undated": statistics.undated})
        return {"url": notion_url(layout["root"]), "count": len(statistics.records), "updated": True}
