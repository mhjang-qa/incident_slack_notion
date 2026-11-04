"""Read durable sync requests from the private sync screen; credentials stay server-side."""
import os
from urllib.parse import urlparse
from uuid import UUID

from .http import APIError, Transport
from .domain.period import DateRange


class SyncRequests:
    def __init__(self, url, bearer, service_key, transport=None):
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'deployment-sync-qa.nearfinch1.chatgpt.site' or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
            raise APIError('Invalid sync service origin')
        self.url = url.rstrip('/')
        self.headers = {'OAI-Sites-Authorization': 'Bearer ' + bearer, 'X-Sync-Service-Key': service_key}
        self.http = transport or Transport()

    @classmethod
    def configured(cls):
        values = [os.getenv(k, '') for k in ('SYNC_SITE_URL', 'SYNC_SITE_BEARER', 'SYNC_SERVICE_KEY')]
        if not any(values):
            return None
        if not all(values):
            raise APIError('Sync request service configuration is incomplete')
        return cls(*values)

    def pending(self):
        data = self.http.request('GET', self.url + '/api/internal/pending', headers=self.headers)
        return data['requests']

    def accept(self, requests, history, channels):
        accepted = []
        for request in requests:
            value = request['input']
            try:
                if str(UUID(value['id'], version=4)) != value['id'] or value['id'] != request['id']:
                    raise ValueError('Invalid request ID')
                if value['mode'] == 'backfill':
                    period = DateRange(start_date=value['start_date'], end_date=value['end_date']).require_past(history.reporter.settings.timezone)
                    job = history.create(period.start_date, period.end_date, channels=channels, sync=True)
                    accepted.append((request['id'], job['id']))
                elif value['mode'] == 'sync':
                    accepted.append((request['id'], None))
                else:
                    raise ValueError('Invalid mode')
            except (ValueError, KeyError):
                self.status(request['id'], 'Failed', '요청 형식 또는 수집 기간을 확인해 주세요.')
        return accepted

    def status(self, request_id, state, message):
        self.http.request('POST', self.url + '/api/internal/status', headers=self.headers,
                          json={'id': request_id, 'state': state, 'message': message})

    def publish(self, accepted, repo, result):
        for request_id, job_id in accepted:
            if job_id:
                job = repo.backfill(job_id)
                state = job['state']
                message = f"발견 {job['discovered']}건 · 처리 {job['processed']}건"
                if state != 'Completed':
                    message += ' · 다음 확인 ' + job['cursor']
                if state == 'Pending':
                    state = 'Queued'
            elif result is None:
                # An early pipeline failure must leave the request retryable.
                state, message = 'Retrying', '작업이 완료되지 않아 다음 실행에서 다시 확인합니다.'
            elif result['failed']:
                state, message = 'Retrying', '일부 수집이 실패해 다음 실행에서 다시 확인합니다.'
            elif result['deferred']:
                state, message = 'WaitingQuota', 'AI 사용 한도가 회복되면 자동으로 이어서 처리합니다.'
            else:
                state, message = 'Completed', '최근 배포 확인과 Notion 리포트 반영을 완료했습니다.'
            self.status(request_id, state, message)


def main():
    """Lightweight scheduled listener dispatches the existing workflow with its scoped Actions token."""
    service = SyncRequests.configured()
    if not service:
        raise APIError('Sync request service is not configured')
    if not service.pending():
        print('No pending sync requests')
        return
    token = os.environ['GH_TOKEN']
    repo = os.environ['GITHUB_REPOSITORY']
    if repo != 'mhjang-qa/incident_slack_notion':
        raise APIError('Unexpected target repository')
    http = Transport()
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json'}
    url = f'https://api.github.com/repos/{repo}/actions/workflows/deployment-sync.yml'
    # Avoid stacking dispatches while the main worker is already queued or running.
    runs = http.request('GET', url + '/runs?per_page=20', headers=headers)['workflow_runs']
    if any(r['status'] in {'queued', 'in_progress', 'waiting', 'requested', 'pending'} for r in runs):
        print('Sync worker already active')
        return
    http.request('POST', url + '/dispatches', headers=headers, json={'ref': 'main', 'inputs': {'mode': 'sync'}}, retry_safe=False)
    print('Sync worker requested')


if __name__ == '__main__':
    main()
