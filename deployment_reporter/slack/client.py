from ..domain.models import SlackMessage
from ..http import APIError, Transport


class SlackClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.http = transport or Transport(retries=settings.max_retries)

    def call(self, method, **params):
        token = self.settings.slack_bot_token.get_secret_value()
        if method == "conversations.replies":
            token = self.settings.slack_thread_token.get_secret_value() or token
        if not token:
            raise APIError("SLACK_BOT_TOKEN is required")
        data = self.http.request("GET", "https://slack.com/api/" + method, headers={"Authorization": f"Bearer {token}"}, params=params)
        if not data.get("ok"):
            code = data.get("error", "unknown_error")
            # Slack error codes, not arbitrary response text.
            allowed = {"invalid_auth", "missing_scope", "not_in_channel", "channel_not_found", "ratelimited", "token_revoked", "not_allowed_token_type", "thread_not_found"}
            raise APIError("Slack " + (code if code in allowed else "API failure"))
        return data

    def pages(self, method, key, **params):
        result = []
        cursor = ""
        seen = set()
        while True:
            data = self.call(method, limit=100, cursor=cursor, **params)
            result.extend(data.get(key, []))
            cursor = data.get("response_metadata", {}).get("next_cursor", "").strip()
            if not cursor:
                if data.get("has_more"):
                    raise APIError("Slack pagination incomplete; no cursor returned")
                return result
            if cursor in seen:
                raise APIError("Slack pagination repeated cursor")
            seen.add(cursor)

    def convert(self, channel, item):
        # Keep the complete API payload, including edited/blocks metadata, untouched.
        return SlackMessage(channel=channel, ts=item["ts"], text=item.get("text", ""), user=item.get("user", item.get("bot_id", "")), thread_ts=item.get("thread_ts"), message_id=item.get("client_msg_id"), payload=item)

    def history(self, channel, oldest, latest):
        items = self.pages("conversations.history", "messages", channel=channel, oldest=str(oldest), latest=str(latest), inclusive="true")
        return sorted([self.convert(channel, x) for x in items if "ts" in x and x.get("subtype") not in {"message_deleted", "channel_join", "channel_leave"}], key=lambda m: float(m.ts))

    def thread(self, root, latest):
        items = self.pages("conversations.replies", "messages", channel=root.channel, ts=root.thread_ts or root.ts, latest=str(latest), inclusive="true")
        return [self.convert(root.channel, x) for x in items if "ts" in x]

    def permalink(self, message):
        return self.call("chat.getPermalink", channel=message.channel, message_ts=message.ts)["permalink"]

    def channels(self):
        return self.pages("conversations.list", "channels", types="public_channel,private_channel", exclude_archived="true")

    def test(self):
        data = self.call("auth.test")
        expected = self.settings.slack_workspace.rstrip("/")
        if expected and expected not in {data.get("team_id"), data.get("team"), data.get("url", "").rstrip("/")}:
            raise APIError("SLACK_WORKSPACE differs from authenticated workspace")
        return {"ok": True, "team": data.get("team"), "team_id": data.get("team_id")}
