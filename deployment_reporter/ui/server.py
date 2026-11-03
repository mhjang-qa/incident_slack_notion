from contextlib import asynccontextmanager
from pathlib import Path
import json
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import load_settings
from ..service import Reporter
from ..scheduler.jobs import Scheduler


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(7, ge=1, le=90)
    analyze: bool = True
    sync: bool = False


class AnalyzeRequest(BaseModel):
    recollect: bool = False


def create_app(reporter=None):
    reporter = reporter or Reporter(load_settings())
    scheduler = Scheduler(reporter)

    @asynccontextmanager
    async def lifespan(app):
        yield
        scheduler.stop()

    app = FastAPI(title="Deployment Reporter", lifespan=lifespan)
    app.state.reporter = reporter

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Prevent DNS rebinding and cross-site triggering of local write operations.
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "Local host only"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Same origin required"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site request blocked"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(Exception)
    async def error_handler(request, exc):
        from ..http import APIError
        if isinstance(exc, KeyError):
            return JSONResponse({"detail": "Event not found"}, status_code=404)
        message = str(exc) if isinstance(exc, APIError) else f"{type(exc).__name__}: operation failed; check configuration / logs"
        return JSONResponse({"detail": message}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Path(__file__).with_name("index.html").read_text()

    @app.get("/api/settings")
    def settings():
        return {"settings": reporter.settings.public(), "scheduler_running": scheduler.running,
                "credentials": {"slack": bool(reporter.settings.slack_bot_token.get_secret_value()), "notion": bool(reporter.settings.notion_token.get_secret_value()), "gemini": bool(reporter.settings.gemini_api_key.get_secret_value())}}

    @app.get("/api/automation")
    def automation():
        path = Path("config/cloud.json")
        return json.loads(path.read_text()) if path.exists() else {"enabled": False}

    @app.put("/api/settings")
    def update_settings(changes: dict):
        return reporter.apply_settings(changes)

    @app.post("/api/test/{provider}")
    def test(provider: str):
        clients = {"slack": reporter.slack, "notion": reporter.notion, "gemini": reporter.gemini}
        if provider not in clients:
            raise HTTPException(404, "Unknown provider")
        return clients[provider].test()

    @app.get("/api/channels")
    def channels():
        return [{"id": c["id"], "name": c["name"]} for c in reporter.slack.channels()]

    @app.post("/api/batch")
    def batch(body: BatchRequest):
        return reporter.batch(days=body.days, analyze=body.analyze, sync=body.sync)

    @app.get("/api/events")
    def events():
        return reporter.repo.events()

    @app.get("/api/events/{event_id}")
    def preview(event_id: str):
        return reporter.preview(event_id)

    @app.post("/api/events/{event_id}/analyze")
    def analyze(event_id: str, body: AnalyzeRequest):
        return reporter.analyze(event_id, recollect=body.recollect)

    @app.post("/api/events/{event_id}/sync")
    def sync(event_id: str):
        return {"url": reporter.sync(event_id)}

    @app.post("/api/scheduler/{action}")
    def schedule(action: str):
        if action == "start":
            scheduler.start()
        elif action == "stop":
            scheduler.stop()
        else:
            raise HTTPException(404, "Unknown action")
        return {"running": scheduler.running}

    @app.get("/api/logs")
    def logs():
        return reporter.repo.logs()

    return app
