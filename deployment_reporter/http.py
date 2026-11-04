"""Bounded API retries; ambiguous remote creates are recovered by lookup."""
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import httpx
from urllib.parse import urlparse


class APIError(RuntimeError):
    pass


class RateLimitError(APIError):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


def retry_delay(value, attempt):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return min(2 ** attempt, 30)


class Transport:
    def __init__(self, client=None, retries=3, sleep=time.sleep):
        self.client = client or httpx.Client(timeout=45)
        self.retries = retries
        self.sleep = sleep

    def request(self, method, url, *, retry_safe=True, **kwargs):
        for attempt in range(self.retries):
            try:
                response = self.client.request(method, url, **kwargs)
            except httpx.TransportError:
                if not retry_safe or attempt + 1 == self.retries:
                    raise APIError("Network failure; retry this event (remote result may need recovery)") from None
                self.sleep(min(2 ** attempt, 30))
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if response.status_code == 429 and urlparse(url).hostname == "generativelanguage.googleapis.com":
                    try:
                        details = response.json().get("error", {}).get("details", [])
                        daily = any("perday" in str(v.get("quotaId", "")).lower()
                                    for item in details for v in item.get("violations", []))
                        waits = [float(item["retryDelay"].removesuffix("s")) for item in details
                                 if item.get("@type", "").endswith("RetryInfo") and isinstance(item.get("retryDelay"), str)]
                        wait = max(waits) if waits else 3600
                        if daily:
                            raise RateLimitError("Gemini daily quota exhausted; retained for automatic retry after reset", max(60, wait))
                    except (ValueError, TypeError, AttributeError):
                        pass
                # A 429 explicitly rejected a request; never repeat ambiguous creates after 5xx.
                if (retry_safe or response.status_code == 429) and attempt + 1 < self.retries:
                    delay = response.headers.get("Retry-After")
                    # Gemini commonly sends google.rpc.RetryInfo in JSON, without Retry-After.
                    if response.status_code == 429 and not delay and "generativelanguage.googleapis.com" == urlparse(url).hostname:
                        try:
                            details = response.json().get("error", {}).get("details", [])
                            delays = [float(item["retryDelay"].removesuffix("s")) for item in details
                                      if item.get("@type", "").endswith("RetryInfo") and isinstance(item.get("retryDelay"), str)]
                            delay = max(delays) if delays else None
                        except (ValueError, TypeError, AttributeError):
                            delay = None
                    seconds = retry_delay(delay, attempt)
                    if response.status_code == 429 and seconds > 60:
                        raise RateLimitError("API rate limit; retained for a later automatic retry", seconds)
                    self.sleep(seconds)
                    continue
            if response.is_error:
                # Never include provider bodies, request URLs (keys), or tokens in errors/logs.
                host = urlparse(url).hostname
                provider = {"slack.com": "Slack", "api.notion.com": "Notion", "generativelanguage.googleapis.com": "Gemini"}.get(host, "API")
                raise APIError(f"API HTTP {response.status_code} ({provider}); check credentials, permissions, schema and retry")
            return response.json() if response.content else {}
        raise APIError("API retry limit reached")
