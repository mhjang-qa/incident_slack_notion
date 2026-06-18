import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from incident_slack_notion.models import SlackMessage
from incident_slack_notion.parser import (
    apply_thread,
    format_duration,
    is_incident_candidate,
    is_recovery_message,
    parse_incident,
)

KST = ZoneInfo("Asia/Seoul")


def message(text: str, posted_at: datetime, ts: str = "1.0") -> SlackMessage:
    return SlackMessage(
        ts=ts,
        thread_ts="1.0",
        user_id="U1",
        author="테스터",
        text=text,
        permalink="https://example.slack.com/message",
        posted_at=posted_at,
        is_thread_reply=ts != "1.0",
    )


class ParserTest(unittest.TestCase):
    def test_parse_incident_message(self) -> None:
        source = message(
            "[오픈뱅킹 기업은행 타임아웃]\n"
            "12:01:11 부터 타임아웃 발생중으로 모니터링 중입니다.\n"
            "한패스 서비스에는 영향 없습니다.\nCS 대응 참고 바랍니다.",
            datetime(2026, 6, 7, 12, 5, tzinfo=KST),
        )
        incident = parse_incident(source)

        self.assertEqual(incident.title, "[오픈뱅킹] 기업은행 타임아웃")
        self.assertEqual(
            incident.occurred_at, datetime(2026, 6, 7, 12, 1, 11, tzinfo=KST)
        )
        self.assertEqual(incident.status, "모니터링 중")
        self.assertEqual(incident.impact, "한패스 서비스 영향 없음")
        self.assertIn("CS 대응 참고", incident.details)

    def test_explicit_duration_is_preferred(self) -> None:
        root = message(
            "[오픈뱅킹 기업은행 타임아웃]\n12:01:11부터 장애 발생",
            datetime(2026, 6, 7, 12, 2, tzinfo=KST),
        )
        recovery = message(
            "[오픈뱅킹 기업은행 정상화]\n"
            "장애시간: 12:01:11 ~ 15:13:13 (약 3시간 12분간)",
            datetime(2026, 6, 7, 15, 14, tzinfo=KST),
            "2.0",
        )
        incident = apply_thread(parse_incident(root), [root, recovery])

        self.assertEqual(incident.status, "정상화")
        self.assertEqual(incident.duration_minutes, 192)
        self.assertEqual(incident.duration_text, "약 3시간 12분")
        self.assertEqual(
            incident.recovered_at, datetime(2026, 6, 7, 15, 13, 13, tzinfo=KST)
        )

    def test_duration_across_midnight(self) -> None:
        root = message(
            "[결제 서비스 오류]\n23:50:00부터 오류 발생",
            datetime(2026, 6, 7, 23, 51, tzinfo=KST),
        )
        recovery = message(
            "[결제 정상화]\n장애시간: 23:50:00 ~ 00:20:00\n복구 완료",
            datetime(2026, 6, 8, 0, 21, tzinfo=KST),
            "2.0",
        )
        incident = apply_thread(parse_incident(root), [root, recovery])

        self.assertEqual(incident.duration_minutes, 30)
        self.assertEqual(
            incident.recovered_at, datetime(2026, 6, 8, 0, 20, tzinfo=KST)
        )

    def test_keywords_and_duration_format(self) -> None:
        self.assertTrue(is_incident_candidate("거래 지연 모니터링 중"))
        self.assertTrue(is_recovery_message("조치 완료되었습니다"))
        self.assertEqual(format_duration(50), "약 50분")
        self.assertEqual(format_duration(100), "약 1시간 40분")


if __name__ == "__main__":
    unittest.main()
