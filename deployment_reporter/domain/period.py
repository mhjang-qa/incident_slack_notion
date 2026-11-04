from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, model_validator


class DateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid_order(self):
        if self.end_date < self.start_date:
            raise ValueError("종료일은 시작일 이후여야 합니다")
        if (self.end_date - self.start_date).days >= 366:
            raise ValueError("한 번에 최대 366일을 지정할 수 있습니다")
        return self

    def bounds(self, timezone):
        zone = ZoneInfo(timezone)
        start = datetime.combine(self.start_date, time.min, zone).timestamp()
        end = datetime.combine(self.end_date + timedelta(days=1), time.min, zone).timestamp()
        return start, end

    def require_past(self, timezone, clock=None):
        today = datetime.fromtimestamp(clock, ZoneInfo(timezone)).date() if clock is not None else datetime.now(ZoneInfo(timezone)).date()
        if self.end_date >= today:
            raise ValueError("과거 수집 종료일은 어제까지 지정할 수 있습니다")
        return self
