"""Best-effort, authenticated node telemetry reporting to the control plane."""
from __future__ import annotations

import asyncio
import logging
import os
import random
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


def heartbeat_payload(monitor: dict, provider: str, version: str) -> dict:
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
    }


async def heartbeat_loop(config: tuple[str, int, str, int], provider, provider_name: str, version: str) -> None:
    url, node_id, token, interval = config
    endpoint = f"{url}/api/nodes/{node_id}/heartbeat"
    backoff = 5
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                payload = heartbeat_payload(provider.host_monitor(), provider_name, version)
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
