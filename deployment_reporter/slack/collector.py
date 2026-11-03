import logging
from ..domain.relevance import score, VERSION, tokens
from .detector import is_candidate

LOG = logging.getLogger(__name__)


class Collector:
    def __init__(self, slack, settings):
        self.slack = slack
        self.settings = settings

    def collect(self, root, channel_messages, current_time):
        window_end = float(root.ts) + self.settings.window_hours * 3600
        end = min(window_end, current_time)
        channel_end = end
        stop_reason = "window" if current_time >= window_end else "tracking"
        original_versions = set(VERSION.findall(root.text))
        for msg in sorted(channel_messages, key=lambda x: float(x.ts)):
            if float(root.ts) < float(msg.ts) <= channel_end and (not msg.thread_ts or msg.thread_ts == msg.ts):
                if is_candidate(msg.text):
                    channel_end = float(msg.ts)
                    stop_reason = "next announcement (channel); own thread tracked until window"
                    break
                # New version must also share subject words; unrelated version chatter cannot close it.
                versions = set(VERSION.findall(msg.text))
                if original_versions and versions and not versions.intersection(original_versions) and tokens(root.text).intersection(tokens(msg.text)):
                    channel_end = float(msg.ts)
                    stop_reason = "new version (channel); own thread tracked until window"
                    break
        messages = {root.key: root}
        for msg in channel_messages:
            if float(root.ts) < float(msg.ts) <= channel_end and not (float(msg.ts) == channel_end and channel_end < end):
                messages[msg.key] = msg
        # Do not silently fall back to announcement-only analysis if thread access fails.
        for msg in self.slack.thread(root, end):
            if float(root.ts) < float(msg.ts) <= end:
                messages[msg.key] = msg
            elif msg.key == root.key:
                messages[msg.key] = msg  # retain Slack edits to the root
        evidence = [score(root, msg, self.settings.aliases) for msg in sorted(messages.values(), key=lambda m: float(m.ts))]
        for item in evidence:
            if item.included:
                item.message.url = self.slack.permalink(item.message)
        LOG.info("[Collector] collected=%d included=%d", len(evidence), sum(e.included for e in evidence))
        LOG.info("[Relevance] rule scoring complete")
        if current_time >= window_end:
            stop_reason = "window closed; " + stop_reason
        return evidence, stop_reason
