"""Dispatch the configured cloud backfill using the user's existing Git login."""
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlparse

from .domain.period import DateRange
from .http import APIError, Transport


def git_token():
    result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", text=True,
                            capture_output=True, timeout=15, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("password"):
        raise APIError("GitHub 로그인 인증을 확인할 수 없습니다. 자동 실행 이력에서 Run workflow로 기간을 지정하세요.")
    return fields["password"]


class CloudBackfills:
    def __init__(self, timezone, config=None, transport=None, token_provider=git_token):
        self.timezone = timezone
        self.config = config if config is not None else json.loads(Path("config/cloud.json").read_text())
        self.http = transport or Transport()
        self.token_provider = token_provider
        parsed = urlparse(self.config.get("workflow_url", ""))
        match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/actions/workflows/([A-Za-z0-9_.-]+\.ya?ml)", parsed.path)
        if not self.config.get("enabled") or parsed.scheme != "https" or parsed.netloc != "github.com" or not match:
            raise ValueError("클라우드 자동화 설정이 올바르지 않습니다")
        self.owner, self.repo, self.workflow = match.groups()

    def request(self, start_date, end_date):
        period = DateRange(start_date=start_date, end_date=end_date).require_past(self.timezone)
        token = self.token_provider()
        # Dispatch is idempotent at the durable date-range job level, but do not repeat ambiguous POSTs.
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/actions/workflows/{self.workflow}/dispatches"
        self.http.request("POST", url, headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json"},
                          json={"ref": "main", "inputs": {"mode": "backfill", **period.model_dump(mode="json")}}, retry_safe=False)
        return {"queued": True, **period.model_dump(mode="json"), "workflow_url": self.config["workflow_url"]}
