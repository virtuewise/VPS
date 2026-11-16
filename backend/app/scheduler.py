"""Deterministic, resource-aware node placement primitives."""
from __future__ import annotations

from typing import Any


def choose_node(nodes: list[dict[str, Any]], *, cpu_count: int, memory_mb: int, disk_gb: int,
                now: int, heartbeat_ttl: int = 180, provider: str | None = None) -> dict[str, Any] | None:
    """Choose the least-loaded eligible node; stale or under-capacity nodes are excluded."""
    candidates = []
    for node in nodes:
        if node.get("status") != "online" or not node.get("last_seen") or now - int(node["last_seen"]) > heartbeat_ttl:
            continue
        resources = node.get("resources") or {}
        if provider and resources.get("provider") != provider:
            continue
        try:
            cpu_free = float(resources["cpu_total"]) - float(resources.get("cpu_used", 0))
            memory_free = int(resources["memory_total_mb"]) - int(resources.get("memory_used_mb", 0))
            disk_free = float(resources["storage_total_gb"]) - float(resources.get("storage_used_gb", 0))
        except (KeyError, TypeError, ValueError):
            continue
        if cpu_free < cpu_count or memory_free < memory_mb or disk_free < disk_gb:
            continue
        cpu_ratio = cpu_count / max(float(resources["cpu_total"]), 1)
        memory_ratio = memory_mb / max(int(resources["memory_total_mb"]), 1)
        disk_ratio = disk_gb / max(float(resources["storage_total_gb"]), 1)
        score = cpu_ratio * 0.25 + memory_ratio * 0.45 + disk_ratio * 0.30
        candidates.append((score, int(node["id"]), node, {
            "cpu_free": cpu_free, "memory_free_mb": memory_free, "storage_free_gb": round(disk_free, 1),
        }))
    if not candidates:
        return None
    score, _, node, available = min(candidates, key=lambda item: (item[0], item[1]))
    return {"node_id": node["id"], "node_name": node["name"], "score": round(score, 4), "available": available}
