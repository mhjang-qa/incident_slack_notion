import hashlib
from ..http import APIError, Transport
from .schema import rich


class NotionClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.http = transport or Transport(retries=settings.max_retries)

    def call(self, method, path, payload=None, retry_safe=True):
        token = self.settings.notion_token.get_secret_value()
        if not token:
            raise APIError("NOTION_TOKEN is required")
        return self.http.request(method, "https://api.notion.com/v1" + path,
                                 headers={"Authorization": f"Bearer {token}", "Notion-Version": self.settings.notion_version},
                                 **({"json": payload} if payload is not None else {}), retry_safe=retry_safe)

    def children(self, page_id):
        result, cursor, seen = [], "", set()
        while True:
            data = self.call("GET", f"/blocks/{page_id}/children?page_size=100" + ("&start_cursor=" + cursor if cursor else ""))
            result.extend(data["results"])
            if not data.get("has_more"):
                return result
            cursor = data.get("next_cursor")
            if not cursor or cursor in seen:
                raise APIError("Notion pagination incomplete")
            seen.add(cursor)

    def find(self, source_id, property_name, value):
        data = self.call("POST", f"/data_sources/{source_id}/query", {"filter": {"property": property_name, "rich_text": {"equals": value}}, "page_size": 2})
        if len(data["results"]) > 1:
            raise APIError("Duplicate Notion keys detected; manual review needed")
        return data["results"][0] if data["results"] else None

    def upsert(self, source_id, key_property, key, properties):
        page = self.find(source_id, key_property, key)
        if page:
            return self.call("PATCH", f'/pages/{page["id"]}', {"properties": properties})
        return self.call("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": source_id}, "properties": properties}, retry_safe=False)

    def child_page(self, parent, title, blocks):
        matches = [x for x in self.children(parent) if x["type"] == "child_page" and x["child_page"]["title"] == title]
        if len(matches) > 1:
            raise APIError("Duplicate managed revision pages; review manually")
        if matches:
            return {"id": matches[0]["id"], "url": "https://www.notion.so/" + matches[0]["id"].replace("-", "")}
        return self.call("POST", "/pages", {"parent": {"page_id": parent}, "properties": {"title": {"title": rich(title)}}, "children": blocks}, retry_safe=False)

    def content_pages(self, parent, prefix, blocks):
        # Each chunk is created atomically and recovered by deterministic child title.
        # The hash prevents silently reusing partial/mismatched content after a crash.
        import json
        fingerprint = hashlib.sha256(json.dumps(blocks, sort_keys=True).encode()).hexdigest()[:12]
        pages = []
        for start in range(0, len(blocks), 80):
            pages.append(self.child_page(parent, f"{prefix} · {fingerprint} · {start // 80 + 1}", blocks[start:start + 80]))
        return pages

    def current_sections(self, page_id, sections):
        children = self.children(page_id)
        for label, text in sections.items():
            marker = label + " · Deployment Reporter\n"
            value = str(text or "미확인")
            if len(value) > 1800:
                value = value[:1800] + "\n전체 내용은 Notion Detail에서 확인하세요."
            content = {"rich_text": rich(marker + value)}
            matches = [b for b in children if b["type"] == "callout" and "".join(t.get("plain_text", t.get("text", {}).get("content", "")) for t in b["callout"]["rich_text"]).startswith(marker)]
            if len(matches) > 1:
                raise APIError("Duplicate managed overview sections; manual review required")
            if matches:
                self.call("PATCH", f'/blocks/{matches[0]["id"]}', {"callout": content})
            else:
                # Recover an ambiguous append by finding its marker on the next attempt.
                self.call("PATCH", f"/blocks/{page_id}/children", {"children": [{"object": "block", "type": "callout", "callout": content}]}, retry_safe=False)

    def test(self):
        if not self.settings.notion_page_id:
            raise APIError("NOTION_PAGE_ID (QA parent page) is required")
        self.call("GET", f"/pages/{self.settings.notion_page_id}")
        return {"ok": True, "note": "QA parent page read only; write capability not tested"}
