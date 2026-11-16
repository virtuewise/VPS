"""Strict provider manifest parsing; deliberately does not load plugin code."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.providers.capabilities import validate_capabilities

_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_PLUGIN_ID = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)+$")


def _version(value: str) -> tuple[int, int, int]:
    match = _SEMVER.fullmatch(value)
    if not match:
        raise ValueError("Versions must use stable semantic versioning (MAJOR.MINOR.PATCH).")
    return tuple(int(part) for part in match.groups())


class ProviderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    manifest_version: Literal[1]
    id: str = Field(min_length=3, max_length=128)
    name: str = Field(min_length=2, max_length=80)
    version: str
    provider_api_min: str
    provider_api_max: str
    provider_type: Literal["virtualization"]
    runtime: Literal["builtin", "python-worker"]
    entrypoint: str | None = Field(default=None, max_length=160)
    capabilities: list[str] = Field(min_length=1, max_length=32)
    maintainer: str = Field(min_length=2, max_length=120)
    license: str = Field(min_length=1, max_length=40)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _PLUGIN_ID.fullmatch(value):
            raise ValueError("Provider id must be a reverse-DNS-style identifier.")
        return value

    @field_validator("version", "provider_api_min", "provider_api_max")
    @classmethod
    def valid_version(cls, value: str) -> str:
        _version(value)
        return value

    @field_validator("capabilities")
    @classmethod
    def valid_capabilities(cls, value: list[str]) -> list[str]:
        validate_capabilities(value)
        return value

    @model_validator(mode="after")
    def valid_range(self) -> "ProviderManifest":
        if self.runtime == "python-worker" and not self.entrypoint:
            raise ValueError("python-worker providers must declare an entrypoint.")
        if self.runtime == "builtin" and self.entrypoint is not None:
            raise ValueError("builtin providers must not declare an entrypoint.")
        if self.entrypoint is not None and not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z_][A-Za-z0-9_]*", self.entrypoint):
            raise ValueError("Invalid provider entrypoint.")
        if _version(self.provider_api_min) > _version(self.provider_api_max):
            raise ValueError("provider_api_min must not exceed provider_api_max.")
        return self

    def supports_api(self, api_version: str) -> bool:
        current = _version(api_version)
        return _version(self.provider_api_min) <= current <= _version(self.provider_api_max)


def load_provider_manifest(path: str | Path, *, api_version: str) -> ProviderManifest:
    """Load and validate declarative metadata only; never import its runtime."""
    manifest_path = Path(path)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Provider manifest must be a regular, non-symlink file.")
    if manifest_path.stat().st_size > 32 * 1024:
        raise ValueError("Provider manifest exceeds the 32 KiB limit.")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Provider manifest is unreadable or invalid JSON.") from exc
    manifest = ProviderManifest.model_validate(raw)
    if not manifest.supports_api(api_version):
        raise ValueError(
            f"Provider {manifest.id} supports API {manifest.provider_api_min}.."
            f"{manifest.provider_api_max}, not {api_version}."
        )
    return manifest
