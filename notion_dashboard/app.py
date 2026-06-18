"""FastAPI application and standalone HTML generator."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from notion_dashboard.services.analytics_service import build_payload
from notion_dashboard.services.dashboard_service import DashboardService
from notion_dashboard.services.notion_service import NotionService

PROJECT_DIR = Path(__file__).resolve().parent
KST = ZoneInfo(os.getenv("TIMEZONE", "Asia/Seoul"))
LOGGER = logging.getLogger(__name__)


class DashboardRepository:
    def __init__(self) -> None:
        load_dotenv()
        token = os.getenv("NOTION_TOKEN", "").strip()
        database_id = os.getenv("NOTION_DATABASE_ID", "").strip()
        if not token or not database_id:
            raise RuntimeError("NOTION_TOKEN과 NOTION_DATABASE_ID가 필요합니다.")
        self.notion = NotionService(token, database_id)
        self.lock = Lock()
        self.payload: dict[str, Any] | None = None
        self.expires_at: datetime | None = None

    def get(self, force: bool = False) -> dict[str, Any]:
        now = datetime.now(tz=KST)
        if not force and self.payload and self.expires_at and now < self.expires_at:
            return self.payload
        with self.lock:
            now = datetime.now(tz=KST)
            if not force and self.payload and self.expires_at and now < self.expires_at:
                return self.payload
            snapshot = self.notion.fetch_all()
            self.payload = build_payload(snapshot.rows, snapshot.property_map, now)
            self.expires_at = now + timedelta(minutes=5)
            return self.payload


dashboard_service = DashboardService(PROJECT_DIR)
repository: DashboardRepository | None = None
app = FastAPI(title="Notion 장애 Dashboard", version="1.0.0")


def get_repository() -> DashboardRepository:
    global repository
    if repository is None:
        repository = DashboardRepository()
    return repository


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    try:
        payload = get_repository().get()
        return HTMLResponse(dashboard_service.render(payload))
    except Exception as exc:
        LOGGER.exception("Dashboard 생성 실패")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/dashboard")
def dashboard_data(refresh: bool = False) -> dict[str, Any]:
    try:
        return get_repository().get(force=refresh)
    except Exception as exc:
        LOGGER.exception("Notion 동기화 실패")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def generate_static(output: Path) -> Path:
    payload = get_repository().get(force=True)
    return dashboard_service.write_static(payload, output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Notion 장애 Dashboard")
    parser.add_argument("--generate", metavar="PATH", help="정적 index.html 생성")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    if args.generate:
        path = generate_static(Path(args.generate))
        print(f"Dashboard generated: {path}")
        return
    import uvicorn

    uvicorn.run("notion_dashboard.app:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()

