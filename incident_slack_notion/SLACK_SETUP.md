# Slack 설정 가이드

## 1. Slack App 생성

1. [Slack API Apps](https://api.slack.com/apps)에서 **Create New App**을 선택합니다.
2. **From scratch**를 선택하고 Hanpass 워크스페이스를 지정합니다.
3. **OAuth & Permissions → Bot Token Scopes**에 다음 권한을 추가합니다.

- `channels:history`
- `channels:read`
- `users:read`
- `links:read`
- `chat:write` (장애 없음 알림에 필수)

대상 채널이 비공개 채널이면 `groups:history`, `groups:read` 권한도 필요합니다.

## 2. 설치 및 채널 초대

1. **Install to Workspace**를 실행합니다.
2. 발급된 `xoxb-` Bot User OAuth Token을 `.env`의 `SLACK_BOT_TOKEN`에 입력합니다.
3. 장애 공지 채널에서 `/invite @앱이름`으로 앱을 초대합니다.
4. 채널 URL의 `/archives/` 다음 값인 `C09FYCDU5BR`를 `SLACK_CHANNEL_ID`로 사용합니다.
5. 알림 채널 `slice_gh-test`에도 앱을 초대합니다.

`SLACK_NOTIFICATION_CHANNEL`은 채널명(`slice_gh-test`) 또는 채널 ID를 지원합니다.
운영 안정성을 위해 채널 URL에서 확인한 채널 ID를 설정하는 방식을 권장합니다.

토큰은 코드, 문서, Git 저장소에 저장하지 않습니다.

## 3. Bot 표시 이름

메시지는 `xoxb-` Bot Token과 `chat.postMessage`로 전송하며 코드에서 `username`을
지정하지 않습니다. 따라서 발신자 이름은 Slack App의 Bot User 프로필을 그대로 사용합니다.

표시 이름 변경 절차:

1. Slack App → **App Home → Your App's Presence in Slack**에서 Display Name을
   `Hanpass QA Bot`으로 변경합니다.
2. **OAuth & Permissions → Reinstall to Workspace**를 실행합니다.
3. 발급된 Bot User OAuth Token(`xoxb-...`)으로 GitHub Secret `SLACK_BOT_TOKEN`을
   교체합니다.
4. 앱이 채널에서 제거된 경우에만 다시 초대합니다.

실행 시 `auth.test`, `users.info`, `bots.info`로 토큰의 실제 App/Bot User 정체성을
로그에 남깁니다. App 설정과 설치된 Bot User 프로필 이름이 일시적으로 다를 수 있으므로
이름 불일치는 경고만 남기고, `username` 없이 메시지를 전송하여 Slack이 App 표시 이름을
적용하도록 합니다. 전송 응답의 최종 `username`과 `bot_id`도 로그에 기록됩니다.

## 4. 확인

앱 실행 시 `missing_scope`, `not_in_channel`, `channel_not_found` 오류가 발생하면 권한,
워크스페이스 설치 상태, 채널 초대 여부를 순서대로 확인합니다.
