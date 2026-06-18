# Notion 설정 가이드

## 1. Integration 생성

1. [Notion Integrations](https://www.notion.so/my-integrations)에서 새 Integration을 생성합니다.
2. Read content, Update content, Insert content 권한을 활성화합니다.
3. Internal Integration Secret을 `.env`의 `NOTION_TOKEN`에 입력합니다.

## 2. Database 연결

1. 대상 Database 페이지의 **연결(Connections)** 메뉴에서 생성한 Integration을 초대합니다.
2. Database URL에서 32자리 ID를 추출해 `NOTION_DATABASE_ID`에 입력합니다.
3. 연결된 뷰 URL에 `?v=`가 있으면 `v` 앞의 ID가 Database ID입니다. 하이픈 유무는
   공식 클라이언트에서 모두 처리됩니다.

## 3. 권장 컬럼

| 컬럼 | 권장 타입 |
|---|---|
| 제목 | Title |
| 상태 | Status 또는 Select |
| 장애 발생 일시 | Date |
| 장애 정상화 일시 | Date |
| 장애 지속 시간 | Rich text |
| 영향 서비스 | Multi-select 또는 Rich text |
| 영향도 | Select 또는 Rich text |
| 상세 내용 | Rich text |
| 최초 공지자 | Rich text |
| Slack 링크 | URL |
| 원문 메시지 | Rich text |
| 스레드 요약 | Rich text |
| 등록 일시 | Date |
| 최종 업데이트 일시 | Date |

애플리케이션은 시작 시 실제 스키마를 조회하고 이름과 타입을 확인합니다. 다른 컬럼명을
사용하는 경우 `notion_client.py` 상단의 `PROPERTY_MAP`을 수정합니다. Notion의 생성 시각,
최종 편집 시각 자동 속성은 API로 쓸 수 없으므로 별도의 Date 속성을 사용해야 합니다.

