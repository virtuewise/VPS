"""Typed provider adapter with mandatory rootless Docker isolation in production."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
import sys
from pathlib import Path
from typing import Any, Callable

class WorkerProvider:
    def __init__(self, package: Path, manifest: dict[str, Any], *, timeout: int = 30, sandbox_mode: str | None = None, sandbox_image: str | None = None, activation_check: Callable[[], bool] | None = None):
        self.package = package.resolve()
        self.manifest = manifest
        self.name = manifest["id"]
        self.activation_check = activation_check
        self.timeout = max(1, min(timeout, 120))
        self.worker = Path(__file__).with_name("worker.py").resolve()
        self.sandbox_mode = sandbox_mode or os.getenv("VPS_PROVIDER_SANDBOX", "disabled").lower()
        self.sandbox_image = sandbox_image or os.getenv("VPS_PROVIDER_SANDBOX_IMAGE", "")
        if self.sandbox_mode == "docker":
            if "@sha256:" not in self.sandbox_image:
                raise RuntimeError("Provider sandbox image must be pinned by immutable sha256 digest.")
            self._verify_rootless_docker()
        elif self.sandbox_mode != "process":
            raise RuntimeError("Third-party providers require VPS_PROVIDER_SANDBOX=docker; process mode is for tests only.")

    @staticmethod
    def _verify_rootless_docker() -> None:
        try:
            result = subprocess.run(["docker", "info", "--format", "{{json .SecurityOptions}}"], capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Rootless Docker is required for provider sandboxing.") from exc
        if result.returncode != 0 or "rootless" not in result.stdout.lower():
            raise RuntimeError("Docker must be configured in rootless mode before third-party providers can run.")

    def _require(self, capability: str) -> None:
        if capability not in self.manifest.get("capabilities", []):
            raise PermissionError("Provider manifest does not declare capability: " + capability)

    def _call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if self.activation_check is not None and not self.activation_check():
            raise PermissionError("Provider is disabled or no longer the active installed version.")
        request = json.dumps(payload or {}, separators=(",", ":"))
        if len(request.encode("utf-8")) > 256 * 1024:
            raise ValueError("Provider request exceeds 256 KiB")
        cid_file = None
        if self.sandbox_mode == "docker":
            fd, cid_file = tempfile.mkstemp(prefix="virtuewise-provider-", suffix=".cid")
            os.close(fd)
            os.unlink(cid_file)
            command = ["docker", "run", "--rm", "--cidfile", cid_file, "-i", "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m", "--memory-swap=256m", "--cpus=1", "--ulimit", "nofile=256:256", "--ulimit", "fsize=1048576:1048576", "--ipc=none", "--user=65534:65534", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m", "--mount", f"type=bind,src={self.package},dst=/plugin.vwpkg,readonly", "--mount", f"type=bind,src={self.worker},dst=/worker.py,readonly", "--entrypoint", "python", self.sandbox_image, "-I", "/worker.py", "/plugin.vwpkg", method]
        else:
            env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "WINDIR": os.environ.get("WINDIR", ""), "LANG": os.environ.get("LANG", "C.UTF-8")}
            command = [sys.executable, "-I", str(self.worker), str(self.package), method]
        try:
            result = subprocess.run(command, input=request, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=self.timeout, cwd=str(self.package.parent), env=env if self.sandbox_mode == "process" else None, check=False)
        except subprocess.TimeoutExpired as exc:
            if cid_file:
                try:
                    container_id = Path(cid_file).read_text(encoding="ascii").strip()
                    if container_id:
                        subprocess.run(["docker", "kill", container_id], capture_output=True, timeout=5, check=False)
                        subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, timeout=5, check=False)
                except (OSError, subprocess.SubprocessError):
                    pass
            raise TimeoutError("Provider worker timed out; the container was terminated where possible.") from exc
        finally:
            if cid_file and os.path.exists(cid_file):
                os.unlink(cid_file)
        if result.returncode != 0:
            raise RuntimeError("Provider worker failed; details are intentionally not exposed to API clients.")
        if len(result.stdout.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Provider worker response exceeds 1 MiB")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Provider worker returned invalid JSON.") from exc

    def list_vms(self):
        self._require("inventory.read")
        result = self._call("list_vms")
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result): raise TypeError("Provider list_vms must return a list of objects")
        return result

    def list_switches(self):
        self._require("inventory.read")
        result = self._call("list_switches")
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result): raise TypeError("Provider list_switches must return a list of objects")
        return result

    def host_monitor(self):
        self._require("host.metrics.read")
        result = self._call("host_monitor")
        if not isinstance(result, dict): raise TypeError("Provider host_monitor must return an object")
        return result

    def create_vm(self, **kwargs):
        self._require("vm.create")
        result = self._call("create_vm", kwargs)
        if not isinstance(result, dict): raise TypeError("Provider create_vm must return an object")
        return result

    def vm_action(self, action, name, *, delete_disks=False):
        if action not in {"start", "stop", "delete", "console"}:
            raise ValueError("Unsupported VM action")
        capability = "vm.delete" if action == "delete" else "vm.console" if action == "console" else "vm.power"
        self._require(capability)
        result = self._call("vm_action", {"action": action, "name": name, "delete_disks": delete_disks})
        if not isinstance(result, dict): raise TypeError("Provider vm_action must return an object")
        return result
