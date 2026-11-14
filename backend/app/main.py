from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

APP_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = APP_ROOT / "frontend"
DATABASE_PATH = Path(os.getenv("VPS_DATABASE_PATH", str(APP_ROOT / "virtuewise-vps.db")))

app = FastAPI(
    title="VirtueWise VPS API",
    description="Open-source, self-hosted VPS control plane.",
    version="0.1.0",
)


def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                hostname TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()


@app.on_event("startup")
def on_startup() -> None:
    initialize_database()


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "virtuewise-vps-api", "version": "0.1.0"}


@app.get("/api/nodes", tags=["nodes"])
def list_nodes() -> dict[str, list[dict[str, Any]]]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, hostname, status, created_at FROM nodes ORDER BY id"
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
