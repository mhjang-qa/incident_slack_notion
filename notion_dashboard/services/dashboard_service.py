"""Template rendering and standalone dashboard generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


class DashboardService:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.environment = Environment(
            loader=FileSystemLoader(project_dir / "templates"),
            autoescape=select_autoescape(("html", "xml")),
        )

    def render(self, payload: dict[str, Any], static_mode: bool = False) -> str:
        template = self.environment.get_template("index.html")
        embedded_json = (
            json.dumps(payload, ensure_ascii=False)
            .replace("&", "\\u0026")
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029")
        )
        return template.render(
            dashboard_data=embedded_json,
            css=(self.project_dir / "static/css/dashboard.css").read_text("utf-8"),
            js=(self.project_dir / "static/js/dashboard.js").read_text("utf-8"),
            static_mode=static_mode,
        )

    def write_static(self, payload: dict[str, Any], output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(self.render(payload, static_mode=True), encoding="utf-8")
        return output
