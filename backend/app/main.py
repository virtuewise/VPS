from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_ROOT = Path(os.getenv("VPS_APP_ROOT", str(Path(__file__).resolve().parents[2])))
FRONTEND_DIR = APP_ROOT / "frontend"
DATABASE_PATH = Path(os.getenv("VPS_DATABASE_PATH", str(APP_ROOT / "virtuewise-vps.db")))
AGENT_URL = os.getenv("VPS_AGENT_URL", "").rstrip("/")
AGENT_TOKEN = os.getenv("VPS_AGENT_TOKEN", "")
AGENT_TIMEOUT = float(os.getenv("VPS_AGENT_TIMEOUT", "8"))
app = FastAPI(title="VirtueWise VPS API", description="Open-source, self-hosted VPS control plane.", version="0.3.0")


def initialize_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, hostname TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        connection.commit()


@app.on_event("startup")
def on_startup() -> None:
    initialize_database()


def agent_configured() -> bool:
    return bool(AGENT_URL and AGENT_TOKEN)


async def agent_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    if not agent_configured():
        raise HTTPException(status_code=503, detail="Hyper-V agent is not configured. Set VPS_AGENT_URL and VPS_AGENT_TOKEN.")
    parsed = urlparse(AGENT_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=503, detail="Invalid VPS_AGENT_URL configuration.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=503, detail="HTTP is only permitted for a loopback agent. Use HTTPS for remote nodes.")
    try:
        async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
            response = await client.request(method, f"{AGENT_URL}{path}", json=payload, headers={"Authorization": f"Bearer {AGENT_TOKEN}"})
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Hyper-V agent request timed out.") from None
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not connect to the Hyper-V agent.") from None
    if response.status_code >= 400:
        detail = "Hyper-V agent rejected the request."
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Hyper-V agent returned an invalid response.") from None


class CreateVMRequest(BaseModel):
    name: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")
    memory_mb: int = Field(default=2048, ge=512, le=32768)
    disk_gb: int = Field(default=20, ge=8, le=500)
    cpu_count: int = Field(default=2, ge=1, le=16)
    iso_path: str = Field(min_length=5, max_length=260)
    switch_name: str | None = Field(default=None, max_length=100)
    cloud_init: dict[str, str] | None = None


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "virtuewise-vps-api", "version": "0.3.0"}


@app.get("/api/agent/status", tags=["agent"])
async def get_agent_status() -> dict[str, Any]:
    if not agent_configured():
        return {"configured": False, "online": False, "message": "Agent connection not configured"}
    try:
        result = await agent_request("GET", "/health")
        return {"configured": True, "online": result.get("status") == "ok", "agent": result}
    except HTTPException as exc:
        return {"configured": True, "online": False, "message": str(exc.detail)}


@app.get("/api/vms", tags=["instances"])
async def list_vms() -> dict[str, Any]:
    return await agent_request("GET", "/v1/vms")


@app.get("/api/switches", tags=["agent"])
async def list_switches() -> dict[str, Any]:
    return await agent_request("GET", "/v1/switches")


@app.get("/api/monitor", tags=["monitoring"])
async def get_monitor() -> dict[str, Any]:
    return await agent_request("GET", "/v1/monitor")


@app.post("/api/vms", tags=["instances"])
async def create_vm(request: CreateVMRequest) -> dict[str, Any]:
    return await agent_request("POST", "/v1/vms", request.model_dump())


@app.post("/api/vms/{name}/start", tags=["instances"])
async def start_vm(name: str) -> dict[str, Any]:
    return await agent_request("POST", "/v1/vms/start", {"name": name})


@app.post("/api/vms/{name}/stop", tags=["instances"])
async def stop_vm(name: str) -> dict[str, Any]:
    return await agent_request("POST", "/v1/vms/stop", {"name": name})


@app.post("/api/vms/{name}/console", tags=["instances"])
async def open_console(name: str) -> dict[str, Any]:
    return await agent_request("POST", f"/v1/vms/{name}/console")


@app.delete("/api/vms/{name}", tags=["instances"])
async def delete_vm(name: str, confirm_name: str, delete_disks: bool = False) -> dict[str, Any]:
    return await agent_request("DELETE", f"/v1/vms/{name}", {"name": name, "confirm_name": confirm_name, "delete_disks": delete_disks})


@app.get("/api/nodes", tags=["nodes"])
def list_nodes() -> dict[str, list[dict[str, Any]]]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id, name, hostname, status, created_at FROM nodes ORDER BY id").fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
