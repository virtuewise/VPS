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
from .tenancy import audit, hash_password, initialize_tenancy, verify_password

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
    initialize_tenancy(DATABASE_PATH, ADMIN_PASSWORD)


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
        if int(session["expires"]) < int(time.time()) or not isinstance(session["csrf"], str): return None
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            row = connection.execute("SELECT id,username,role,active FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if not row or not row[3]: return None
        session.update({"id": row[0], "username": row[1], "role": row[2]})
        return session
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def require_user(request: Request, x_csrf_token: str | None = Header(default=None)) -> dict[str, Any]:
    session = _read_session(request)
    if not session: raise HTTPException(status_code=401, detail="Sign in required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and (not x_csrf_token or not hmac.compare_digest(x_csrf_token, session["csrf"])):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid.")
    return session


def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if user["role"] != "admin": raise HTTPException(status_code=403, detail="Administrator role required.")
    return user


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    password: str = Field(min_length=16, max_length=1024)
    role: str = Field(default="user", pattern=r"^(admin|user)$")
    vm_quota: int = Field(default=3, ge=0, le=1000)


class UserUpdateRequest(BaseModel):
    role: str = Field(pattern=r"^(admin|user)$")
    vm_quota: int = Field(ge=0, le=1000)
    active: bool


class CloudInitRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32, pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    password_hash: str = Field(min_length=90, max_length=200, pattern=r"^\\$6\\$(?:rounds=[0-9]+\\$)?[A-Za-z0-9./]{1,16}\\$[A-Za-z0-9./]{86}$")
    ssh_public_key: str = Field(min_length=40, max_length=8192, pattern=r"^(?:ssh-ed25519|ssh-rsa) [A-Za-z0-9+/=]+(?: [^\\r\\n]{0,100})?$")


class CreateVMRequest(BaseModel):
    name: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")
    memory_mb: int = Field(default=2048, ge=512, le=32768)
    disk_gb: int = Field(default=20, ge=8, le=500)
    cpu_count: int = Field(default=2, ge=1, le=16)
    iso_path: str = Field(min_length=5, max_length=260)
    switch_name: str | None = Field(default=None, max_length=100)
    cloud_init: CloudInitRequest | None = None


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "virtuewise-vps-api", "version": "0.4.0"}


@app.get("/api/auth/session", tags=["authentication"])
def auth_session(request: Request) -> dict[str, Any]:
    session = _read_session(request)
    if not session:
        return {"authenticated": False}
    return {"authenticated": True, "csrf_token": session["csrf"], "expires_at": session["expires"], "user": {"id": session["id"], "username": session["username"], "role": session["role"]}}


@app.post("/api/auth/login", tags=["authentication"])
def auth_login(credentials: LoginRequest, response: Response, http_request: Request) -> dict[str, Any]:
    username_key = credentials.username.strip().lower()
    client_ip = http_request.client.host if http_request.client else "unknown"
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        attempts = connection.execute("SELECT COUNT(*) FROM login_attempts WHERE username_key=? AND client_ip=? AND attempted_at >= datetime('now','-15 minutes')", (username_key, client_ip)).fetchone()[0]
        if attempts >= 5:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait 15 minutes and try again.")
        row = connection.execute("SELECT id,username,password_hash,role,active FROM users WHERE username=? COLLATE NOCASE", (credentials.username,)).fetchone()
        if not row or not row[4] or not verify_password(credentials.password, row[2]):
            connection.execute("INSERT INTO login_attempts(username_key,client_ip) VALUES(?,?)", (username_key, client_ip))
            connection.commit()
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        connection.execute("DELETE FROM login_attempts WHERE username_key=? AND client_ip=?", (username_key, client_ip))
        connection.commit()
    csrf = secrets.token_urlsafe(32)
    expires = int(time.time()) + SESSION_TTL_SECONDS
    payload = _b64(json.dumps({"user_id": row[0], "expires": expires, "csrf": csrf}, separators=(",", ":")).encode("utf-8"))
    response.set_cookie(
        SESSION_COOKIE, f"{payload}.{_session_signature(payload)}",
        max_age=SESSION_TTL_SECONDS, httponly=True, secure=SESSION_COOKIE_SECURE,
        samesite="strict", path="/",
    )
    actor = {"id": row[0], "username": row[1]}
    audit(DATABASE_PATH, actor, "auth.login", row[1])
    return {"authenticated": True, "csrf_token": csrf, "expires_at": expires, "user": {"id": row[0], "username": row[1], "role": row[3]}}


