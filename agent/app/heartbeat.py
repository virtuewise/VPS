"""Best-effort, authenticated node telemetry reporting to the control plane."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import socket
from urllib.parse import urlparse

import httpx

log = logging.getLogger("virtuewise.heartbeat")


def heartbeat_config(env: dict[str, str] | None = None) -> tuple[str, int, str, int] | None:
    values = os.environ if env is None else env
    url = values.get("VPS_CONTROL_PLANE_URL", "").strip().rstrip("/")
    node_id = values.get("VPS_NODE_ID", "").strip()
    token = values.get("VPS_NODE_TOKEN", "").strip()
    if not url and not node_id and not token:
        return None
    if not url or not node_id.isdigit() or int(node_id) < 1 or len(token) < 32:
        raise RuntimeError("Configure VPS_CONTROL_PLANE_URL, positive VPS_NODE_ID, and VPS_NODE_TOKEN (at least 32 characters) together.")
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}):
        raise RuntimeError("Node heartbeat requires HTTPS; HTTP is allowed only for loopback development.")
    if not parsed.netloc or parsed.query or parsed.fragment:
        raise RuntimeError("VPS_CONTROL_PLANE_URL must be a base HTTP(S) URL without query or fragment.")
    interval = max(15, min(3600, int(values.get("VPS_HEARTBEAT_INTERVAL", "60"))))
    return url, int(node_id), token, interval


def heartbeat_payload(monitor: dict, provider: str, version: str, inventory: list[dict] | None = None,
                      switches: list[dict] | None = None, capabilities: list[str] | None = None) -> dict:
    total = int(monitor.get("memory_total_mb") or 0)
    free = int(monitor.get("memory_free_mb") or 0)
    storage_total = float(monitor.get("storage_total_gb") or 0)
    storage_free = float(monitor.get("storage_free_gb") or 0)
    return {
        "cpu_total": os.cpu_count() or 1,
        "cpu_used": round((float(monitor.get("cpu_percent") or 0) / 100) * (os.cpu_count() or 1), 2),
        "memory_total_mb": total, "memory_used_mb": max(0, total - free),
        "storage_total_gb": storage_total, "storage_used_gb": max(0, round(storage_total - storage_free, 1)),
        "provider": provider, "agent_version": version,
        **({"capabilities": capabilities} if capabilities is not None else {}),
        **({"inventory": inventory} if inventory is not None else {}),
        **({"switches": switches} if switches is not None else {}),
    }


async def heartbeat_loop(config: tuple[str, int, str, int], provider, provider_name: str, version: str,
                         capabilities: list[str] | None = None) -> None:
    url, node_id, token, interval = config
    endpoint = f"{url}/api/nodes/{node_id}/heartbeat"
    backoff = 5
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                monitor = await asyncio.to_thread(provider.host_monitor)
                try:
                    inventory = await asyncio.to_thread(provider.list_vms)
                except Exception as exc:
                    log.warning("Node inventory refresh failed (%s); retaining previous snapshot", type(exc).__name__)
                    inventory = None
                try:
                    switches = await asyncio.to_thread(provider.list_switches)
                except Exception as exc:
                    log.warning("Node switch refresh failed (%s); retaining previous snapshot", type(exc).__name__)
                    switches = None
                payload = heartbeat_payload(monitor, provider_name, version, inventory, switches, capabilities)
                response = await client.post(endpoint, json=payload, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                backoff = 5
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Node heartbeat failed (%s); retrying in %s seconds", type(exc).__name__, backoff)
                await asyncio.sleep(backoff + random.uniform(0, min(3, backoff / 4)))
                backoff = min(interval, backoff * 2)


async def execute_node_command(provider, kind: str, payload: dict):
    """Execute only typed VM operations and return a fresh inventory snapshot."""
    if not isinstance(payload, dict):
        raise ValueError("Command payload must be an object.")
    if kind == "vm.create":
        allowed = {"name", "memory_mb", "disk_gb", "cpu_count", "iso_path", "switch_name", "cloud_init"}
        required = {"name", "memory_mb", "disk_gb", "cpu_count", "iso_path"}
        if set(payload) - allowed or not required <= set(payload):
            raise ValueError("Invalid VM create command payload.")
        name = payload["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,39}", name):
            raise ValueError("Invalid VM name in create command.")
        for key, low, high in (("memory_mb", 512, 32768), ("disk_gb", 8, 500), ("cpu_count", 1, 16)):
            value = payload[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"Invalid {key} in create command.")
        if not isinstance(payload["iso_path"], str) or not 5 <= len(payload["iso_path"]) <= 260:
            raise ValueError("Invalid ISO path in create command.")
        if payload.get("switch_name") is not None and (not isinstance(payload["switch_name"], str) or len(payload["switch_name"]) > 100):
            raise ValueError("Invalid switch name in create command.")
        cloud_init = payload.get("cloud_init")
        if cloud_init is not None:
            if not isinstance(cloud_init, dict) or set(cloud_init) != {"username", "password_hash", "ssh_public_key"}:
                raise ValueError("Invalid cloud-init payload in create command.")
            if any(not isinstance(cloud_init[key], str) or not cloud_init[key] for key in cloud_init):
                raise ValueError("Invalid cloud-init values in create command.")
        operation = await asyncio.to_thread(provider.create_vm, **payload)
    elif kind == "network.create":
        if set(payload) != {"name", "mode", "cidr"}:
            raise ValueError("Invalid network create command payload.")
        name, mode, cidr = payload["name"], payload["mode"], payload["cidr"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,62}", name):
            raise ValueError("Invalid virtual network name.")
        if mode not in {"isolated", "nat"}:
            raise ValueError("Invalid virtual network mode.")
        if cidr is not None and (not isinstance(cidr, str) or len(cidr) > 32):
            raise ValueError("Invalid network CIDR.")
        method = getattr(provider, "create_network", None)
        if not callable(method):
            raise ValueError("Configured provider does not support network management.")
        operation = await asyncio.to_thread(method, name=name, mode=mode, cidr=cidr)
    elif kind == "network.delete":
        if set(payload) != {"name"}:
            raise ValueError("Invalid network delete command payload.")
        name = payload["name"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{1,62}", name):
            raise ValueError("Invalid virtual network name.")
        method = getattr(provider, "delete_network", None)
        if not callable(method):
            raise ValueError("Configured provider does not support network management.")
        operation = await asyncio.to_thread(method, name)
    elif kind in {"vm.start", "vm.stop", "vm.delete", "vm.console"}:
        name = payload.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,39}", name):
            raise ValueError("Invalid VM name in node command.")
        action = kind.split(".", 1)[1]
        if action == "delete":
            if payload.get("confirm_name") != name:
                raise ValueError("Delete command requires exact VM-name confirmation.")
            delete_disks = payload.get("delete_disks", False)
            if not isinstance(delete_disks, bool):
                raise ValueError("Delete disk flag must be a boolean.")
            operation = await asyncio.to_thread(provider.vm_action, action, name, delete_disks=delete_disks)
        else:
            operation = await asyncio.to_thread(provider.vm_action, action, name)
    else:
        raise ValueError("Unsupported node command type.")
    if not isinstance(operation, dict):
        raise TypeError("Provider returned an invalid command result.")
    result = dict(operation)
    try:
        inventory = await asyncio.to_thread(provider.list_vms)
        if isinstance(inventory, list):
            result["_inventory"] = inventory
        else:
            log.warning("Provider returned invalid VM inventory after a successful command; retaining the previous snapshot")
    except Exception as exc:
        log.warning("VM inventory refresh failed after a successful command (%s); retaining the previous snapshot", type(exc).__name__)
    if kind.startswith("network."):
        try:
            switches = await asyncio.to_thread(provider.list_switches)
            if isinstance(switches, list):
                result["_switches"] = switches
            else:
                log.warning("Provider returned invalid switch inventory after a successful network command; retaining the previous snapshot")
        except Exception as exc:
            log.warning("Switch inventory refresh failed after a successful network command (%s); retaining the previous snapshot", type(exc).__name__)
    return result


async def node_command_loop(config: tuple[str, int, str, int], provider) -> None:
    """Poll for durable node commands over the node's authenticated outbound channel."""
    url, node_id, token, interval = config
    base = f"{url}/api/nodes/{node_id}/commands"
    headers = {"Authorization": f"Bearer {token}"}
    backoff = 2
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                response = await client.post(base + "/claim", headers=headers)
                response.raise_for_status()
                command = response.json().get("command")
                if not command:
                    backoff = 2
                    await asyncio.sleep(min(5, max(1, interval // 12)))
                    continue
                try:
                    result = await execute_node_command(provider, command["kind"], command["payload"])
                    json.dumps(result, allow_nan=False)
                    completion = {"status": "succeeded", "result": result}
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.warning("Node command failed (%s)", type(exc).__name__)
                    completion = {"status": "failed", "error": f"{type(exc).__name__}: provider operation failed"}
                complete = await client.post(base + f"/{command['id']}/complete", json=completion, headers=headers)
                complete.raise_for_status()
                backoff = 2
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Node command channel failed (%s); retrying in %s seconds", type(exc).__name__, backoff)
                await asyncio.sleep(backoff + random.uniform(0, min(2, backoff / 4)))
                backoff = min(30, backoff * 2)
