from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

APP_ROOT = Path(os.getenv("VPS_APP_ROOT", str(Path(__file__).resolve().parents[2])))
FRONTEND_DIR = APP_ROOT / "frontend"
DATABASE_PATH = Path(os.getenv("VPS_DATABASE_PATH", str(APP_ROOT / "virtuewise-vps.db")))
AGENT_URL = os.getenv("VPS_AGENT_URL", "").rstrip("/")
AGENT_TOKEN = os.getenv("VPS_AGENT_TOKEN", "")
AGENT_TIMEOUT = float(os.getenv("VPS_AGENT_TIMEOUT", "8"))
ADMIN_PASSWORD = os.getenv("VPS_ADMIN_PASSWORD", "")
SESSION_SECRET = os.getenv("VPS_SESSION_SECRET", "")
SESSION_COOKIE_SECURE = os.getenv("VPS_SESSION_COOKIE_SECURE", "false").lower() == "true"
SESSION_TTL_SECONDS = 12 * 60 * 60
SESSION_COOKIE = "vwps_session"
app = FastAPI(title="VirtueWise VPS API", description="Open-source, self-hosted VPS control plane.", version="0.4.0")


def initialize_database() -> None:
    if len(ADMIN_PASSWORD) < 16:
        raise RuntimeError("Set VPS_ADMIN_PASSWORD to a unique password of at least 16 characters.")
    if len(SESSION_SECRET) < 32:
        raise RuntimeError("Set VPS_SESSION_SECRET to a random secret of at least 32 characters.")
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, hostname TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        connection.commit()


@app.on_event("startup")
def on_startup() -> None:
    initialize_database()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _session_signature(payload: str) -> str:
    return _b64(hmac.new(SESSION_SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())


def _read_session(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(signature, _session_signature(payload)):
            return None
        session = json.loads(_unb64(payload))
        if int(session["expires"]) < int(time.time()) or not isinstance(session["csrf"], str):
            return None
        return session
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def require_admin(request: Request, x_csrf_token: str | None = Header(default=None)) -> None:
    session = _read_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="Administrator login required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not x_csrf_token or not hmac.compare_digest(x_csrf_token, session["csrf"]):
            raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


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
    return {"status": "ok", "service": "virtuewise-vps-api", "version": "0.4.0"}


@app.get("/api/auth/session", tags=["authentication"])
def auth_session(request: Request) -> dict[str, Any]:
    session = _read_session(request)
    if not session:
        return {"authenticated": False}
    return {"authenticated": True, "csrf_token": session["csrf"], "expires_at": session["expires"]}


@app.post("/api/auth/login", tags=["authentication"])
def auth_login(request: LoginRequest, response: Response) -> dict[str, Any]:
    if not hmac.compare_digest(request.password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Incorrect administrator password.")
    csrf = secrets.token_urlsafe(32)
    expires = int(time.time()) + SESSION_TTL_SECONDS
    payload = _b64(json.dumps({"expires": expires, "csrf": csrf}, separators=(",", ":")).encode("utf-8"))
    response.set_cookie(
        SESSION_COOKIE, f"{payload}.{_session_signature(payload)}",
        max_age=SESSION_TTL_SECONDS, httponly=True, secure=SESSION_COOKIE_SECURE,
        samesite="strict", path="/",
    )
    return {"authenticated": True, "csrf_token": csrf, "expires_at": expires}


@app.post("/api/auth/logout", tags=["authentication"], dependencies=[Depends(require_admin)])
def auth_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=SESSION_COOKIE_SECURE, httponly=True, samesite="strict")
    return {"success": True}


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


@app.get("/api/agent/status", tags=["agent"], dependencies=[Depends(require_admin)])
async def get_agent_status() -> dict[str, Any]:
    if not agent_configured():
        return {"configured": False, "online": False, "message": "Agent connection not configured"}
    try:
        result = await agent_request("GET", "/health")
        return {"configured": True, "online": result.get("status") == "ok", "agent": result}
    except HTTPException as exc:
        return {"configured": True, "online": False, "message": str(exc.detail)}


@app.get("/api/vms", tags=["instances"], dependencies=[Depends(require_admin)])
async def list_vms() -> dict[str, Any]:
    return await agent_request("GET", "/v1/vms")


@app.get("/api/switches", tags=["agent"], dependencies=[Depends(require_admin)])
async def list_switches() -> dict[str, Any]:
    return await agent_request("GET", "/v1/switches")


@app.get("/api/monitor", tags=["monitoring"], dependencies=[Depends(require_admin)])
async def get_monitor() -> dict[str, Any]:
    return await agent_request("GET", "/v1/monitor")


@app.post("/api/vms", tags=["instances"], dependencies=[Depends(require_admin)])
async def create_vm(request: CreateVMRequest) -> dict[str, Any]:
    return await agent_request("POST", "/v1/vms", request.model_dump())


@app.post("/api/vms/{name}/start", tags=["instances"], dependencies=[Depends(require_admin)])
async def start_vm(name: str) -> dict[str, Any]:
    return await agent_request("POST", "/v1/vms/start", {"name": name})


@app.post("/api/vms/{name}/stop", tags=["instances"], dependencies=[Depends(require_admin)])
async def stop_vm(name: str) -> dict[str, Any]:
    return await agent_request("POST", "/v1/vms/stop", {"name": name})


@app.post("/api/vms/{name}/console", tags=["instances"], dependencies=[Depends(require_admin)])
async def open_console(name: str) -> dict[str, Any]:
    return await agent_request("POST", f"/v1/vms/{name}/console")


@app.delete("/api/vms/{name}", tags=["instances"], dependencies=[Depends(require_admin)])
async def delete_vm(name: str, confirm_name: str, delete_disks: bool = False) -> dict[str, Any]:
    return await agent_request("DELETE", f"/v1/vms/{name}", {"name": name, "confirm_name": confirm_name, "delete_disks": delete_disks})


@app.get("/api/nodes", tags=["nodes"], dependencies=[Depends(require_admin)])
def list_nodes() -> dict[str, list[dict[str, Any]]]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id, name, hostname, status, created_at FROM nodes ORDER BY id").fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