@app.post("/api/auth/logout", tags=["authentication"])
def auth_logout(response: Response, user: dict[str, Any] = Depends(require_user)) -> dict[str, bool]:
    audit(DATABASE_PATH, user, "auth.logout", user["username"])
    response.delete_cookie(SESSION_COOKIE, path="/", secure=SESSION_COOKIE_SECURE, httponly=True, samesite="strict")
    return {"success": True}


@app.get("/api/users", tags=["users"])
def list_users(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id,username,role,vm_quota,active,created_at FROM users ORDER BY id").fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/users", tags=["users"])
def create_user(request: UserCreateRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            cursor = connection.execute("INSERT INTO users(username,password_hash,role,vm_quota) VALUES(?,?,?,?)", (request.username, hash_password(request.password), request.role, request.vm_quota))
            connection.commit()
    except sqlite3.IntegrityError: raise HTTPException(status_code=409, detail="Username already exists.") from None
    audit(DATABASE_PATH, admin, "user.create", request.username, f"role={request.role}; quota={request.vm_quota}")
    return {"id": cursor.lastrowid, "username": request.username, "role": request.role, "vm_quota": request.vm_quota}


@app.patch("/api/users/{user_id}", tags=["users"])
def update_user(user_id: int, request: UserUpdateRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        target = connection.execute("SELECT id,username,role,active FROM users WHERE id=?", (user_id,)).fetchone()
        if not target: raise HTTPException(status_code=404, detail="User not found.")
        if target[0] == admin["id"] and (request.role != "admin" or not request.active):
            raise HTTPException(status_code=400, detail="You cannot demote or suspend your own administrator account.")
        if target[2] == "admin" and (request.role != "admin" or not request.active):
            count = connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if count <= 1: raise HTTPException(status_code=400, detail="At least one active administrator must remain.")
        connection.execute("UPDATE users SET role=?,vm_quota=?,active=? WHERE id=?", (request.role, request.vm_quota, int(request.active), user_id))
        connection.commit()
    audit(DATABASE_PATH, admin, "user.update", target[1], f"role={request.role}; quota={request.vm_quota}; active={request.active}")
    return {"id": user_id, "username": target[1], "role": request.role, "vm_quota": request.vm_quota, "active": request.active}


@app.post("/api/users/{user_id}/transfer-vm", tags=["users"])
def transfer_vm_ownership(user_id: int, vm_name: str, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        user = connection.execute("SELECT id,username,active FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not user[2]: raise HTTPException(status_code=404, detail="Active destination user not found.")
        vm = connection.execute("SELECT vm_name FROM vm_ownership WHERE vm_name=?", (vm_name,)).fetchone()
        if not vm: raise HTTPException(status_code=404, detail="Owned VM record not found.")
        connection.execute("UPDATE vm_ownership SET user_id=? WHERE vm_name=?", (user_id, vm_name))
        connection.commit()
    audit(DATABASE_PATH, admin, "vm.transfer", vm_name, f"new_owner={user[1]}")
    return {"vm_name": vm_name, "owner": user[1]}


@app.delete("/api/users/{user_id}", tags=["users"])
def delete_user(user_id: int, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, bool]:
    if user_id == admin["id"]: raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        target = connection.execute("SELECT username,role FROM users WHERE id=?", (user_id,)).fetchone()
        if not target: raise HTTPException(status_code=404, detail="User not found.")
        if target[1] == "admin" and connection.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0] <= 1:
            raise HTTPException(status_code=400, detail="At least one active administrator must remain.")
        owned = connection.execute("SELECT COUNT(*) FROM vm_ownership WHERE user_id=?", (user_id,)).fetchone()[0]
        if owned: raise HTTPException(status_code=409, detail=f"Transfer or delete the user's {owned} VM ownership record(s) before deleting this account.")
        connection.execute("DELETE FROM users WHERE id=?", (user_id,))
        connection.commit()
    audit(DATABASE_PATH, admin, "user.delete", target[0])
    return {"success": True}


@app.get("/api/audit", tags=["administration"])
def get_audit_log(limit: int = 100, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    limit = max(1, min(limit, 500))
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id,actor_username,action,target,details,created_at FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(row) for row in rows]}


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


@app.get("/api/agent/status", tags=["agent"], dependencies=[Depends(require_user)])
async def get_agent_status() -> dict[str, Any]:
    if not agent_configured():
        return {"configured": False, "online": False, "message": "Agent connection not configured"}
    try:
        result = await agent_request("GET", "/health")
        return {"configured": True, "online": result.get("status") == "ok", "agent": result}
    except HTTPException as exc:
        return {"configured": True, "online": False, "message": str(exc.detail)}


@app.get("/api/vms", tags=["instances"])
async def list_vms(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    result = await agent_request("GET", "/v1/vms")
    if user["role"] == "admin": return result
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        owned = {row[0] for row in connection.execute("SELECT vm_name FROM vm_ownership WHERE user_id=?", (user["id"],)).fetchall()}
    result["items"] = [vm for vm in result.get("items", []) if vm.get("name") in owned]
    return result


@app.get("/api/switches", tags=["agent"], dependencies=[Depends(require_user)])
async def list_switches() -> dict[str, Any]:
    return await agent_request("GET", "/v1/switches")


@app.get("/api/monitor", tags=["monitoring"], dependencies=[Depends(require_user)])
async def get_monitor() -> dict[str, Any]:
    return await agent_request("GET", "/v1/monitor")


@app.post("/api/vms", tags=["instances"])
async def create_vm(request: CreateVMRequest, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            quota = connection.execute("SELECT vm_quota FROM users WHERE id=?", (user["id"],)).fetchone()[0]
            used = connection.execute("SELECT COUNT(*) FROM vm_ownership WHERE user_id=?", (user["id"],)).fetchone()[0]
        if quota >= 0 and used >= quota: raise HTTPException(status_code=403, detail="Your VM quota has been reached. Contact an administrator.")
    result = await agent_request("POST", "/v1/vms", request.model_dump())
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("INSERT OR REPLACE INTO vm_ownership(vm_name,user_id) VALUES(?,?)", (request.name, user["id"]))
        connection.commit()
    audit(DATABASE_PATH, user, "vm.create", request.name)
    return result


def authorize_vm(name: str, user: dict[str, Any]) -> None:
    if user["role"] == "admin": return
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        row = connection.execute("SELECT user_id FROM vm_ownership WHERE vm_name=?", (name,)).fetchone()
    if not row or row[0] != user["id"]: raise HTTPException(status_code=404, detail="VM not found.")


@app.post("/api/vms/{name}/start", tags=["instances"])
async def start_vm(name: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    authorize_vm(name, user)
    result = await agent_request("POST", "/v1/vms/start", {"name": name})
    audit(DATABASE_PATH, user, "vm.start", name)
    return result


@app.post("/api/vms/{name}/stop", tags=["instances"])
async def stop_vm(name: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    authorize_vm(name, user)
    result = await agent_request("POST", "/v1/vms/stop", {"name": name})
    audit(DATABASE_PATH, user, "vm.stop", name)
    return result


@app.post("/api/vms/{name}/console", tags=["instances"])
async def open_console(name: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    authorize_vm(name, user)
    return await agent_request("POST", f"/v1/vms/{name}/console")


@app.delete("/api/vms/{name}", tags=["instances"])
async def delete_vm(name: str, confirm_name: str, delete_disks: bool = False, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    authorize_vm(name, user)
    result = await agent_request("DELETE", f"/v1/vms/{name}", {"name": name, "confirm_name": confirm_name, "delete_disks": delete_disks})
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("DELETE FROM vm_ownership WHERE vm_name=?", (name,))
        connection.commit()
    audit(DATABASE_PATH, user, "vm.delete", name, f"delete_disks={delete_disks}")
    return result


@app.get("/api/nodes", tags=["nodes"], dependencies=[Depends(require_user)])
def list_nodes() -> dict[str, list[dict[str, Any]]]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id, name, hostname, status, created_at FROM nodes ORDER BY id").fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
