from .schema import rich, text_blocks, schema
from ..http import APIError

MARKER = "Managed by Deployment Reporter / schema-v1"
REPORT_INTRO = "📊 Summary: 월간·주간 통계와 배포가 많은 서비스·프로젝트·영역. 📋 배포 이력: 개별 배포의 상세 내용. 🗃 Raw Data: 원문과 분류 근거."


def bootstrap(client, repo, parent):
    if not parent:
        raise APIError("NOTION_PAGE_ID (QA parent page) is required")
    key = "notion_layout:" + parent
    state = repo.get_value(key, {})
    # Only a page carrying our ownership marker can be adopted after local state loss.
    if not state.get("root"):
        for child in client.children(parent):
            if child["type"] == "child_page" and child["child_page"]["title"] == "Deployment Report":
                children = client.children(child["id"])
                if any(MARKER == "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in b.get("paragraph", {}).get("rich_text", [])) for b in children):
                    if state.get("root"):
                        raise APIError("Multiple managed Deployment Report roots; manual review required")
                    state["root"] = child["id"]
        if not state.get("root"):
            # Do not adopt an unrelated page with the same title.
            page = client.call("POST", "/pages", {"parent": {"page_id": parent}, "properties": {"title": {"title": rich("Deployment Report")}}, "children": text_blocks(MARKER) + text_blocks(REPORT_INTRO)}, retry_safe=False)
            state["root"] = page["id"]
        repo.set_value(key, state)
    if not state.get("lowdata"):
        page = client.child_page(state["root"], "lowdata", text_blocks(MARKER))
        state["lowdata"] = page["id"]
        repo.set_value(key, state)
    summary_schema, raw_schema = schema()
    for name, title, properties in (("summary", "📋 배포 이력", summary_schema), ("raw", "🗃 Raw Data", raw_schema)):
        if name not in state:
            found = None
            for child in client.children(state["root"]) + client.children(state["lowdata"]):
                if child["type"] == "child_database" and child["child_database"]["title"] in ({title, "📊 Summary"} if name == "summary" else {title}):
                    db = client.call("GET", f'/databases/{child["id"]}')
                    description = "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in db.get("description", []))
                    if description == MARKER:
                        if found:
                            raise APIError("Multiple managed databases; review required")
                        found = db
            db = found or client.call("POST", "/databases", {"parent": {"type": "page_id", "page_id": state["lowdata"]}, "title": rich(title), "description": rich(MARKER), "is_inline": True, "initial_data_source": {"properties": properties}}, retry_safe=False)
            if not db.get("data_sources"):
                db = client.call("GET", f'/databases/{db["id"]}')
            if len(db.get("data_sources", [])) != 1:
                raise APIError("Expected one managed Notion data source")
            state[name] = {"database": db["id"], "source": db["data_sources"][0]["id"], "url": db.get("url", "https://www.notion.so/" + db["id"].replace("-", ""))}
            repo.set_value(key, state)
        db = client.call("GET", "/databases/" + state[name]["database"])
        if db.get("parent", {}).get("page_id") != state["lowdata"]:
            if "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in db.get("description", [])) != MARKER:
                raise APIError("Database ownership marker missing; move refused")
            client.call("PATCH", "/databases/" + state[name]["database"], {"parent": {"type": "page_id", "page_id": state["lowdata"]}})
        actual = client.call("GET", f'/data_sources/{state[name]["source"]}')["properties"]
        for prop, spec in properties.items():
            expected = next(iter(spec))
            if prop not in actual or actual[prop].get("type") != expected:
                raise APIError(f"Notion property mismatch: {name}.{prop} expected {expected}; restore schema before retry")
    return state
