"""Signed provider package verification and administrator-controlled lifecycle."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.providers.manifest import ProviderManifest

MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
MAX_FILES = 256


def _read_json(path: Path, limit: int = 1024 * 1024) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Metadata must be a bounded regular file.")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_package(package: Path, signature_file: Path, trust_store: Path) -> tuple[ProviderManifest, str, str]:
    if package.is_symlink() or not package.is_file() or package.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("Provider package must be a regular file no larger than 64 MiB.")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    signature = _read_json(signature_file, 16 * 1024)
    if not isinstance(signature, dict) or set(signature) != {"key_id", "signature"}:
        raise ValueError("Invalid detached signature metadata.")
    key_id = signature["key_id"]
    if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", key_id):
        raise ValueError("Invalid signing key identifier.")
    keys = _read_json(trust_store, 256 * 1024)
    if not isinstance(keys, dict) or key_id not in keys:
        raise ValueError("Package signer is not trusted.")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(keys[key_id], validate=True))
        sig_bytes = base64.b64decode(signature["signature"], validate=True)
        public_key.verify(sig_bytes, bytes.fromhex(digest))
    except (ValueError, TypeError, InvalidSignature) as exc:
        raise ValueError("Provider package signature verification failed.") from exc
    try:
        with zipfile.ZipFile(package) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES:
                raise ValueError("Provider package contains too many files.")
            total = 0
            names = set()
            for info in infos:
                path = PurePosixPath(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if path.is_absolute() or ".." in path.parts or "\\" in info.filename or "\x00" in info.filename or info.is_dir() or mode == stat.S_IFLNK:
                    raise ValueError("Unsafe or unsupported package archive path.")
                if info.filename in names:
                    raise ValueError("Duplicate package archive path.")
                names.add(info.filename)
                total += info.file_size
                if total > MAX_UNPACKED_BYTES or info.file_size > MAX_UNPACKED_BYTES:
                    raise ValueError("Provider package expands beyond the size limit.")
            if "manifest.json" not in names:
                raise ValueError("Package is missing manifest.json.")
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.file_size > 32 * 1024:
                raise ValueError("Provider manifest exceeds 32 KiB.")
            manifest_raw = json.loads(archive.read("manifest.json").decode("utf-8"))
            manifest = ProviderManifest.model_validate(manifest_raw)
            if manifest.runtime != "python-worker":
                raise ValueError("Installable packages must use python-worker runtime.")
            module_name, _class_name = manifest.entrypoint.split(":", 1)
            module_path = module_name.replace(".", "/") + ".py"
            if module_path not in names:
                raise ValueError("Package entrypoint module is missing.")
            if any(not (n == "manifest.json" or n.endswith(".py")) for n in names):
                raise ValueError("Only Python source files and manifest.json are allowed in v1 packages.")
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError("Provider package archive is invalid.") from exc
    return manifest, digest, key_id


class ProviderRegistry:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.packages = self.root / "packages"
        self.registry_file = self.root / "registry.json"
        if self.packages.is_symlink():
            raise ValueError("Provider package root must not be a symlink.")
        self.packages.mkdir(parents=True, exist_ok=True)
        if not self.registry_file.exists():
            self._save({"providers": {}, "events": []})

    def _load(self) -> dict[str, Any]:
        data = _read_json(self.registry_file)
        if not isinstance(data, dict) or set(data) - {"providers", "events"} or not isinstance(data.get("providers"), dict):
            raise ValueError("Provider registry is corrupt.")
        data.setdefault("events", [])
        if not isinstance(data["events"], list):
            raise ValueError("Provider audit journal is corrupt.")
        return data

    @staticmethod
    def _event(data: dict[str, Any], action: str, provider_id: str, version: str | None = None) -> None:
        data.setdefault("events", []).append({"at": datetime.now(timezone.utc).isoformat(), "action": action, "provider_id": provider_id, "version": version})
        data["events"] = data["events"][-1000:]

    def _save(self, data: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="registry-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.registry_file)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def install(self, package: Path, signature: Path, trust_store: Path, *, api_version: str) -> dict[str, Any]:
        manifest, digest, key_id = verify_package(package, signature, trust_store)
        if not manifest.supports_api(api_version):
            raise ValueError("Provider package is incompatible with this agent API.")
        data = self._load()
        current = data["providers"].get(manifest.id, {"versions": [], "active_version": None, "enabled": False})
        if current["versions"] and tuple(int(x) for x in manifest.version.split(".")) <= max(tuple(int(x) for x in v["version"].split(".")) for v in current["versions"]):
            raise ValueError("Provider upgrades must use a version greater than every installed version.")
        target_dir = self.packages / manifest.id
        if target_dir.is_symlink():
            raise ValueError("Provider package directory must not be a symlink.")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{manifest.version}.vwpkg"
        if target.exists():
            raise ValueError("This provider version is already installed.")
        package_bytes = package.read_bytes()
        if hashlib.sha256(package_bytes).hexdigest() != digest:
            raise ValueError("Provider package changed during installation.")
        fd, temporary = tempfile.mkstemp(prefix="package-", suffix=".tmp", dir=target_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(package_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        current["versions"] = [v for v in current["versions"] if v["version"] != manifest.version]
        signature_target = target.with_suffix(".sig.json")
        signature_bytes = signature.read_bytes()
        fd, signature_temp = tempfile.mkstemp(prefix="signature-", suffix=".tmp", dir=target_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(signature_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(signature_temp, signature_target)
        finally:
            if os.path.exists(signature_temp): os.unlink(signature_temp)
        current["versions"].append({"version": manifest.version, "sha256": digest, "key_id": key_id, "package": str(target.relative_to(self.root)), "signature": str(signature_target.relative_to(self.root))})
        current["versions"].sort(key=lambda v: tuple(int(x) for x in v["version"].split(".")))
        current["active_version"] = manifest.version
        current["enabled"] = False
        current["manifest"] = manifest.model_dump()
        data["providers"][manifest.id] = current
        self._event(data, "install", manifest.id, manifest.version)
        self._save(data)
        return {"id": manifest.id, "version": manifest.version, "sha256": digest, "key_id": key_id, "enabled": False}

    def set_enabled(self, provider_id: str, enabled: bool) -> dict[str, Any]:
        data = self._load()
        item = data["providers"].get(provider_id)
        if not item:
            raise ValueError("Provider is not installed.")
        item["enabled"] = enabled
        self._event(data, "enable" if enabled else "disable", provider_id, item["active_version"])
        self._save(data)
        return {"id": provider_id, "enabled": enabled, "active_version": item["active_version"]}

    def rollback(self, provider_id: str) -> dict[str, Any]:
        data = self._load()
        item = data["providers"].get(provider_id)
        if not item or len(item["versions"]) < 2:
            raise ValueError("No previous provider version is available.")
        versions = item["versions"]
        current_index = next(i for i, v in enumerate(versions) if v["version"] == item["active_version"])
        if current_index == 0:
            raise ValueError("No earlier provider version is available.")
        item["active_version"] = versions[current_index - 1]["version"]
        item["manifest"]["version"] = item["active_version"]
        item["enabled"] = False
        self._event(data, "rollback", provider_id, item["active_version"])
        self._save(data)
        return {"id": provider_id, "active_version": item["active_version"], "enabled": False}

    def list(self) -> list[dict[str, Any]]:
        return [{"id": key, "active_version": value["active_version"], "enabled": value["enabled"], "installed_versions": [v["version"] for v in value["versions"]]} for key, value in sorted(self._load()["providers"].items())]


    def uninstall(self, provider_id: str) -> dict[str, Any]:
        data = self._load()
        item = data["providers"].get(provider_id)
        if not item:
            raise ValueError("Provider is not installed.")
        if item["enabled"]:
            raise ValueError("Disable the provider before uninstalling it.")
        raw_target_dir = self.packages / provider_id
        if raw_target_dir.is_symlink():
            raise ValueError("Provider package directory is unsafe.")
        target_dir = raw_target_dir.resolve()
        if self.packages.resolve() not in target_dir.parents:
            raise ValueError("Provider package directory is unsafe.")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        del data["providers"][provider_id]
        self._event(data, "uninstall", provider_id, item.get("active_version"))
        self._save(data)
        return {"id": provider_id, "uninstalled": True}

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._load()["events"][-max(1, min(limit, 1000)):]

    def is_active_enabled(self, provider_id: str, version: str) -> bool:
        item = self._load()["providers"].get(provider_id)
        return bool(item and item["enabled"] and item["active_version"] == version)

    def active_package(self, provider_id: str, *, trust_store: str | Path, api_version: str) -> tuple[Path, dict[str, Any]]:
        item = self._load()["providers"].get(provider_id)
        if not item or not item["enabled"]:
            raise ValueError("Provider is not enabled.")
        version = next(v for v in item["versions"] if v["version"] == item["active_version"])
        raw_path = self.root / version["package"]
        if raw_path.is_symlink():
            raise ValueError("Installed provider package must not be a symlink.")
        path = raw_path.resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError("Installed provider package is missing or unsafe.")
        raw_signature = self.root / version["signature"]
        if raw_signature.is_symlink():
            raise ValueError("Installed provider signature must not be a symlink.")
        signature = raw_signature.resolve()
        if self.root not in signature.parents or not signature.is_file():
            raise ValueError("Installed provider signature is missing or unsafe.")
        manifest, digest, key_id = verify_package(path, signature, Path(trust_store))
        if digest != version["sha256"] or key_id != version["key_id"] or manifest.version != item["active_version"]:
            raise ValueError("Installed provider metadata does not match its signed package.")
        if not manifest.supports_api(api_version):
            raise ValueError("Installed provider is no longer compatible with this agent API.")
        return path, manifest.model_dump()
