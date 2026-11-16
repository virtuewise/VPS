from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .tenancy import audit, hash_password, initialize_tenancy, verify_password
from .scheduler import choose_node

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
MCP_READ_TOKEN = os.getenv("VPS_MCP_READ_TOKEN", "")
MCP_SCOPES = {"health.read", "vms.read", "nodes.read", "resources.read"}
MCP_ADMIN_SCOPES = {"nodes.read", "resources.read"}
app = FastAPI(title="VirtueWise VPS API", description="Open-source, self-hosted VPS control plane.", version="0.10.0")
_worker_task: asyncio.Task | None = None
WORKER_INSTANCE_ID = uuid.uuid4().hex
NODE_COMMAND_LEASE_SECONDS = 180
LEGACY_WORKER_LEASE_SECONDS = 60


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
        node_columns = {row[1] for row in connection.execute("PRAGMA table_info(nodes)").fetchall()}
        if "token_hash" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN token_hash TEXT")
        if "last_seen" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN last_seen INTEGER")
        if "resources_json" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN resources_json TEXT NOT NULL DEFAULT '{}'")
        if "inventory_json" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN inventory_json TEXT NOT NULL DEFAULT '[]'")
        if "switches_json" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN switches_json TEXT NOT NULL DEFAULT '[]'")
        if "switches_seen" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN switches_seen INTEGER")
        if "inventory_seen" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN inventory_seen INTEGER")
        if "heartbeat_interval_seconds" not in node_columns:
            connection.execute("ALTER TABLE nodes ADD COLUMN heartbeat_interval_seconds INTEGER NOT NULL DEFAULT 60")
        connection.execute("""CREATE TABLE IF NOT EXISTS node_enrollment_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, hostname TEXT NOT NULL, expires_at INTEGER NOT NULL,
            used_at INTEGER, created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        connection.commit()
    initialize_tenancy(DATABASE_PATH, ADMIN_PASSWORD)
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS mcp_api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT, token_hash TEXT NOT NULL UNIQUE,
            label TEXT NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
            scopes_json TEXT NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER,
            last_used_at INTEGER, created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_mcp_tokens_user ON mcp_api_tokens(user_id,revoked_at,expires_at)")
        connection.execute("""CREATE TABLE IF NOT EXISTS node_commands (
            id TEXT PRIMARY KEY, node_id INTEGER NOT NULL REFERENCES nodes(id),
            user_id INTEGER, job_id TEXT, kind TEXT NOT NULL,
            payload_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
            attempts INTEGER NOT NULL DEFAULT 0, result_json TEXT, error TEXT,
            claimed_at INTEGER, lease_expires_at INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        command_columns = {row[1] for row in connection.execute("PRAGMA table_info(node_commands)").fetchall()}
        if "claimed_at" not in command_columns:
            connection.execute("ALTER TABLE node_commands ADD COLUMN claimed_at INTEGER")
        if "lease_expires_at" not in command_columns:
            connection.execute("ALTER TABLE node_commands ADD COLUMN lease_expires_at INTEGER")
        connection.execute("UPDATE node_commands SET claimed_at=COALESCE(claimed_at,CAST(strftime('%s','now') AS INTEGER)),lease_expires_at=COALESCE(lease_expires_at,CAST(strftime('%s','now') AS INTEGER)+180) WHERE status='running'")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_node_commands_queue ON node_commands(node_id,status,created_at)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_node_commands_lease ON node_commands(status,lease_expires_at)")
        connection.execute("""CREATE TABLE IF NOT EXISTS managed_networks (
            node_id INTEGER NOT NULL REFERENCES nodes(id), name TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('isolated','nat')), cidr TEXT,
            status TEXT NOT NULL CHECK(status IN ('provisioning','active','deleting')),
            created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(node_id,name))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS tenant_network_assignments (
            node_id INTEGER NOT NULL, network_name TEXT NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id), assigned_by INTEGER,
            assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(node_id,network_name))""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_tenant_network_assignments_user ON tenant_network_assignments(user_id,node_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_vm_ownership_network ON vm_ownership(node_id,network_name)")
        connection.commit()


def reap_expired_work(now: int | None = None) -> dict[str, int]:
    """Fail only work whose explicit worker or node-command lease has expired."""
    current = int(time.time()) if now is None else int(now)
    expired_commands: list[tuple[str, str | None, int]] = []
    expired_legacy: list[tuple[str, str]] = []
    with closing(sqlite3.connect(DATABASE_PATH, timeout=15)) as connection:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("BEGIN IMMEDIATE")
        expired_commands = connection.execute("""SELECT id,job_id,node_id FROM node_commands
            WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at<=?""", (current,)).fetchall()
        for command_id, job_id, node_id in expired_commands:
            cursor = connection.execute("""UPDATE node_commands SET status='failed',
                error='Node command lease expired; provider outcome must be reconciled.',
                lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='running' AND lease_expires_at<=?""", (command_id, current))
            if cursor.rowcount == 1 and job_id:
                connection.execute("""UPDATE provisioning_jobs SET status='failed',
                    error='Node command lease expired; provider outcome is unknown. Reconcile against a fresh node inventory.',
                    reconciliation_status='pending',worker_lease_owner=NULL,worker_lease_expires_at=NULL,
                    updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'""", (job_id,))
        expired_legacy = connection.execute("""SELECT id,vm_name FROM provisioning_jobs
            WHERE status='running' AND node_id IS NULL AND worker_lease_expires_at IS NOT NULL
            AND worker_lease_expires_at<=?""", (current,)).fetchall()
        for job_id, vm_name in expired_legacy:
            connection.execute("""UPDATE provisioning_jobs SET status='failed',
                error='Legacy provisioning worker lease expired; provider outcome is unknown and requires manual host inspection.',
                reconciliation_status='manual_required',reconciliation_note='Legacy single-agent jobs cannot be verified through enrolled-node inventory.',
                worker_lease_owner=NULL,worker_lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='running' AND node_id IS NULL AND worker_lease_expires_at<=?""", (job_id, current))
        connection.commit()
    for command_id, job_id, node_id in expired_commands:
        audit(DATABASE_PATH, {"id": None, "username": "system"}, "node.command.lease_expired", command_id,
              f"node_id={node_id}; job_id={job_id or ''}")
    for job_id, vm_name in expired_legacy:
        audit(DATABASE_PATH, {"id": None, "username": "system"}, "provisioning.legacy_lease_expired", vm_name, f"job_id={job_id}")
    return {"node_commands": len(expired_commands), "legacy_jobs": len(expired_legacy)}


@app.on_event("startup")
async def on_startup() -> None:
    global _worker_task, WORKER_INSTANCE_ID
    WORKER_INSTANCE_ID = uuid.uuid4().hex
    initialize_database()
    # Active work survives an API process restart. Only expired explicit leases
    # are failed; provider operations are never replayed automatically.
    reap_expired_work()
    _worker_task = asyncio.create_task(provisioning_worker())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _worker_task
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


def scheduler_node_snapshot(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute("SELECT id,name,status,last_seen,resources_json FROM nodes ORDER BY id").fetchall()
    nodes: list[dict[str, Any]] = []
    for row in rows:
        try:
            resources = json.loads(row[4] or "{}")
        except (ValueError, TypeError):
            resources = {}
        switch_row = connection.execute("SELECT switches_json,switches_seen FROM nodes WHERE id=?", (row[0],)).fetchone()
        try:
            switches = json.loads(switch_row[0] or "[]") if switch_row and switch_row[1] and int(time.time()) - int(switch_row[1]) <= 180 else []
        except (ValueError, TypeError):
            switches = []
        reserved = connection.execute("""SELECT COALESCE(SUM(cpu_count),0),
            COALESCE(SUM(memory_mb),0),COALESCE(SUM(disk_gb),0)
            FROM vm_ownership WHERE node_id=?""", (row[0],)).fetchone()
        resources["cpu_reserved"] = int(reserved[0])
        resources["memory_reserved_mb"] = int(reserved[1])
        resources["storage_reserved_gb"] = int(reserved[2])
        nodes.append({"id": row[0], "name": row[1], "status": row[2], "last_seen": row[3],
                      "switch_names": [item.get("name") for item in switches if isinstance(item, dict) and isinstance(item.get("name"), str)],
                      "resources": resources})
    return nodes


async def provisioning_worker() -> None:
    """Dispatch durable jobs with atomic claims and explicit worker leases."""
    next_reap = 0.0
    while True:
        monotonic_now = asyncio.get_running_loop().time()
        if monotonic_now >= next_reap:
            reap_expired_work()
            next_reap = monotonic_now + 5.0
        job = None
        with closing(sqlite3.connect(DATABASE_PATH, timeout=15)) as connection:
            connection.execute("PRAGMA busy_timeout=15000")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT id,user_id,request_json FROM provisioning_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
            if row:
                payload = json.loads(row[2])
                nodes = scheduler_node_snapshot(connection)
                if nodes:
                    selected = choose_node(nodes, cpu_count=int(payload["cpu_count"]), memory_mb=int(payload["memory_mb"]),
                                           disk_gb=int(payload["disk_gb"]), now=int(time.time()), provider=payload.get("provider"),
                                           preferred_node_id=payload.get("node_id"), switch_name=payload.get("switch_name"))
                    if selected:
                        node_id = int(selected["node_id"])
                        command_id = str(uuid.uuid4())
                        command_payload = {key: value for key, value in payload.items() if key not in {"provider", "node_id"}}
                        connection.execute("INSERT INTO node_commands(id,node_id,user_id,job_id,kind,payload_json,status) VALUES(?,?,?,?,?,?, 'queued')",
                                           (command_id, node_id, row[1], row[0], "vm.create", json.dumps(command_payload)))
                        connection.execute("UPDATE provisioning_jobs SET status='running',attempts=attempts+1,node_id=?,worker_lease_owner=NULL,worker_lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'", (node_id, row[0]))
                        connection.execute("UPDATE vm_ownership SET node_id=? WHERE vm_name=?", (node_id, payload["name"]))
                        job = ("node", row[0], command_id)
                elif agent_configured():
                    payload.pop("provider", None)
                    payload.pop("node_id", None)
                    cursor = connection.execute("UPDATE provisioning_jobs SET status='running',attempts=attempts+1,worker_lease_owner=?,worker_lease_expires_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
                                                (WORKER_INSTANCE_ID, int(time.time())+LEGACY_WORKER_LEASE_SECONDS, row[0]))
                    if cursor.rowcount == 1:
                        job = ("legacy", row[0], payload)
            connection.commit()
        if job is None:
            await asyncio.sleep(1)
            continue
        mode, job_id, work = job
        if mode == "node":
            # The selected agent claims this durable command over its outbound authenticated channel.
            await asyncio.sleep(0)
            continue
        try:
            result = await agent_request("POST", "/v1/vms", work)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection:
                connection.execute("UPDATE provisioning_jobs SET status='failed',error=?,worker_lease_owner=NULL,worker_lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running' AND worker_lease_owner=?",
                                    (f"{type(exc).__name__}: {str(exc)[:900]}", job_id, WORKER_INSTANCE_ID))
                connection.commit()
        else:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection:
                connection.execute("UPDATE provisioning_jobs SET status='succeeded',result_json=?,error=NULL,reconciliation_status='not_required',worker_lease_owner=NULL,worker_lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running' AND worker_lease_owner=?",
                                    (json.dumps(result, default=str)[:20000], job_id, WORKER_INSTANCE_ID))
                connection.commit()
            with closing(sqlite3.connect(DATABASE_PATH)) as connection:
                owner = connection.execute("SELECT j.user_id,j.vm_name,u.username FROM provisioning_jobs j JOIN users u ON u.id=j.user_id WHERE j.id=?", (job_id,)).fetchone()
            if owner:
                audit(DATABASE_PATH, {"id": owner[0], "username": owner[2]}, "vm.create", owner[1], f"job_id={job_id}")


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
    cpu_quota_cores: int = Field(default=8, ge=0, le=4096)
    memory_quota_mb: int = Field(default=16384, ge=0, le=4194304)
    storage_quota_gb: int = Field(default=200, ge=0, le=1000000)


class UserUpdateRequest(BaseModel):
    role: str = Field(pattern=r"^(admin|user)$")
    vm_quota: int = Field(ge=0, le=1000)
    cpu_quota_cores: int = Field(default=8, ge=0, le=4096)
    memory_quota_mb: int = Field(default=16384, ge=0, le=4194304)
    storage_quota_gb: int = Field(default=200, ge=0, le=1000000)
    active: bool


class CloudInitRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32, pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    password_hash: str = Field(min_length=90, max_length=200, pattern=r"^\$6\$(?:rounds=[0-9]+\$)?[A-Za-z0-9./]{1,16}\$[A-Za-z0-9./]{86}$")
    ssh_public_key: str = Field(min_length=40, max_length=8192, pattern=r"^(?:ssh-ed25519|ssh-rsa) [A-Za-z0-9+/=]+(?: [^\r\n]{0,100})?$")


class NodeHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cpu_total: int | None = Field(default=None, ge=1, le=1024)
    cpu_used: float | None = Field(default=None, ge=0, le=1024, allow_inf_nan=False)
    memory_total_mb: int | None = Field(default=None, ge=1, le=16777216)
    memory_used_mb: int | None = Field(default=None, ge=0, le=16777216)
    storage_total_gb: float | None = Field(default=None, ge=0, le=10000000, allow_inf_nan=False)
    storage_used_gb: float | None = Field(default=None, ge=0, le=10000000, allow_inf_nan=False)
    provider: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    agent_version: str | None = Field(default=None, min_length=1, max_length=64)
    capabilities: list[str] | None = Field(default=None, max_length=32)
    inventory: list[dict[str, Any]] | None = Field(default=None, max_length=1000)
    switches: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    active_command_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    heartbeat_interval_seconds: int | None = Field(default=None, ge=15, le=3600)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str] | None) -> list[str] | None:
        allowed = {"inventory.read", "host.metrics.read", "vm.create", "vm.power", "vm.delete", "vm.console", "network.manage"}
        if value is not None and (len(value) != len(set(value)) or any(item not in allowed for item in value)):
            raise ValueError("Node reported unknown or duplicate provider capabilities.")
        return value

    @field_validator("inventory")
    @classmethod
    def validate_inventory(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return value
        if any(not isinstance(item.get("name"), str) or not item["name"] or len(item["name"]) > 128 for item in value):
            raise ValueError("Every VM inventory item requires a bounded name.")
        return value

    @field_validator("switches")
    @classmethod
    def validate_switches(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        if value is None:
            return value
        if any(not isinstance(item.get("name"), str) or not item["name"] or len(item["name"]) > 128 for item in value):
            raise ValueError("Every virtual switch requires a bounded name.")
        return value

    @field_validator("cpu_used")
    @classmethod
    def validate_cpu_usage(cls, value: float | None, info):
        total = info.data.get("cpu_total")
        if value is not None and total is not None and value > total:
            raise ValueError("CPU usage cannot exceed reported logical CPU count.")
        return value

    @field_validator("memory_used_mb")
    @classmethod
    def validate_memory_usage(cls, value: int, info):
        total = info.data.get("memory_total_mb")
        if value is not None and total is not None and value > total:
            raise ValueError("Memory usage cannot exceed reported total memory.")
        return value

    @field_validator("storage_used_gb")
    @classmethod
    def validate_storage_usage(cls, value: float, info):
        total = info.data.get("storage_total_gb")
        if value is not None and total is not None and value > total:
            raise ValueError("Storage usage cannot exceed reported total storage.")
        return value


class CreateNetworkRequest(BaseModel):
    node_id: int = Field(ge=1)
    name: str = Field(min_length=2, max_length=63, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,62}$")
    mode: str = Field(pattern=r"^(isolated|nat)$")
    cidr: str | None = Field(default=None, max_length=32)


class NetworkAssignmentRequest(BaseModel):
    user_id: int = Field(ge=1)


class ProvisioningReconcileRequest(BaseModel):
    action: str = Field(pattern=r"^(confirm_present|confirm_absent)$")


class NodeCommandCompleteRequest(BaseModel):
    status: str = Field(pattern=r"^(succeeded|failed)$")
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=1000)


class MCPTokenCreateRequest(BaseModel):
    user_id: int = Field(ge=1)
    label: str = Field(min_length=2, max_length=80)
    scopes: list[str] = Field(min_length=1, max_length=4)
    expires_in_days: int = Field(default=30, ge=1, le=365)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise ValueError("Token label must contain at least two non-space characters.")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("MCP scopes must not contain duplicates.")
        unknown = sorted(set(value) - MCP_SCOPES)
        if unknown:
            raise ValueError("Unknown MCP scopes: " + ", ".join(unknown))
        return value


class NodeEnrollmentRequest(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    hostname: str = Field(min_length=1, max_length=253)


class NodeEnrollRequest(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class PlacementRequest(BaseModel):
    cpu_count: int = Field(ge=1, le=256)
    memory_mb: int = Field(ge=512, le=1048576)
    disk_gb: int = Field(ge=1, le=100000)
    provider: str | None = Field(default=None, max_length=32)
    preferred_node_id: int | None = Field(default=None, ge=1)
    switch_name: str | None = Field(default=None, max_length=100)


class CreateVMRequest(BaseModel):
    name: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")
    provider: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    memory_mb: int = Field(default=2048, ge=512, le=32768)
    disk_gb: int = Field(default=20, ge=8, le=500)
    cpu_count: int = Field(default=2, ge=1, le=16)
    iso_path: str = Field(min_length=5, max_length=260)
    switch_name: str | None = Field(default=None, max_length=100)
    node_id: int | None = Field(default=None, ge=1)
    cloud_init: CloudInitRequest | None = None


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "virtuewise-vps-api", "version": "0.10.0"}


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
        rows = connection.execute("SELECT id,username,role,vm_quota,cpu_quota_cores,memory_quota_mb,storage_quota_gb,active,created_at FROM users ORDER BY id").fetchall()
    return {"items": [dict(row) for row in rows]}


@app.post("/api/users", tags=["users"])
def create_user(request: UserCreateRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            cursor = connection.execute("INSERT INTO users(username,password_hash,role,vm_quota,cpu_quota_cores,memory_quota_mb,storage_quota_gb) VALUES(?,?,?,?,?,?,?)", (request.username, hash_password(request.password), request.role, request.vm_quota, request.cpu_quota_cores, request.memory_quota_mb, request.storage_quota_gb))
            connection.commit()
    except sqlite3.IntegrityError: raise HTTPException(status_code=409, detail="Username already exists.") from None
    audit(DATABASE_PATH, admin, "user.create", request.username, f"role={request.role}; quota={request.vm_quota}")
    return {"id": cursor.lastrowid, "username": request.username, "role": request.role, "vm_quota": request.vm_quota, "cpu_quota_cores": request.cpu_quota_cores, "memory_quota_mb": request.memory_quota_mb, "storage_quota_gb": request.storage_quota_gb}


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
        connection.execute("UPDATE users SET role=?,vm_quota=?,cpu_quota_cores=?,memory_quota_mb=?,storage_quota_gb=?,active=? WHERE id=?", (request.role, request.vm_quota, request.cpu_quota_cores, request.memory_quota_mb, request.storage_quota_gb, int(request.active), user_id))
        connection.commit()
    audit(DATABASE_PATH, admin, "user.update", target[1], f"role={request.role}; quota={request.vm_quota}; active={request.active}")
    return {"id": user_id, "username": target[1], "role": request.role, "vm_quota": request.vm_quota, "cpu_quota_cores": request.cpu_quota_cores, "memory_quota_mb": request.memory_quota_mb, "storage_quota_gb": request.storage_quota_gb, "active": request.active}


@app.post("/api/users/{user_id}/transfer-vm", tags=["users"])
def transfer_vm_ownership(user_id: int, vm_name: str, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute("SELECT id,username,active,role FROM users WHERE id=?", (user_id,)).fetchone()
        if not user or not user[2]: raise HTTPException(status_code=404, detail="Active destination user not found.")
        vm = connection.execute("SELECT vm_name,node_id,network_name FROM vm_ownership WHERE vm_name=?", (vm_name,)).fetchone()
        if not vm: raise HTTPException(status_code=404, detail="Owned VM record not found.")
        if vm[2] and user[3] == "user":
            assignment = connection.execute("SELECT 1 FROM tenant_network_assignments WHERE user_id=? AND node_id=? AND network_name=?", (user_id, vm[1], vm[2])).fetchone()
            if not assignment:
                raise HTTPException(status_code=409, detail="Destination customer is not assigned the VM's attached network.")
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
        connection.execute("DELETE FROM tenant_network_assignments WHERE user_id=?", (user_id,))
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
        raise HTTPException(status_code=503, detail="Node agent is not configured. Set VPS_AGENT_URL and VPS_AGENT_TOKEN.")
    parsed = urlparse(AGENT_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=503, detail="Invalid VPS_AGENT_URL configuration.")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(status_code=503, detail="HTTP is only permitted for a loopback agent. Use HTTPS for remote nodes.")
    try:
        async with httpx.AsyncClient(timeout=AGENT_TIMEOUT) as client:
            response = await client.request(method, f"{AGENT_URL}{path}", json=payload, headers={"Authorization": f"Bearer {AGENT_TOKEN}"})
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Node agent request timed out.") from None
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not connect to the node agent.") from None
    if response.status_code >= 400:
        detail = "Node agent rejected the request."
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    try:
        return response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Node agent returned an invalid response.") from None


def live_node_rows() -> list[sqlite3.Row]:
    cutoff = int(time.time()) - 180
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT id,name,hostname,status,last_seen,resources_json,inventory_json,switches_json,switches_seen FROM nodes WHERE status='online' AND last_seen>=? ORDER BY id", (cutoff,)).fetchall()


@app.get("/api/agent/status", tags=["agent"], dependencies=[Depends(require_user)])
async def get_agent_status() -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        registered = connection.execute("SELECT COUNT(*) FROM nodes WHERE status!='revoked'").fetchone()[0]
    if registered:
        live = live_node_rows()
        return {"configured": True, "online": bool(live), "nodes_online": len(live),
                "nodes_registered": registered, "message": "Node agents connected" if live else "Waiting for enrolled nodes to heartbeat"}
    if not agent_configured():
        return {"configured": False, "online": False, "message": "No enrolled node or legacy agent connection configured"}
    try:
        result = await agent_request("GET", "/health")
        return {"configured": True, "online": result.get("status") == "ok", "agent": result}
    except HTTPException as exc:
        return {"configured": True, "online": False, "message": str(exc.detail)}


@app.get("/api/vms", tags=["instances"])
async def list_vms(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    live = live_node_rows()
    if live:
        items: list[dict[str, Any]] = []
        for node in live:
            try:
                inventory = json.loads(node["inventory_json"] or "[]")
            except (ValueError, TypeError):
                inventory = []
            for vm in inventory:
                if isinstance(vm, dict) and isinstance(vm.get("name"), str):
                    items.append({**vm, "node_id": node["id"], "node_name": node["name"]})
        result = {"items": items}
    else:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            registered = connection.execute("SELECT COUNT(*) FROM nodes WHERE status!='revoked'").fetchone()[0]
        if registered:
            result = {"items": []}
        elif agent_configured():
            result = await agent_request("GET", "/v1/vms")
        else:
            result = {"items": []}
    if user["role"] == "admin":
        return result
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        owned = {row[0] for row in connection.execute("SELECT vm_name FROM vm_ownership WHERE user_id=?", (user["id"],)).fetchall()}
    result["items"] = [vm for vm in result.get("items", []) if vm.get("name") in owned]
    return result


@app.get("/api/switches", tags=["agent"])
async def list_switches(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    assigned: set[tuple[int, str]] | None = None
    if user["role"] != "admin":
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            rows = connection.execute("""SELECT a.node_id,a.network_name FROM tenant_network_assignments a
                JOIN managed_networks m ON m.node_id=a.node_id AND m.name=a.network_name
                JOIN users u ON u.id=a.user_id AND u.active=1
                WHERE a.user_id=? AND m.status='active'""", (user["id"],)).fetchall()
        assigned = {(int(row[0]), str(row[1])) for row in rows}
    live = live_node_rows()
    if live:
        items: list[dict[str, Any]] = []
        for node in live:
            if not node["switches_seen"] or int(time.time()) - int(node["switches_seen"]) > 180:
                continue
            try:
                switches = json.loads(node["switches_json"] or "[]")
            except (ValueError, TypeError):
                switches = []
            items.extend({**item, "node_id": node["id"], "node_name": node["name"]} for item in switches
                         if isinstance(item, dict) and (assigned is None or (int(node["id"]), str(item.get("name"))) in assigned))
        return {"items": items}
    if user["role"] != "admin":
        return {"items": []}
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        registered = connection.execute("SELECT COUNT(*) FROM nodes WHERE status!='revoked'").fetchone()[0]
    if registered:
        return {"items": []}
    return await agent_request("GET", "/v1/switches")


@app.get("/api/monitor", tags=["monitoring"])
async def get_monitor(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if user["role"] != "admin":
        return {"restricted": True, "message": "Host-level resource telemetry is administrator-only."}
    live = live_node_rows()
    if live:
        resources = []
        for node in live:
            try:
                resources.append(json.loads(node["resources_json"] or "{}"))
            except (ValueError, TypeError):
                continue
        cpu_total = sum(float(item.get("cpu_total", 0) or 0) for item in resources)
        cpu_used = sum(float(item.get("cpu_used", 0) or 0) for item in resources)
        memory_total = sum(int(item.get("memory_total_mb", 0) or 0) for item in resources)
        memory_used = sum(int(item.get("memory_used_mb", 0) or 0) for item in resources)
        storage_total = sum(float(item.get("storage_total_gb", 0) or 0) for item in resources)
        storage_used = sum(float(item.get("storage_used_gb", 0) or 0) for item in resources)
        return {"cpu_percent": round(cpu_used / cpu_total * 100, 1) if cpu_total else 0,
                "memory_total_mb": memory_total, "memory_free_mb": max(0, memory_total-memory_used),
                "storage_total_gb": storage_total, "storage_free_gb": max(0, storage_total-storage_used),
                "nodes_online": len(live)}
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        registered = connection.execute("SELECT COUNT(*) FROM nodes WHERE status!='revoked'").fetchone()[0]
    if registered:
        return {"cpu_percent": 0, "memory_total_mb": 0, "memory_free_mb": 0,
                "storage_total_gb": 0, "storage_free_gb": 0, "nodes_online": 0}
    return await agent_request("GET", "/v1/monitor")


@app.post("/api/vms", tags=["instances"], status_code=202)
def create_vm(request: CreateVMRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
              user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    """Reserve customer quota and enqueue VM provisioning with optional per-user idempotency."""
    if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", idempotency_key):
        raise HTTPException(status_code=422, detail="Idempotency-Key must be 8-128 characters using letters, digits, dot, underscore, colon or hyphen.")
    request_hash = hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    job_id = str(uuid.uuid4())
    with closing(sqlite3.connect(DATABASE_PATH, timeout=15)) as connection:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("BEGIN IMMEDIATE")
        if idempotency_key is not None:
            previous = connection.execute("SELECT id,status,request_hash FROM provisioning_jobs WHERE user_id=? AND idempotency_key=?",
                                          (user["id"], idempotency_key)).fetchone()
            if previous:
                connection.rollback()
                if previous[2] != request_hash:
                    raise HTTPException(status_code=409, detail="This Idempotency-Key was already used with a different VM request. Use a new key for a new request.")
                return {"accepted": True, "provisioning_job_id": previous[0], "status": previous[1],
                        "replayed": True, "message": "This request was already accepted; returning its original provisioning job."}
        if connection.execute("SELECT 1 FROM vm_ownership WHERE vm_name=?", (request.name,)).fetchone():
            connection.rollback()
            raise HTTPException(status_code=409, detail="A VM with this name is already recorded.")
        if request.switch_name is not None and request.node_id is None:
            enrolled = connection.execute("SELECT COUNT(*) FROM nodes WHERE status!='revoked'").fetchone()[0]
            if user["role"] != "admin" or enrolled or not agent_configured():
                connection.rollback()
                raise HTTPException(status_code=422, detail="A VM network attachment must identify its enrolled compute node.")
        if user["role"] == "admin" and request.switch_name is not None:
            managed = connection.execute("SELECT status FROM managed_networks WHERE node_id=? AND name=?", (request.node_id, request.switch_name)).fetchone()
            if managed and managed[0] != "active":
                connection.rollback()
                raise HTTPException(status_code=409, detail="This managed network is not in an active state.")
        if user["role"] != "admin" and request.switch_name is not None:
            if request.node_id is None:
                connection.rollback()
                raise HTTPException(status_code=403, detail="A customer network attachment must identify its assigned compute node.")
            assigned = connection.execute("""SELECT n.switches_json,n.switches_seen,n.status,m.status
                FROM tenant_network_assignments a JOIN managed_networks m
                ON m.node_id=a.node_id AND m.name=a.network_name
                JOIN nodes n ON n.id=a.node_id
                WHERE a.user_id=? AND a.node_id=? AND a.network_name=?""",
                (user["id"], request.node_id, request.switch_name)).fetchone()
            if not assigned or assigned[2] != "online" or assigned[3] != "active" or not assigned[1] or int(time.time()) - int(assigned[1]) > 180:
                connection.rollback()
                raise HTTPException(status_code=403, detail="This network is not assigned to your account on an online node.")
            try:
                reported = json.loads(assigned[0] or "[]")
            except (ValueError, TypeError):
                reported = []
            if not any(isinstance(item, dict) and item.get("name") == request.switch_name for item in reported):
                connection.rollback()
                raise HTTPException(status_code=409, detail="Assigned network is not present in the latest provider inventory.")
        if user["role"] != "admin":
            limits = connection.execute("SELECT vm_quota,cpu_quota_cores,memory_quota_mb,storage_quota_gb FROM users WHERE id=? AND active=1", (user["id"],)).fetchone()
            if not limits:
                connection.rollback()
                raise HTTPException(status_code=403, detail="Account is inactive.")
            used = connection.execute("SELECT COUNT(*),COALESCE(SUM(cpu_count),0),COALESCE(SUM(memory_mb),0),COALESCE(SUM(disk_gb),0) FROM vm_ownership WHERE user_id=?", (user["id"],)).fetchone()
            requested = (1, request.cpu_count, request.memory_mb, request.disk_gb)
            labels = ("VM", "CPU core", "MB memory", "GB storage")
            for limit, current, amount, label in zip(limits, used, requested, labels):
                if limit >= 0 and current + amount > limit:
                    connection.rollback()
                    raise HTTPException(status_code=403, detail=f"Your {label} quota would be exceeded.")
        node_count = connection.execute("SELECT COUNT(*) FROM nodes WHERE status!='revoked'").fetchone()[0]
        if not agent_configured() and not node_count:
            connection.rollback()
            raise HTTPException(status_code=503, detail="No node is enrolled and no legacy VPS_AGENT_URL/VPS_AGENT_TOKEN is configured.")
        connection.execute("INSERT INTO vm_ownership(vm_name,user_id,cpu_count,memory_mb,disk_gb,node_id,network_name) VALUES(?,?,?,?,?,?,?)",
                           (request.name, user["id"], request.cpu_count, request.memory_mb, request.disk_gb, request.node_id, request.switch_name))
        connection.execute("INSERT INTO provisioning_jobs(id,user_id,vm_name,status,request_json,idempotency_key,request_hash) VALUES(?,?,?,'queued',?,?,?)",
                           (job_id, user["id"], request.name, json.dumps(request.model_dump()), idempotency_key, request_hash if idempotency_key is not None else None))
        connection.commit()
    audit(DATABASE_PATH, user, "vm.provisioning_queued", request.name, f"job_id={job_id}")
    return {"accepted": True, "provisioning_job_id": job_id, "status": "queued", "message": "VM provisioning has been queued. Poll the job status endpoint for progress."}


@app.get("/api/jobs/{job_id}", tags=["jobs"])
def get_provisioning_job(job_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT id,user_id,vm_name,status,attempts,error,created_at,updated_at,result_json,node_id,reconciliation_status,reconciled_at,reconciliation_note FROM provisioning_jobs WHERE id=?", (job_id,)).fetchone()
    if not row or (user["role"] != "admin" and row["user_id"] != user["id"]):
        raise HTTPException(status_code=404, detail="Provisioning job not found.")
    item = dict(row)
    item["result"] = json.loads(item.pop("result_json")) if item.get("result_json") else None
    return item


@app.get("/api/admin/provisioning-jobs", tags=["jobs"])
def list_reconciliation_jobs(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""SELECT j.id,j.vm_name,j.user_id,u.username,j.status,j.attempts,j.error,
            j.created_at,j.updated_at,j.node_id,n.name AS node_name,n.status AS node_status,n.last_seen,n.inventory_seen,
            j.reconciliation_status FROM provisioning_jobs j
            JOIN users u ON u.id=j.user_id LEFT JOIN nodes n ON n.id=j.node_id
            WHERE j.status='failed' AND j.reconciliation_status='pending' AND j.node_id IS NOT NULL
            ORDER BY j.updated_at DESC LIMIT 100""").fetchall()
    now = int(time.time())
    items = []
    for row in rows:
        item = dict(row)
        heartbeat_age = now-int(item["last_seen"]) if item["last_seen"] else None
        inventory_age = now-int(item["inventory_seen"]) if item["inventory_seen"] else None
        item["can_reconcile"] = bool(item["node_status"] != "revoked" and heartbeat_age is not None and 0 <= heartbeat_age <= 180
                                      and inventory_age is not None and 0 <= inventory_age <= 180)
        items.append(item)
    return {"items": items}


@app.post("/api/admin/provisioning-jobs/{job_id}/reconcile", tags=["jobs"])
def reconcile_provisioning_job(job_id: str, request: ProvisioningReconcileRequest,
                               admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Resolve an ambiguous VM-create result only against a fresh inventory from its assigned node."""
    with closing(sqlite3.connect(DATABASE_PATH, timeout=15)) as connection:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("BEGIN IMMEDIATE")
        job = connection.execute("""SELECT user_id,vm_name,status,node_id,reconciliation_status
            FROM provisioning_jobs WHERE id=?""", (job_id,)).fetchone()
        if not job:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Provisioning job not found.")
        if job[2] != "failed" or job[4] != "pending" or job[3] is None:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Only unresolved failed jobs assigned to an enrolled node can be reconciled.")
        node = connection.execute("SELECT status,last_seen,inventory_json,inventory_seen FROM nodes WHERE id=?", (job[3],)).fetchone()
        now = int(time.time())
        if not node or node[0] == "revoked" or not node[1] or not 0 <= now-int(node[1]) <= 180:
            connection.rollback()
            raise HTTPException(status_code=409, detail="The assigned node must report a fresh heartbeat before reconciliation.")
        if not node[3] or not 0 <= now-int(node[3]) <= 180:
            connection.rollback()
            raise HTTPException(status_code=409, detail="The assigned node must report a fresh VM inventory snapshot; a heartbeat without inventory is not sufficient.")
        try:
            inventory = json.loads(node[2] or "[]")
        except (ValueError, TypeError):
            connection.rollback()
            raise HTTPException(status_code=409, detail="The assigned node inventory is invalid; reconciliation is blocked.") from None
        if not isinstance(inventory, list) or any(not isinstance(item, dict) for item in inventory):
            connection.rollback()
            raise HTTPException(status_code=409, detail="The assigned node inventory is incomplete; reconciliation is blocked.")
        present = any(item.get("name") == job[1] for item in inventory)
        if request.action == "confirm_present" and not present:
            connection.rollback()
            raise HTTPException(status_code=409, detail="The VM is not present in the assigned node's latest inventory. Refresh inventory and verify before choosing an action.")
        if request.action == "confirm_absent" and present:
            connection.rollback()
            raise HTTPException(status_code=409, detail="The VM is present in the assigned node's latest inventory; it cannot be marked absent.")
        if present:
            result = {"name": job[1], "node_id": job[3], "reconciled": True, "inventory_verified": True}
            connection.execute("""UPDATE provisioning_jobs SET status='succeeded',result_json=?,error=NULL,
                reconciliation_status='present',reconciled_by=?,reconciled_at=CURRENT_TIMESTAMP,
                reconciliation_note='VM confirmed present in fresh assigned-node inventory',updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND status='failed' AND reconciliation_status='pending'""",
                (json.dumps(result), admin["id"], job_id))
            action = "provisioning.reconcile_present"
        else:
            connection.execute("DELETE FROM vm_ownership WHERE vm_name=? AND user_id=? AND node_id=?",
                               (job[1], job[0], job[3]))
            connection.execute("""UPDATE provisioning_jobs SET reconciliation_status='absent',
                reconciled_by=?,reconciled_at=CURRENT_TIMESTAMP,
                reconciliation_note='VM confirmed absent in fresh assigned-node inventory; ownership reservation released',
                updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='failed' AND reconciliation_status='pending'""",
                (admin["id"], job_id))
            action = "provisioning.reconcile_absent"
        connection.commit()
    audit(DATABASE_PATH, admin, action, job[1], f"job_id={job_id}; node_id={job[3]}")
    return {"job_id": job_id, "vm_name": job[1], "node_id": job[3],
            "reconciliation_status": "present" if present else "absent",
            "job_status": "succeeded" if present else "failed",
            "reservation_released": not present,
            "message": "VM exists; job marked successful." if present else "VM is absent; ownership and quota reservation released. A new provisioning request may now be submitted."}



def authorize_vm(name: str, user: dict[str, Any]) -> int | None:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        row = connection.execute("SELECT user_id,node_id FROM vm_ownership WHERE vm_name=?", (name,)).fetchone()
    if not row:
        if user["role"] == "admin":
            for node in live_node_rows():
                try:
                    inventory = json.loads(node["inventory_json"] or "[]")
                except (ValueError, TypeError):
                    inventory = []
                if any(isinstance(vm, dict) and vm.get("name") == name for vm in inventory):
                    return node["id"]
            return None
        raise HTTPException(status_code=404, detail="VM not found.")
    if user["role"] != "admin" and row[0] != user["id"]:
        raise HTTPException(status_code=404, detail="VM not found.")
    return row[1]


async def dispatch_node_command(node_id: int, user_id: int, kind: str, payload: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    command_id = str(uuid.uuid4())
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("INSERT INTO node_commands(id,node_id,user_id,kind,payload_json,status) VALUES(?,?,?,?,?,'queued')",
                           (command_id, node_id, user_id, kind, json.dumps(payload)))
        connection.commit()
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            row = connection.execute("SELECT status,result_json,error FROM node_commands WHERE id=?", (command_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Node command record disappeared.")
        if row[0] == "succeeded":
            return json.loads(row[1]) if row[1] else {"accepted": True}
        if row[0] == "failed":
            raise HTTPException(status_code=502, detail=f"Node command failed: {row[2] or 'provider error'}")
        await asyncio.sleep(0.25)
    raise HTTPException(status_code=504, detail=f"Node command {command_id} is still pending; do not repeat the operation. Check GET /api/node-commands/{command_id}.")


@app.get("/api/node-commands/{command_id}", tags=["nodes"])
def get_node_command(command_id: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        row = connection.execute("SELECT id,node_id,user_id,kind,status,error,result_json,created_at,updated_at FROM node_commands WHERE id=?", (command_id,)).fetchone()
    if not row or (user["role"] != "admin" and row[2] != user["id"]):
        raise HTTPException(status_code=404, detail="Node command not found.")
    return {"id": row[0], "node_id": row[1], "kind": row[3], "status": row[4], "error": row[5],
            "result": json.loads(row[6]) if row[6] else None, "created_at": row[7], "updated_at": row[8]}


@app.post("/api/vms/{name}/start", tags=["instances"])
async def start_vm(name: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    node_id = authorize_vm(name, user)
    result = await dispatch_node_command(node_id, user["id"], "vm.start", {"name": name}) if node_id else await agent_request("POST", "/v1/vms/start", {"name": name})
    audit(DATABASE_PATH, user, "vm.start", name)
    return result


@app.post("/api/vms/{name}/stop", tags=["instances"])
async def stop_vm(name: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    node_id = authorize_vm(name, user)
    result = await dispatch_node_command(node_id, user["id"], "vm.stop", {"name": name}) if node_id else await agent_request("POST", "/v1/vms/stop", {"name": name})
    audit(DATABASE_PATH, user, "vm.stop", name)
    return result


@app.post("/api/vms/{name}/console", tags=["instances"])
async def open_console(name: str, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    node_id = authorize_vm(name, user)
    if node_id:
        return await dispatch_node_command(node_id, user["id"], "vm.console", {"name": name})
    return await agent_request("POST", f"/v1/vms/{name}/console")


@app.delete("/api/vms/{name}", tags=["instances"])
async def delete_vm(name: str, confirm_name: str, delete_disks: bool = False, user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if confirm_name != name:
        raise HTTPException(status_code=400, detail="Deletion confirmation must exactly match the VM name.")
    node_id = authorize_vm(name, user)
    if node_id:
        result = await dispatch_node_command(node_id, user["id"], "vm.delete", {"name": name, "confirm_name": confirm_name, "delete_disks": delete_disks})
    else:
        result = await agent_request("DELETE", f"/v1/vms/{name}", {"name": name, "confirm_name": confirm_name, "delete_disks": delete_disks})
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("DELETE FROM vm_ownership WHERE vm_name=?", (name,))
        connection.commit()
    audit(DATABASE_PATH, user, "vm.delete", name, f"delete_disks={delete_disks}")
    return result


@app.get("/api/nodes", tags=["nodes"])
def list_nodes(user: dict[str, Any] = Depends(require_user)) -> dict[str, list[dict[str, Any]]]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT id,name,hostname,status,created_at,last_seen,resources_json,inventory_json FROM nodes ORDER BY id").fetchall()
    items = []
    now = int(time.time())
    for row in rows:
        item = dict(row)
        item["resources"] = json.loads(item.pop("resources_json") or "{}")
        inventory = json.loads(item.pop("inventory_json") or "[]")
        item["vm_count"] = len(inventory) if isinstance(inventory, list) else 0
        if item["status"] != "revoked" and (not item["last_seen"] or now-item["last_seen"] > 180):
            item["status"] = "offline"
        if user["role"] != "admin":
            item = {key: item[key] for key in ("id", "name", "status", "vm_count")}
        items.append(item)
    return {"items": items}


@app.post("/api/scheduler/placement", tags=["scheduler"])
def preview_node_placement(request: PlacementRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Preview eligible node placement; does not provision or reserve capacity."""
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        nodes = scheduler_node_snapshot(connection)
    selected = choose_node(nodes, cpu_count=request.cpu_count, memory_mb=request.memory_mb,
                           disk_gb=request.disk_gb, now=int(time.time()), provider=request.provider,
                           preferred_node_id=request.preferred_node_id, switch_name=request.switch_name)
    if selected is None:
        raise HTTPException(status_code=409, detail="No fresh, online node has sufficient reported capacity.")
    return {"placement": selected, "reserved": False, "note": "Preview only; actual placement is reserved transactionally when the provisioning worker dispatches the job."}


@app.post("/api/mcp/tokens", tags=["mcp"])
def create_mcp_token(request: MCPTokenCreateRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Create a customer- or administrator-scoped read-only MCP bearer token; reveal it once."""
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        target = connection.execute("SELECT id,username,role,active FROM users WHERE id=?", (request.user_id,)).fetchone()
        if not target or not target[3]:
            raise HTTPException(status_code=404, detail="Active token owner not found.")
        if target[2] != "admin" and set(request.scopes) & MCP_ADMIN_SCOPES:
            raise HTTPException(status_code=422, detail="Customer tokens cannot receive infrastructure-wide node or host-resource scopes.")
        token = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + request.expires_in_days * 86400
        cursor = connection.execute(
            "INSERT INTO mcp_api_tokens(token_hash,label,user_id,scopes_json,expires_at,created_by) VALUES(?,?,?,?,?,?)",
            (hashlib.sha256(token.encode("utf-8")).hexdigest(), request.label.strip(), request.user_id,
             json.dumps(sorted(request.scopes)), expires_at, admin["id"]),
        )
        connection.commit()
        token_id = cursor.lastrowid
    audit(DATABASE_PATH, admin, "mcp.token.create", str(token_id), f"owner={target[1]}; scopes={','.join(sorted(request.scopes))}; expires_at={expires_at}")
    return {"id": token_id, "label": request.label.strip(), "user_id": request.user_id,
            "username": target[1], "scopes": sorted(request.scopes), "expires_at": expires_at,
            "token": token, "warning": "Store this token securely; it will not be shown again."}


@app.get("/api/mcp/tokens", tags=["mcp"])
def list_mcp_tokens(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("""SELECT t.id,t.label,t.user_id,u.username,t.scopes_json,t.expires_at,
            t.revoked_at,t.last_used_at,t.created_at FROM mcp_api_tokens t
            JOIN users u ON u.id=t.user_id ORDER BY t.id DESC""").fetchall()
    return {"items": [{**{key: row[key] for key in row.keys() if key != "scopes_json"},
                       "scopes": json.loads(row["scopes_json"])} for row in rows]}


@app.post("/api/mcp/tokens/{token_id}/revoke", tags=["mcp"])
def revoke_mcp_token(token_id: int, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        row = connection.execute("SELECT label,revoked_at FROM mcp_api_tokens WHERE id=?", (token_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="MCP token not found.")
        if row[1] is None:
            connection.execute("UPDATE mcp_api_tokens SET revoked_at=? WHERE id=?", (int(time.time()), token_id))
            connection.commit()
    if row[1] is None:
        audit(DATABASE_PATH, admin, "mcp.token.revoke", str(token_id), row[0])
    return {"id": token_id, "revoked": True}


def require_mcp_read_token(request: Request) -> dict[str, Any]:
    """Authenticate a global admin token or a hashed, scoped, expiring customer token."""
    if request.method != "GET":
        raise HTTPException(status_code=405, detail="MCP integration credential is read-only.")
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not supplied:
        raise HTTPException(status_code=401, detail="MCP read credential is invalid.")
    if len(MCP_READ_TOKEN) >= 32 and hmac.compare_digest(supplied, MCP_READ_TOKEN):
        return {"token_id": None, "user_id": None, "role": "admin", "scopes": set(MCP_SCOPES), "global": True}
    token_hash = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        row = connection.execute("""SELECT t.id,t.user_id,t.scopes_json,t.expires_at,t.revoked_at,u.role,u.active
            FROM mcp_api_tokens t JOIN users u ON u.id=t.user_id WHERE t.token_hash=?""", (token_hash,)).fetchone()
        if not row or row[4] is not None or row[3] <= int(time.time()) or not row[6]:
            raise HTTPException(status_code=401, detail="MCP read credential is invalid, expired, revoked, or inactive.")
        try:
            scopes = set(json.loads(row[2]))
        except (ValueError, TypeError):
            raise HTTPException(status_code=401, detail="MCP credential metadata is invalid.") from None
        if not scopes or not scopes <= MCP_SCOPES or (row[5] != "admin" and scopes & MCP_ADMIN_SCOPES):
            raise HTTPException(status_code=403, detail="MCP credential scopes are no longer permitted for this account.")
        connection.execute("UPDATE mcp_api_tokens SET last_used_at=? WHERE id=?", (int(time.time()), row[0]))
        connection.commit()
    return {"token_id": row[0], "user_id": row[1], "role": row[5], "scopes": scopes, "global": False}


def require_mcp_scope(context: dict[str, Any], scope: str) -> dict[str, Any]:
    if scope not in context["scopes"]:
        raise HTTPException(status_code=403, detail=f"MCP token requires the {scope} scope.")
    return context


@app.get("/api/integrations/mcp/health", tags=["mcp"])
async def mcp_health(context: dict[str, Any] = Depends(require_mcp_read_token)) -> dict[str, Any]:
    require_mcp_scope(context, "health.read")
    if context["role"] != "admin":
        return {"control_plane": "ok"}
    live_nodes = live_node_rows()
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        registered_nodes = connection.execute("SELECT COUNT(*) FROM nodes WHERE status!='revoked'").fetchone()[0]
    if registered_nodes:
        return {"control_plane": "ok", "agent_configured": True, "agent_online": bool(live_nodes),
                "nodes_online": len(live_nodes), "nodes_registered": registered_nodes}
    result: dict[str, Any] = {"control_plane": "ok", "agent_configured": agent_configured(), "agent_online": False}
    if agent_configured():
        try:
            agent = await agent_request("GET", "/health")
            result["agent_online"] = agent.get("status") == "ok"
            result["agent"] = agent
        except HTTPException as exc:
            result["agent_error"] = str(exc.detail)
    return result


@app.get("/api/integrations/mcp/nodes", tags=["mcp"])
def mcp_nodes(context: dict[str, Any] = Depends(require_mcp_read_token)) -> dict[str, Any]:
    require_mcp_scope(context, "nodes.read")
    return list_nodes()


@app.get("/api/integrations/mcp/nodes/{node_id}", tags=["mcp"])
def mcp_node_detail(node_id: int, context: dict[str, Any] = Depends(require_mcp_read_token)) -> dict[str, Any]:
    require_mcp_scope(context, "nodes.read")
    for node in list_nodes()["items"]:
        if node["id"] == node_id:
            return node
    raise HTTPException(status_code=404, detail="Node not found.")


@app.get("/api/integrations/mcp/vms", tags=["mcp"])
async def mcp_vms(context: dict[str, Any] = Depends(require_mcp_read_token)) -> dict[str, Any]:
    require_mcp_scope(context, "vms.read")
    return await list_vms({"id": context["user_id"], "role": context["role"]})


@app.get("/api/integrations/mcp/vms/{name}", tags=["mcp"])
async def mcp_vm_detail(name: str, context: dict[str, Any] = Depends(require_mcp_read_token)) -> dict[str, Any]:
    require_mcp_scope(context, "vms.read")
    if context["role"] != "admin":
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            owned = connection.execute("SELECT 1 FROM vm_ownership WHERE user_id=? AND vm_name=?", (context["user_id"], name)).fetchone()
        if not owned:
            raise HTTPException(status_code=404, detail="Virtual machine not found.")
    inventory = await list_vms({"id": context["user_id"], "role": context["role"]})
    items = inventory.get("items", []) if isinstance(inventory, dict) else inventory
    for vm in items:
        if isinstance(vm, dict) and vm.get("name") == name:
            return vm
    raise HTTPException(status_code=404, detail="Virtual machine not found.")


@app.get("/api/integrations/mcp/resources", tags=["mcp"])
async def mcp_resources(context: dict[str, Any] = Depends(require_mcp_read_token)) -> dict[str, Any]:
    require_mcp_scope(context, "resources.read")
    if context["role"] != "admin":
        raise HTTPException(status_code=403, detail="Host-wide resource telemetry is restricted to administrator-scoped MCP tokens.")
    return await get_monitor({"id": context["user_id"], "role": context["role"]})


@app.post("/api/nodes/enrollment-tokens", tags=["nodes"])
def create_node_enrollment_token(request: NodeEnrollmentRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Issue a one-time, short-lived node enrollment secret. Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + 900
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute(
            "INSERT INTO node_enrollment_tokens(token_hash,name,hostname,expires_at,created_by) VALUES(?,?,?,?,?)",
            (token_hash, request.name, request.hostname, expires_at, admin["id"]),
        )
        connection.commit()
    audit(DATABASE_PATH, admin, "node.enrollment_token.created", request.name, "expires_in_seconds=900")
    return {"token": token, "expires_at": expires_at, "expires_in_seconds": 900}


@app.post("/api/nodes/enroll", tags=["nodes"])
def enroll_node(request: NodeEnrollRequest) -> dict[str, Any]:
    """Consume a one-time enrollment token and register a node identity."""
    token_hash = hashlib.sha256(request.token.encode("utf-8")).hexdigest()
    now = int(time.time())
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        invitation = connection.execute(
            "SELECT id,name,hostname,expires_at,used_at FROM node_enrollment_tokens WHERE token_hash=?",
            (token_hash,),
        ).fetchone()
        if not invitation or invitation["used_at"] is not None or invitation["expires_at"] < now:
            connection.rollback()
            raise HTTPException(status_code=401, detail="Enrollment token is invalid, expired, or already used.")
        connection.execute("UPDATE node_enrollment_tokens SET used_at=? WHERE id=? AND used_at IS NULL", (now, invitation["id"]))
        cursor = connection.execute(
            "INSERT INTO nodes(name,hostname,status) VALUES(?,?,?)",
            (invitation["name"], invitation["hostname"], "registered"),
        )
        node_id = cursor.lastrowid
        connection.commit()
    node_token = secrets.token_urlsafe(48)
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("UPDATE nodes SET token_hash=? WHERE id=?", (hashlib.sha256(node_token.encode()).hexdigest(), node_id))
        connection.commit()
    return {"registered": True, "node": {"id": node_id, "name": invitation["name"], "hostname": invitation["hostname"], "status": "registered"}, "node_token": node_token, "credential_note": "Store this token securely; it is shown only once."}


def authenticate_node(connection: sqlite3.Connection, node_id: int, request: Request) -> sqlite3.Row:
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not supplied:
        raise HTTPException(status_code=401, detail="Node credential required.")
    digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    connection.row_factory = sqlite3.Row
    node = connection.execute("SELECT id,status FROM nodes WHERE id=? AND token_hash=?", (node_id, digest)).fetchone()
    if not node or node["status"] == "revoked":
        raise HTTPException(status_code=401, detail="Node identity is invalid or revoked.")
    return node


@app.post("/api/nodes/{node_id}/heartbeat", tags=["nodes"])
def node_heartbeat(node_id: int, request: Request, payload: NodeHeartbeatRequest) -> dict[str, Any]:
    now = int(time.time())
    body = payload.model_dump()
    resources = {key: value for key, value in body.items() if key in {"cpu_total", "cpu_used", "memory_total_mb", "memory_used_mb", "storage_total_gb", "storage_used_gb", "provider", "agent_version", "capabilities"} and value is not None}
    inventory = body.get("inventory")
    switches = body.get("switches")
    if inventory is not None and (not isinstance(inventory, list) or len(inventory) > 1000 or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in inventory)):
        raise HTTPException(status_code=422, detail="Node inventory payload is invalid or too large.")
    if switches is not None and (not isinstance(switches, list) or len(switches) > 500 or any(not isinstance(item, dict) for item in switches)):
        raise HTTPException(status_code=422, detail="Node switch inventory is invalid or too large.")
    inventory_json = json.dumps(inventory, separators=(",", ":")) if inventory is not None else None
    switches_json = json.dumps(switches, separators=(",", ":")) if switches is not None else None
    if len((inventory_json or "[]").encode()) + len((switches_json or "[]").encode()) > 2_000_000:
        raise HTTPException(status_code=413, detail="Node inventory exceeds the 2 MB heartbeat limit.")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        authenticate_node(connection, node_id, request)
        heartbeat_interval = int(body.get("heartbeat_interval_seconds") or 60)
        command_lease_seconds = max(NODE_COMMAND_LEASE_SECONDS, min(10800, heartbeat_interval*3))
        active_command_id = body.get("active_command_id")
        if active_command_id:
            connection.execute("UPDATE node_commands SET lease_expires_at=? WHERE id=? AND node_id=? AND status='running'",
                               (now+command_lease_seconds, active_command_id, node_id))
        cursor = connection.execute("""UPDATE nodes SET status='online',last_seen=?,resources_json=?,
            inventory_json=COALESCE(?,inventory_json),inventory_seen=CASE WHEN ? IS NULL THEN inventory_seen ELSE ? END,
            switches_json=COALESCE(?,switches_json),switches_seen=CASE WHEN ? IS NULL THEN switches_seen ELSE ? END,
            heartbeat_interval_seconds=COALESCE(?,heartbeat_interval_seconds)
            WHERE id=?""", (now, json.dumps(resources, separators=(",", ":")), inventory_json, inventory_json, now,
                               switches_json, switches_json, now, body.get("heartbeat_interval_seconds"), node_id))
        connection.commit()
        if cursor.rowcount != 1:
            raise HTTPException(status_code=401, detail="Node identity is invalid or revoked.")
    return {"accepted": True, "node_id": node_id, "received_at": now}


@app.post("/api/nodes/{node_id}/commands/claim", tags=["nodes"])
def claim_node_command(node_id: int, request: Request) -> dict[str, Any]:
    """Claim one queued command using the node's outbound authenticated connection."""
    with closing(sqlite3.connect(DATABASE_PATH, timeout=15)) as connection:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("BEGIN IMMEDIATE")
        authenticate_node(connection, node_id, request)
        row = connection.execute("SELECT id,kind,payload_json FROM node_commands WHERE node_id=? AND status='queued' ORDER BY created_at,id LIMIT 1", (node_id,)).fetchone()
        if not row:
            connection.commit()
            return {"command": None}
        now = int(time.time())
        heartbeat_row = connection.execute("SELECT heartbeat_interval_seconds FROM nodes WHERE id=?", (node_id,)).fetchone()
        interval = int(heartbeat_row[0]) if heartbeat_row else 60
        lease_seconds = max(NODE_COMMAND_LEASE_SECONDS, min(10800, interval*3))
        cursor = connection.execute("UPDATE node_commands SET status='running',attempts=attempts+1,claimed_at=?,lease_expires_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'",
                                    (now, now+lease_seconds, row[0]))
        connection.commit()
        if cursor.rowcount != 1:
            return {"command": None}
    return {"command": {"id": row[0], "kind": row[1], "payload": json.loads(row[2])}}


@app.post("/api/nodes/{node_id}/commands/{command_id}/complete", tags=["nodes"])
def complete_node_command(node_id: int, command_id: str, request: Request,
                          completion: NodeCommandCompleteRequest) -> dict[str, Any]:
    result_payload = dict(completion.result) if completion.result is not None else None
    inventory_snapshot = result_payload.pop("_inventory", None) if result_payload is not None else None
    switches_snapshot = result_payload.pop("_switches", None) if result_payload is not None else None
    if inventory_snapshot is not None and (not isinstance(inventory_snapshot, list) or len(inventory_snapshot) > 1000 or any(not isinstance(item, dict) for item in inventory_snapshot)):
        raise HTTPException(status_code=422, detail="Node command inventory snapshot is invalid.")
    if inventory_snapshot is not None and len(json.dumps(inventory_snapshot, separators=(",", ":")).encode("utf-8")) > 2_000_000:
        raise HTTPException(status_code=413, detail="Node command inventory snapshot exceeds 2 MB.")
    if switches_snapshot is not None and (not isinstance(switches_snapshot, list) or len(switches_snapshot) > 500 or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in switches_snapshot)):
        raise HTTPException(status_code=422, detail="Node command switch snapshot is invalid.")
    result_json = json.dumps(result_payload, separators=(",", ":"), allow_nan=False) if result_payload is not None else None
    if result_json and len(result_json.encode("utf-8")) > 1_000_000:
        raise HTTPException(status_code=413, detail="Node command result exceeds the 1 MB limit.")
    with closing(sqlite3.connect(DATABASE_PATH, timeout=15)) as connection:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("BEGIN IMMEDIATE")
        authenticate_node(connection, node_id, request)
        row = connection.execute("SELECT job_id,user_id,kind FROM node_commands WHERE id=? AND node_id=? AND status='running'", (command_id, node_id)).fetchone()
        if not row:
            connection.rollback()
            raise HTTPException(status_code=404, detail="Running node command not found.")
        connection.execute("UPDATE node_commands SET status=?,result_json=?,error=?,lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                           (completion.status, result_json, completion.error[:1000] if completion.error else None, command_id))
        if completion.status == "succeeded" and inventory_snapshot is not None:
            connection.execute("UPDATE nodes SET inventory_json=?,inventory_seen=? WHERE id=?",
                               (json.dumps(inventory_snapshot, separators=(",", ":")), int(time.time()), node_id))
        if completion.status == "succeeded" and switches_snapshot is not None:
            connection.execute("UPDATE nodes SET switches_json=?,switches_seen=? WHERE id=?", (json.dumps(switches_snapshot, separators=(",", ":")), int(time.time()), node_id))
        if row[0]:
            job_status = "succeeded" if completion.status == "succeeded" else "failed"
            connection.execute("UPDATE provisioning_jobs SET status=?,result_json=?,error=?,reconciliation_status=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='running'",
                               (job_status, result_json, completion.error[:1000] if completion.error else None,
                                "not_required" if completion.status == "succeeded" else "pending", row[0]))
        connection.commit()
    if row[0]:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            owner = connection.execute("SELECT j.vm_name,u.username FROM provisioning_jobs j JOIN users u ON u.id=j.user_id WHERE j.id=?", (row[0],)).fetchone()
        if owner and completion.status == "succeeded":
            audit(DATABASE_PATH, {"id": row[1], "username": owner[1]}, "vm.create", owner[0], f"job_id={row[0]}; node_id={node_id}")
    return {"accepted": True, "command_id": command_id, "status": completion.status}


@app.get("/api/networks", tags=["networks"])
def list_managed_networks(admin: dict[str, Any] = Depends(require_admin)) -> dict[str, list[dict[str, Any]]]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        rows = connection.execute("""SELECT m.node_id,n.name AS node_name,n.status AS node_status,n.last_seen,
            n.switches_json,m.name,m.mode,m.cidr,m.status,m.created_at,n.switches_seen,
            a.user_id,u.username FROM managed_networks m JOIN nodes n ON n.id=m.node_id
            LEFT JOIN tenant_network_assignments a ON a.node_id=m.node_id AND a.network_name=m.name
            LEFT JOIN users u ON u.id=a.user_id ORDER BY n.name,m.name""").fetchall()
    now = int(time.time())
    items = []
    for row in rows:
        try:
            switches = json.loads(row[4] or "[]")
        except (ValueError, TypeError):
            switches = []
        present = any(isinstance(item, dict) and item.get("name") == row[5] for item in switches)
        status = row[8]
        online = bool(row[3]) and now - int(row[3]) <= 180 and row[2] == "online"
        if not online:
            status = "offline"
        elif not row[10] or now - int(row[10]) > 180:
            status = "unknown"
        elif status == "provisioning" and present:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection:
                connection.execute("UPDATE managed_networks SET status='active' WHERE node_id=? AND name=? AND status='provisioning'", (row[0], row[5]))
                connection.commit()
            status = "active"
        elif status == "deleting" and not present:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection:
                connection.execute("DELETE FROM managed_networks WHERE node_id=? AND name=? AND status='deleting'", (row[0], row[5]))
                connection.commit()
            continue
        elif status == "active" and not present:
            status = "missing"
        items.append({"node_id": row[0], "node_name": row[1], "name": row[5], "mode": row[6],
                      "cidr": row[7], "status": status, "present": present, "created_at": row[9],
                      "assigned_user_id": row[11], "assigned_username": row[12]})
    return {"items": items}


@app.post("/api/networks", tags=["networks"])
async def create_network(request: CreateNetworkRequest, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        row = connection.execute("SELECT status,last_seen,resources_json,switches_json,switches_seen FROM nodes WHERE id=? AND status!='revoked'", (request.node_id,)).fetchone()
    if not row or row[0] != "online" or not row[1] or int(time.time()) - int(row[1]) > 180:
        raise HTTPException(status_code=409, detail="Selected node is offline or has a stale heartbeat.")
    try:
        resources = json.loads(row[2] or "{}")
        switches = json.loads(row[3] or "[]")
    except (ValueError, TypeError):
        resources, switches = {}, []
    if not row[4] or int(time.time()) - int(row[4]) > 180:
        raise HTTPException(status_code=409, detail="Selected node has no fresh virtual-switch inventory; wait for a successful inventory heartbeat.")
    provider = resources.get("provider")
    if "network.manage" not in resources.get("capabilities", []):
        raise HTTPException(status_code=422, detail="Node provider manifest does not grant network.manage.")
    if provider == "hyperv" and (request.mode != "isolated" or request.cidr is not None):
        raise HTTPException(status_code=422, detail="Hyper-V currently supports isolated private switches only; CIDR/DHCP and NAT are not configured by VirtueWise.")
    if provider == "libvirt" and not request.cidr:
        raise HTTPException(status_code=422, detail="Libvirt networks require an explicit IPv4 CIDR.")
    if any(isinstance(item, dict) and item.get("name") == request.name for item in switches):
        raise HTTPException(status_code=409, detail="A network with this name already exists on the selected node and is not managed by VirtueWise.")
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        try:
            connection.execute("INSERT INTO managed_networks(node_id,name,mode,cidr,status,created_by) VALUES(?,?,?,?, 'provisioning',?)",
                               (request.node_id, request.name, request.mode, request.cidr, admin["id"]))
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            raise HTTPException(status_code=409, detail="A VirtueWise network with this name is already recorded on the selected node.") from None
    try:
        result = await dispatch_node_command(request.node_id, admin["id"], "network.create",
                                             {"name": request.name, "mode": request.mode, "cidr": request.cidr})
    except HTTPException as exc:
        if exc.status_code != 504:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection:
                connection.execute("DELETE FROM managed_networks WHERE node_id=? AND name=? AND status='provisioning'", (request.node_id, request.name))
                connection.commit()
        raise
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("UPDATE managed_networks SET status='active' WHERE node_id=? AND name=?", (request.node_id, request.name))
        connection.commit()
    audit(DATABASE_PATH, admin, "network.create", request.name, f"node_id={request.node_id}; mode={request.mode}; cidr={request.cidr or ''}")
    return result


@app.delete("/api/networks/{node_id}/{name}", tags=["networks"])
async def delete_network(node_id: int, name: str, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,62}", name):
        raise HTTPException(status_code=422, detail="Invalid virtual network name.")
    with closing(sqlite3.connect(DATABASE_PATH, timeout=15)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        node = connection.execute("SELECT status,last_seen,switches_json,switches_seen FROM nodes WHERE id=? AND status!='revoked'", (node_id,)).fetchone()
        network = connection.execute("SELECT status FROM managed_networks WHERE node_id=? AND name=?", (node_id, name)).fetchone()
        if not network:
            connection.rollback()
            raise HTTPException(status_code=404, detail="VirtueWise-managed network not found.")
        if network[0] != "active":
            connection.rollback()
            raise HTTPException(status_code=409, detail=f"Network cannot be deleted while its recorded state is {network[0]}.")
        assignment = connection.execute("SELECT user_id FROM tenant_network_assignments WHERE node_id=? AND network_name=?", (node_id, name)).fetchone()
        if assignment:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Unassign this network from its customer before deleting it.")
        attached = connection.execute("SELECT COUNT(*) FROM vm_ownership WHERE network_name=? AND (node_id=? OR node_id IS NULL)", (name, node_id)).fetchone()[0]
        if attached:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Network is referenced by one or more VM ownership records; delete or detach those VMs first.")
        if not node or node[0] != "online" or not node[1] or int(time.time()) - int(node[1]) > 180:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Selected node is offline or has a stale heartbeat.")
        if not node[3] or int(time.time()) - int(node[3]) > 180:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Cannot verify the node's latest network inventory; retry after a successful switches heartbeat.")
        try:
            reported_switches = json.loads(node[2] or "[]")
        except (ValueError, TypeError):
            connection.rollback()
            raise HTTPException(status_code=409, detail="Cannot verify the node's latest network inventory; retry after a valid heartbeat.") from None
        present = any(isinstance(item, dict) and item.get("name") == name for item in reported_switches)
        if not present:
            connection.execute("DELETE FROM managed_networks WHERE node_id=? AND name=? AND status='active'", (node_id, name))
            connection.commit()
            audit(DATABASE_PATH, admin, "network.forget", name, f"node_id={node_id}; absent from fresh provider inventory")
            return {"forgotten": True, "name": name, "node_id": node_id}
        cursor = connection.execute("UPDATE managed_networks SET status='deleting' WHERE node_id=? AND name=? AND status='active'", (node_id, name))
        if cursor.rowcount != 1:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Network state changed; refresh the network list and retry.")
        connection.commit()
    try:
        result = await dispatch_node_command(node_id, admin["id"], "network.delete", {"name": name})
    except HTTPException as exc:
        if exc.status_code != 504:
            with closing(sqlite3.connect(DATABASE_PATH)) as connection:
                connection.execute("UPDATE managed_networks SET status='active' WHERE node_id=? AND name=? AND status='deleting'", (node_id, name))
                connection.commit()
        raise
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        connection.execute("DELETE FROM tenant_network_assignments WHERE node_id=? AND network_name=?", (node_id, name))
        connection.execute("DELETE FROM managed_networks WHERE node_id=? AND name=?", (node_id, name))
        connection.commit()
    audit(DATABASE_PATH, admin, "network.delete", name, f"node_id={node_id}")
    return result


@app.post("/api/networks/{node_id}/{name}/assign", tags=["networks"])
def assign_network(node_id: int, name: str, request: NetworkAssignmentRequest,
                   admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        network = connection.execute("SELECT status FROM managed_networks WHERE node_id=? AND name=?", (node_id, name)).fetchone()
        node = connection.execute("SELECT status,last_seen,switches_json,switches_seen FROM nodes WHERE id=?", (node_id,)).fetchone()
        customer = connection.execute("SELECT username,active,role FROM users WHERE id=?", (request.user_id,)).fetchone()
        if not network or network[0] != "active":
            connection.rollback()
            raise HTTPException(status_code=409, detail="Only an active VirtueWise-managed network can be assigned.")
        if not customer or not customer[1] or customer[2] != "user":
            connection.rollback()
            raise HTTPException(status_code=404, detail="Active customer account not found.")
        if not node or node[0] != "online" or not node[1] or int(time.time())-int(node[1]) > 180 or not node[3] or int(time.time())-int(node[3]) > 180:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Network node or its switch inventory is offline or stale.")
        try:
            switches = json.loads(node[2] or "[]")
        except (ValueError, TypeError):
            connection.rollback()
            raise HTTPException(status_code=409, detail="Cannot verify current network inventory.") from None
        if not any(isinstance(item, dict) and item.get("name") == name for item in switches):
            connection.rollback()
            raise HTTPException(status_code=409, detail="Network is not present in the latest provider inventory.")
        existing = connection.execute("SELECT user_id FROM tenant_network_assignments WHERE node_id=? AND network_name=?", (node_id, name)).fetchone()
        if existing and existing[0] != request.user_id:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Network is already assigned to another customer; unassign it first.")
        connection.execute("INSERT OR IGNORE INTO tenant_network_assignments(node_id,network_name,user_id,assigned_by) VALUES(?,?,?,?)",
                            (node_id, name, request.user_id, admin["id"]))
        connection.commit()
    audit(DATABASE_PATH, admin, "network.assign", name, f"node_id={node_id}; customer_id={request.user_id}")
    return {"node_id": node_id, "network_name": name, "user_id": request.user_id, "username": customer[0]}


@app.delete("/api/networks/{node_id}/{name}/assign", tags=["networks"])
def unassign_network(node_id: int, name: str, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    with closing(sqlite3.connect(DATABASE_PATH, timeout=10)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        assignment = connection.execute("SELECT user_id FROM tenant_network_assignments WHERE node_id=? AND network_name=?", (node_id, name)).fetchone()
        if not assignment:
            raise HTTPException(status_code=404, detail="Network assignment not found.")
        attached = connection.execute("SELECT COUNT(*) FROM vm_ownership WHERE network_name=? AND (node_id=? OR node_id IS NULL)", (name, node_id)).fetchone()[0]
        if attached:
            raise HTTPException(status_code=409, detail="Network cannot be unassigned while customer VMs remain attached. Delete or detach those VMs first.")
        connection.execute("DELETE FROM tenant_network_assignments WHERE node_id=? AND network_name=?", (node_id, name))
        connection.commit()
    audit(DATABASE_PATH, admin, "network.unassign", name, f"node_id={node_id}; customer_id={assignment[0]}")
    return {"node_id": node_id, "network_name": name, "unassigned": True}


@app.post("/api/nodes/{node_id}/rotate-credential", tags=["nodes"])
def rotate_node_credential(node_id: int, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    """Replace a node credential; the new secret is returned exactly once."""
    token = secrets.token_urlsafe(48)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        cursor = connection.execute(
            "UPDATE nodes SET token_hash=?,status='pending' WHERE id=? AND status!='revoked'",
            (digest, node_id),
        )
        connection.commit()
    if cursor.rowcount != 1:
        raise HTTPException(status_code=404, detail="Active node not found.")
    audit(DATABASE_PATH, admin, "node.credential_rotated", str(node_id))
    return {"node_id": node_id, "node_token": token, "credential_note": "Store this token securely; it is shown only once. The previous token is now invalid."}


@app.post("/api/nodes/{node_id}/revoke", tags=["nodes"])
def revoke_node(node_id: int, admin: dict[str, Any] = Depends(require_admin)) -> dict[str, bool]:
    with closing(sqlite3.connect(DATABASE_PATH)) as connection:
        cursor = connection.execute("UPDATE nodes SET status='revoked',token_hash=NULL WHERE id=?", (node_id,))
        if cursor.rowcount == 1:
            connection.execute("UPDATE node_commands SET status='failed',error='Node credential revoked by administrator.',lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE node_id=? AND status IN ('queued','running')", (node_id,))
            connection.execute("UPDATE provisioning_jobs SET status='failed',error='Assigned node was revoked; reconcile provider state before retrying.',updated_at=CURRENT_TIMESTAMP WHERE node_id=? AND status='running'", (node_id,))
        connection.commit()
    if cursor.rowcount != 1:
        raise HTTPException(status_code=404, detail="Node not found.")
    audit(DATABASE_PATH, admin, "node.revoke", str(node_id))
    return {"success": True}


@app.get("/developers", include_in_schema=False)
def developer_portal() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "developers.html")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
