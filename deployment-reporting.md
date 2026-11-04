# 자동 운영

운영 저장소: `mhjang-qa/incident_slack_notion`.

별도 `Slack deployment to Notion` 워크플로가 약 15분 주기로 실행됩니다. 기존 `Slack to Notion incident sync` 워크플로는 수정하지 않습니다. 로컬 화면이나 Mac의 실행 상태와 무관합니다.

프로세스는 기존 Bot이 이미 참여한 Slack 채널에서 공지를 찾고, 배포 여부·기술 영역을 Gemini로 분석해 QA 아래 전용 구조로 자동 반영합니다. 실패한 Event도 보존하고 다음 실행에서 재처리합니다. 최초 7일 공지 수집 이후 같은 입력은 다시 Gemini에 보내지 않습니다. 진행 중 Event는 48시간 Window까지 후속 대화를 확인합니다.

운영별 설정은 `DEPLOYMENT_NOTION_PAGE_ID`, `DEPLOYMENT_SLACK_CHANNEL_IDS`, `DEPLOYMENT_GEMINI_MODEL` 변수와 `DEPLOYMENT_GEMINI_API_KEY` Secret을 사용합니다. 장애용 설정은 그대로 유지합니다. Slack/Notion 인증은 기존 Secret을 직접 참조합니다.

상태는 인증 암호화한 SQLite로 캐시합니다. 원문이 있는 평문 DB나 로그는 저장소·Actions Artifact·캐시에 업로드하지 않습니다. `DEPLOYMENT_STATE_KEY`가 없으면 기존 Notion Token에서 암호화 키를 유도합니다. 토큰 교체 전 별도 상태 키로 이관해야 이전 캐시를 복호화할 수 있습니다. 복호화 실패 시 기존 데이터를 임의 초기화하지 않고 실패합니다.

Notion 결과는 인증된 QA 페이지에서 확인하고, 실행 성공·실패는 GitHub Actions에서 확인합니다. 로컬 GUI는 보조 디버깅 도구입니다. GitHub 예약 실행은 정확한 시각을 보장하지 않으며 캐시 영속성도 별도 백업을 대체하지 않습니다.

[GitHub 캐시 보안 지침](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching), [GitHub Secret 암호화 방식](https://docs.github.com/en/rest/guides/encrypting-secrets-for-the-rest-api)을 참고하세요.

일일 Gemini 쿼터는 Deferred로 보존하고 RetryInfo 이후 예약 실행에서 자동 재개합니다. 이 대기는 일반 실패와 분리해 집계하며, 이미 처리한 Summary와 Raw Data는 다시 생성하지 않습니다.


## 과거 수집 작업

워크플로 수동 실행 입력 `mode=backfill`, `start_date=YYYY-MM-DD`, `end_date=YYYY-MM-DD`로 접수합니다. 종료일을 포함하며 공지 게시일 기준입니다. 최초 실행에서 날짜 범위와 채널을 고정해 durable backfill 작업을 생성하고, 모든 예약 실행에서 최근 배포 처리 후 미완료 과거 작업을 이어갑니다. 최신 공지가 없는 기존 Bot 참여 채널도 과거 수집 탐색에 포함합니다. 명시적인 배포 채널 변수가 있으면 그 채널만 사용합니다.

과거 작업의 진행 위치와 처리한 Event는 암호화 캐시의 동일 SQLite에 보관됩니다. 실패·쿼터 대기·미처리 항목이 있으면 날짜를 넘기지 않습니다. 과거 작업이 관리하는 Window 밖의 대기 Event는 일반 최근 수집에서 다시 전체 조회하지 않아, 과거 요청 때문에 최근 배포 조회 범위가 수개월로 늘어나는 것을 방지합니다. 실제 배포/QA 완료를 추측하지 않는 기존 분석 정책은 동일합니다.

Notion Deployment Report 상단의 프로그램 전용 **과거 수집 진행** Callout에서 확인할 수 있습니다. 캐시가 삭제되면 진행 위치를 완전히 복구할 수는 없지만 기존 Notion Source Key/Raw Record Key를 사용해 중복 생성을 방지합니다.


## 자동 통계

Summary는 월·주간 통계와 서비스/프로젝트/기술 영역/환경별 순위 페이지입니다. 기존 개별 배포 Summary DB는 ID와 속성을 유지해 배포 이력으로 이름만 바꿉니다. 주기 실행의 마지막 단계에서 Notion 전체 등록 이력을 읽어 집계하므로 Gemini 쿼터 대기 중에도 이미 확인한 기록으로 통계를 갱신할 수 있습니다. 현재 날짜·원본 집계 값·과거 수집 상태가 바뀌지 않으면 표를 반복 작성하지 않습니다. 통계가 실제 배포 완료를 의미하지 않도록 예정 공지를 포함한 등록 건수와 검증 완료 건수를 분리합니다.


## 간단한 Notion 대시보드

Deployment Report 상단에는 전체 등록 공지 기준 서비스별·대표 Component별 도넛 차트와 전체/이번 달/이번 주 건수, 이번 달 최다 서비스만 표시합니다. 복수 Components는 대표 Component 한 개로 집계하여 파이 합계를 보존합니다. PNG는 Notion에 직접 업로드하며 공개 이미지 호스팅은 사용하지 않습니다. 상세 통계·배포 이력·원문 DB는 lowdata 하위로 이동하며 기존 ID와 레코드를 유지합니다.

동기화 링크는 GitHub Actions 수집 화면을 엽니다. 저장소 실행 권한이 있는 계정으로 로그인한 후 Run workflow에서 sync를 선택하면 최근 수집, backfill과 start_date/end_date를 지정하면 과거 수집을 요청합니다. 자동 실행은 기존 15분 주기를 유지합니다.


## 전용 동기화 웹 화면

Notion 동기화 링크는 https://deployment-sync-qa.nearfinch1.chatgpt.site 로 연결합니다. 사이트 소유자만 ChatGPT 로그인 후 최근 동기화 또는 과거 날짜 범위를 요청하고 상태를 볼 수 있습니다. 수집 요청 문서는 비공개 R2에 저장하며 Slack 원문·Notion 키·GitHub 키는 웹 사이트로 전달하지 않습니다. 사이트 서비스 키와 기존 사이트 서비스 액세스 토큰은 Actions 시크릿에 보관합니다.

5분 주기의 deployment-requests.yml은 대기 요청이 있고 수집 작업이 실행 중이지 않을 때 저장소 범위 GITHUB_TOKEN으로 기존 deployment-sync.yml을 호출합니다. 기존 15분 수집도 요청을 확인합니다. 실제 스케줄 시작은 GitHub 대기열에 따라 늦어질 수 있습니다. 수집기는 요청을 날짜 범위 작업으로 멱등 등록한 후 Notion 반영·암호화 상태 저장이 성공한 다음에만 접수 상태를 갱신합니다. AI 쿼터 대기와 완료를 구분합니다.
