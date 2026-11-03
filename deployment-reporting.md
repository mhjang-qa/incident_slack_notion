# 자동 운영

운영 저장소: `mhjang-qa/incident_slack_notion`.

별도 `Slack deployment to Notion` 워크플로가 약 15분 주기로 실행됩니다. 기존 `Slack to Notion incident sync` 워크플로는 수정하지 않습니다. 로컬 화면이나 Mac의 실행 상태와 무관합니다.

프로세스는 기존 Bot이 이미 참여한 Slack 채널에서 공지를 찾고, 배포 여부·기술 영역을 Gemini로 분석해 QA 아래 전용 구조로 자동 반영합니다. 실패한 Event도 보존하고 다음 실행에서 재처리합니다. 최초 7일 공지 수집 이후 같은 입력은 다시 Gemini에 보내지 않습니다. 진행 중 Event는 48시간 Window까지 후속 대화를 확인합니다.

운영별 설정은 `DEPLOYMENT_NOTION_PAGE_ID`, `DEPLOYMENT_SLACK_CHANNEL_IDS`, `DEPLOYMENT_GEMINI_MODEL` 변수와 `DEPLOYMENT_GEMINI_API_KEY` Secret을 사용합니다. 장애용 설정은 그대로 유지합니다. Slack/Notion 인증은 기존 Secret을 직접 참조합니다.

상태는 인증 암호화한 SQLite로 캐시합니다. 원문이 있는 평문 DB나 로그는 저장소·Actions Artifact·캐시에 업로드하지 않습니다. `DEPLOYMENT_STATE_KEY`가 없으면 기존 Notion Token에서 암호화 키를 유도합니다. 토큰 교체 전 별도 상태 키로 이관해야 이전 캐시를 복호화할 수 있습니다. 복호화 실패 시 기존 데이터를 임의 초기화하지 않고 실패합니다.

Notion 결과는 인증된 QA 페이지에서 확인하고, 실행 성공·실패는 GitHub Actions에서 확인합니다. 로컬 GUI는 보조 디버깅 도구입니다. GitHub 예약 실행은 정확한 시각을 보장하지 않으며 캐시 영속성도 별도 백업을 대체하지 않습니다.

[GitHub 캐시 보안 지침](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching), [GitHub Secret 암호화 방식](https://docs.github.com/en/rest/guides/encrypting-secrets-for-the-rest-api)을 참고하세요.
