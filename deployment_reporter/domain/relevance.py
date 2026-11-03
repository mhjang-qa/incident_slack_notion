"""Rules supply retrieval evidence, never the final semantic classification."""
import re

from .models import Evidence

STOP = set("공지 배포 서버 작업 완료 예정 내역 추가 적용 변경 수정 확인 요청 오늘 시간 일시 버전 관련 WEB web 약 분 예상 서비스 프로젝트".split())
TOKENS = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9_-]{1,}")
VERSION = re.compile(r"(?i)\bv?\d+\.\d+(?:\.\d+)?(?:[-+][\w.]+)?\b")
RULES = {
    "NATIVE-AOS": r"(?i)\b(?:AOS|Android|playstore)\b|안드로이드|플레이스토어",
    "NATIVE-IOS": r"(?i)\b(?:iOS|appstore)\b|앱스토어",
    "NATIVE": r"(?i)\bSDK\b|네이티브|앱\s*(?:업데이트|심사|버전)",
    "FE": r"(?i)\b(?:WEB|WebView|React|Vue|JavaScript|HTML|CSS|UI)\b|버튼|웹페이지|화면",
    "BE": r"(?i)\b(?:API|Backend|DB|Batch|Scheduler|Endpoint)\b|비즈니스\s*로직|인증",
    "BO": r"(?i)(?<![a-z0-9])(?:BO|Admin|Back\s*Office)(?![a-z0-9])|관리자|운영자|관리\s*(?:기능|페이지)",
}


def rule_hints(text):
    return {name: list(dict.fromkeys(re.findall(pattern, text))) for name, pattern in RULES.items() if re.search(pattern, text)}


def tokens(text):
    return {t.casefold() for t in TOKENS.findall(text) if t not in STOP and not VERSION.fullmatch(t)}


def score(root, message, aliases, threshold=.5):
    if message.key == root.key:
        return Evidence(message=message, message_type="Announcement", relevance_score=1, included=True, reasons=["root"])
    thread = message.thread_ts == (root.thread_ts or root.ts)
    reasons = []
    value = 0.0
    if thread:
        value = 1.0
        reasons.append("same thread")
    else:
        # A different thread is explicitly owned by another conversation.
        if message.thread_ts and message.thread_ts != message.ts:
            return Evidence(message=message, message_type="Other", relevance_score=0, included=False, reasons=["different thread"])
        a, b = set(VERSION.findall(root.text)), set(VERSION.findall(message.text))
        if a and b and not a.intersection(b):
            return Evidence(message=message, message_type="Other", relevance_score=0, included=False, reasons=["different version"])
        if a.intersection(b):
            value += .7
            reasons.append("same version")
        for names in aliases.values():
            if any(n.casefold() in root.text.casefold() for n in names) and any(n.casefold() in message.text.casefold() for n in names):
                value += .45
                reasons.append("same service")
                break
        overlap = tokens(root.text) & tokens(message.text)
        if overlap:
            value += min(.65, .3 * len(overlap))
            reasons.append("keywords: " + ", ".join(sorted(overlap)))
        # Time/user alone cannot make a message relevant.
        if value > 0:
            if message.user and message.user == root.user:
                value += .05
            if float(message.ts) - float(root.ts) <= 3600:
                value += .05
    kind = "Thread" if thread else "Follow-up"
    if re.search(r"장애|오류|실패|노출되지|안\s*됩니다|에러|\bbug\b", message.text, re.I):
        kind = "Issue"
    elif re.search(r"\bQA\b|테스트|검증", message.text, re.I):
        kind = "QA"
    return Evidence(message=message, message_type=kind, relevance_score=min(1, value), included=value >= threshold, reasons=reasons)
