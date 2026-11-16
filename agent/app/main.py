from __future__ import annotations

import hmac
import os
import re
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.providers import hyperv

AGENT_TOKEN = os.getenv("VPS_AGENT_TOKEN", "")
if len(AGENT_TOKEN) < 32:
    raise RuntimeError("Set VPS_AGENT_TOKEN to a random secret of at least 32 characters.")

app = FastAPI(title="VirtueWise VPS Node Agent", description="Authenticated, allow-listed VM operations for a Windows Hyper-V node.", version="0.2.0", docs_url=None, redoc_url=None)


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not hmac.compare_digest(authorization, f"Bearer {AGENT_TOKEN}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


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

    @field_validator("iso_path")
    @classmethod
    def validate_iso_path(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Invalid ISO path")
        return value


class VMNameRequest(BaseModel):
    name: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")


class DeleteVMRequest(VMNameRequest):
    confirm_name: str
    delete_disks: bool = False


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agent": "virtuewise-vps-node-agent", "version": "0.2.0"}


@app.get("/v1/vms", dependencies=[Depends(require_token)])
def list_vms() -> dict[str, list[dict[str, Any]]]:
    return {"items": hyperv.list_vms()}


@app.get("/v1/switches", dependencies=[Depends(require_token)])
def list_switches() -> dict[str, list[dict[str, Any]]]:
    return {"items": hyperv.list_switches()}


@app.get("/v1/monitor", dependencies=[Depends(require_token)])
def monitor() -> dict[str, Any]:
    return hyperv.host_monitor()


@app.post("/v1/vms", dependencies=[Depends(require_token)], status_code=202)
def create_vm(request: CreateVMRequest) -> dict[str, Any]:
    result = hyperv.create_vm(**request.model_dump())
    return {"status": "created", "vm": result}


@app.post("/v1/vms/start", dependencies=[Depends(require_token)])
def start_vm(request: VMNameRequest) -> dict[str, Any]:
    return {"status": "started", "vm": hyperv.vm_action("start", request.name)}


@app.post("/v1/vms/stop", dependencies=[Depends(require_token)])
def stop_vm(request: VMNameRequest) -> dict[str, Any]:
    return {"status": "stopped", "vm": hyperv.vm_action("stop", request.name)}


@app.post("/v1/vms/{name}/console", dependencies=[Depends(require_token)])
def open_console(name: str) -> dict[str, Any]:
    return {"status": "opened", "console": hyperv.vm_action("console", name)}


@app.delete("/v1/vms/{name}", dependencies=[Depends(require_token)])
def delete_vm(name: str, request: DeleteVMRequest) -> dict[str, Any]:
    if request.name != name or request.confirm_name != name:
        raise HTTPException(status_code=400, detail="The VM name and confirmation name must match the URL.")
    result = hyperv.vm_action("delete", name, delete_disks=request.delete_disks)
    return {"status": "deleted", "vm": result}
