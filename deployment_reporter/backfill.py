"""Durable calendar-date backfills share the normal source keys and Notion destination."""
from datetime import date, timedelta
import time

from .domain.period import DateRange
from .storage.repository import digest


class Backfills:
    def __init__(self, reporter):
        self.reporter = reporter
        self.repo = reporter.repo

    def create(self, start_date, end_date, *, channels=None, sync=True):
        period = DateRange(start_date=start_date, end_date=end_date).require_past(self.reporter.settings.timezone)
        selected = sorted(set(channels if channels is not None else self.reporter.settings.channels))
        if not selected:
            raise ValueError("과거 수집 채널을 선택하세요")
        request = {**period.model_dump(mode="json"), "channels": selected, "sync": sync, "timezone": self.reporter.settings.timezone}
        job_id = "BF-" + period.start_date.strftime("%Y%m%d") + "-" + period.end_date.strftime("%Y%m%d") + "-" + digest(request)[:8]
        with self.repo.lock():
            return self.repo.create_backfill(job_id, request)

    def run(self, job_id, *, max_days=3, max_events=10, budget_seconds=600, clock=None):
        if max_days < 1 or max_events < 1 or budget_seconds <= 0:
            raise ValueError("Backfill limits must be positive")
        deadline = time.monotonic() + budget_seconds
        remaining = max_events
        for _ in range(max_days):
            job = self.repo.backfill(job_id)
            if job["state"] == "Completed" or time.monotonic() >= deadline:
                return job
            req = job["request"]
            if req["timezone"] != self.reporter.settings.timezone:
                raise ValueError("과거 수집 작업의 시간대와 현재 설정이 다릅니다")
            day = date.fromisoformat(job["cursor"])
            results = self.reporter.batch(start_date=day, end_date=day, channels=req["channels"],
                                          sync=req["sync"], backfill_job_id=job_id,
                                          max_events=remaining, deadline=deadline, clock=clock)
            remaining -= sum(not r.get("unchanged") and not r.get("pending") and "id" in r for r in results)
            failed = sum("error" in r and not r.get("deferred") for r in results)
            deferred = sum(bool(r.get("deferred")) for r in results)
            pending = sum(bool(r.get("pending")) for r in results)
            stats = {"day": day.isoformat(), "failed": failed, "deferred": deferred, "pending": pending}
            if failed or deferred or pending:
                state = "Retrying" if failed else "WaitingQuota" if deferred else "Running"
                with self.repo.lock():
                    self.repo.update_backfill(job_id, day.isoformat(), state, stats)
                return self.repo.backfill(job_id)
            following = day + timedelta(days=1)
            state = "Completed" if following > date.fromisoformat(req["end_date"]) else "Running"
            with self.repo.lock():
                self.repo.update_backfill(job_id, following.isoformat(), state, stats)
            if state == "Completed":
                return self.repo.backfill(job_id)
            if remaining <= 0:
                return self.repo.backfill(job_id)
        return self.repo.backfill(job_id)

    def run_pending(self, **limits):
        # The normal live batch runs first; historical work consumes the remaining budget.
        job = next((j for j in self.repo.backfills() if j["state"] != "Completed"), None)
        return self.run(job["id"], **limits) if job else None


def progress_text(jobs):
    labels = {"Pending": "접수", "Running": "수집 중", "Retrying": "오류 재시도 대기", "WaitingQuota": "Gemini 쿼터 회복 대기", "Completed": "완료"}
    return "\n\n".join(f'{j["request"]["start_date"]} ~ {j["request"]["end_date"]} · {labels[j["state"]]}\n'
                        f'현재까지 발견한 후보 {j["discovered"]}건 중 {j["processed"]}건 처리 완료 · '
                        + ('전체 기간 확인 완료' if j['state']=='Completed' else f'다음 확인 날짜 {j["cursor"]}')
                        for j in jobs[-10:])
