# Notion 장애 Dashboard 자동 생성기

Notion 장애 Database를 읽어 월별 추이, MTTR, 영향도, 등급, 담당자, 처리 퍼널을 제공하는
반응형 Dashboard입니다. FastAPI 운영 서버와 단일 정적 `index.html` 생성을 모두 지원합니다.

## 실행

저장소 루트에서 실행합니다.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r notion_dashboard/requirements.txt
cp notion_dashboard/.env.example .env
uvicorn notion_dashboard.app:app --reload
```

브라우저에서 `http://localhost:8000`을 엽니다. 서버는 Notion 결과를 5분간 캐시하며,
Dashboard는 5분마다 `/api/dashboard`를 호출합니다. **Notion 새로고침** 버튼은 캐시를
무시하고 즉시 다시 조회합니다.

## 정적 HTML 생성

```bash
python -m notion_dashboard.app --generate public/index.html
```

생성된 파일은 CSS, JavaScript, 데이터를 모두 내장합니다. Chart.js만 CDN에서 로드하므로
인터넷이 연결된 브라우저에서 파일을 직접 열 수 있습니다. Notion 토큰은 HTML에 포함되지
않습니다. 정적 파일은 API를 직접 호출하지 않으므로 새 데이터 반영에는 재생성이 필요합니다.

## 자동 컬럼 매핑

컬럼 이름을 공백·기호·대소문자를 제거해 별칭과 비교한 뒤 속성 타입으로 보완 추론합니다.
현재 논리 필드는 제목, 등록일, 시작/종료시간, 구분, 영향도, 등급, 상태, 담당자, 원인,
조치내용입니다. 실제 매핑 결과는 API 응답의 `property_map`과 서버 로그에서 확인할 수 있습니다.

종료시간이 없으면 진행 중으로 처리하고 MTTR에서 제외합니다. 종료시간이 시작시간보다
이르면 자정을 넘긴 장애로 보고 종료시간에 하루를 더합니다.

## Render 배포

저장소 루트를 Render에 연결하고 `notion_dashboard/render.yaml` Blueprint를 사용합니다.
Render 환경변수에 `NOTION_TOKEN`, `NOTION_DATABASE_ID`를 등록합니다.

## GitHub Pages

`.github/workflows/dashboard-pages.yml`이 정적 Dashboard를 생성하고 Pages artifact로
배포합니다. 저장소 Settings에서:

1. **Pages → Source → GitHub Actions** 선택
2. Actions Secret `NOTION_TOKEN` 등록
3. Actions Variable `NOTION_DATABASE_ID` 등록
4. `Notion incident dashboard pages` 워크플로 수동 실행

워크플로는 6시간마다 정적 페이지를 재생성합니다. GitHub Pages는 백엔드가 없으므로
5분 실시간 갱신이 필요하면 Render의 FastAPI 모드를 사용해야 합니다.

### Notion 임베드

Notion에는 전체 Dashboard URL 뒤에 `?embed=1`을 붙인 주소를 사용합니다.

```text
https://mhjang-qa.github.io/incident_slack_notion/?embed=1
```

임베드 모드는 좌측 메뉴와 큰 헤더, 최근 장애 테이블을 숨기고 필터, KPI, 월별 추이,
처리 퍼널까지만 컴팩트하게 표시합니다. Notion에서 `/embed` 블록을 만든 뒤 위 URL을
입력하고 블록 너비를 페이지 전체 너비에 맞추는 것을 권장합니다.

## 테스트

```bash
python -m unittest discover -s notion_dashboard/tests -v
```
