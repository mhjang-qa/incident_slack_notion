import re

TRIGGER = re.compile(r"\[\s*공\s*지\s*\]", re.I)


def is_candidate(text: str) -> bool:
    return bool(TRIGGER.search(text))


def candidates(messages):
    return [m for m in messages if is_candidate(m.text) and (not m.thread_ts or m.thread_ts == m.ts)]
