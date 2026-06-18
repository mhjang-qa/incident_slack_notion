# Hanpass 장애 리포트 자동화 시스템

Slack 장애 공지 채널을 1분 주기로 조회하고, 신규 장애를 Notion Database에 등록한 뒤
같은 Slack 스레드의 진행 및 정상화 메시지를 하나의 Notion 페이지에 계속 반영합니다.
SQLite에는 Slack 메시지/스레드와 Notion 페이지 간 매핑만 저장합니다.

## 주요 동작

- 키워드 및 제목 패턴 기반 장애 후보 탐지
- 발생 시각, 서비스, 영향도, 상태 파싱 및 운영 보고서 문장 생성
- `message ts`/`thread ts` 기반 중복 방지
- 등록된 모든 스레드를 계속 추적하여 늦은 정상화 공지도 반영
- 스레드 댓글 추가 시 전체 스레드 요약 갱신
- 명시된 장애 지속시간 우선 사용, 없으면 시작/종료 시각으로 계산
- 자정 경과 시간 보정
- Slack rate limit 및 Notion 일시 오류 재시도

## 설치

Python 3.12 이상을 사용합니다. 저장소 상위 디렉터리에서 실행합니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r incident_slack_notion/requirements.txt
cp incident_slack_notion/.env.example .env
```

`.env`에 실제 값을 입력합니다. 상세 설정은 [Slack 가이드](SLACK_SETUP.md)와
[Notion 가이드](NOTION_SETUP.md)를 참고합니다.

## 실행

상위 디렉터리에서 패키지 모듈로 실행해야 합니다. 프로젝트 파일명이 공식
`notion_client` 패키지명과 같기 때문에 `python incident_slack_notion/app.py` 방식은
사용하지 않습니다.

```bash
# 1회 동기화로 설정 확인
python -m incident_slack_notion.app --once

# 장기 실행
python -m incident_slack_notion.app
```

운영에서는 systemd, Docker, Kubernetes 등 프로세스 감시 환경에서 장기 실행하고,
SQLite 파일이 유지되는 영속 볼륨을 지정하십시오.

## 테스트

외부 API 없이 표준 라이브러리 `unittest`로 파서 동작을 검증합니다.

```bash
python -m unittest discover -s incident_slack_notion/tests -v
```

테스트용 Slack 메시지는 `sample_data.json`에 포함되어 있습니다.

## Notion 컬럼 매핑

실행 시 Database 스키마를 조회해 권장 이름과 별칭을 자동 매핑합니다. 매핑 결과는
로그에 출력됩니다. 조직의 실제 컬럼명이 다르면 `notion_client.py` 상단
`PROPERTY_MAP`을 수정하십시오. 존재하지 않거나 쓰기 불가능한 컬럼은 건너뜁니다.

## 운영 주의사항

- `.env`, SQLite DB, 로그에 토큰을 남기거나 Git에 커밋하지 않습니다.
- Slack 앱을 대상 채널에 초대해야 메시지와 스레드를 조회할 수 있습니다.
- 최초 실행은 기본 72시간을 조회합니다. `SLACK_LOOKBACK_HOURS`로 조정할 수 있습니다.
- 동기화 작업은 단일 인스턴스 실행을 전제로 합니다. 다중 인스턴스가 필요하면
  SQLite 대신 중앙 DB와 분산 잠금을 사용해야 합니다.
- API 오류가 난 스레드는 해당 주기에 실패해도 다음 주기에 다시 조회됩니다.
- 신규 장애가 없는 정상 실행은 `SLACK_NOTIFICATION_CHANNEL`에 확인 메시지를 보냅니다.
- Slack 조회 자체가 실패한 경우에는 장애 없음 메시지를 보내지 않아 오탐을 방지합니다.

## GitHub Actions 예약 실행

저장소의 `.github/workflows/incident-sync.yml`은 다음 작업을 수행합니다.

- 6시간마다 Slack → Notion 동기화 1회 실행
- Actions 캐시에서 `incident_mapping.db`를 복원하여 중복 등록 방지
- 동일 동기화 작업의 병렬 실행 방지
- Actions 화면의 **Run workflow** 버튼을 통한 수동 실행

GitHub 저장소 **Settings → Secrets and variables → Actions**에 아래 값을 설정합니다.

### Secrets

- `SLACK_BOT_TOKEN`
- `NOTION_TOKEN`

### Variables

- `SLACK_CHANNEL_ID`: `C09FYCDU5BR`
- `NOTION_DATABASE_ID`: 대상 Notion Database ID
- `SLACK_NOTIFICATION_CHANNEL`: `slice_gh-test` 또는 해당 채널 ID

기본 실행 시각은 한국 시간 기준 `00:17`, `06:17`, `12:17`, `18:17`입니다. 워크플로
파일이 기본 브랜치에 있어야 예약 실행됩니다. GitHub Actions 예약 실행은 정확한 시각을
보장하지 않으며 부하에 따라 지연될 수 있습니다. 또한 캐시가 삭제되면 SQLite
매핑이 초기화될 수 있으므로, 엄격한 무중단 운영과 1분 주기가 필요하면 GitHub Actions가
아닌 상시 실행 서버에서 `python -m incident_slack_notion.app`을 실행해야 합니다.
