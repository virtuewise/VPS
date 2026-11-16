"""Load built-in providers or explicitly enabled signed third-party providers."""
from __future__ import annotations
import os
from pathlib import Path
from typing import Any

from app.providers import hyperv, libvirt
from app.providers.interface import validate_provider
from app.providers.manifest import ProviderManifest, load_provider_manifest
from app.providers.package_manager import ProviderRegistry
from app.providers.runtime import WorkerProvider

API_VERSION = "1.0.0"


def load_configured_provider(name: str | None = None) -> tuple[Any, ProviderManifest, str]:
    selected = (name or os.getenv("VPS_PROVIDER", "hyperv")).strip().lower()
    if selected in {"hyperv", "libvirt"}:
        provider = libvirt if selected == "libvirt" else hyperv
        manifest = load_provider_manifest(Path(__file__).resolve().parents[2] / "provider-manifests" / f"{selected}.json", api_version=API_VERSION)
    elif selected.startswith("plugin:"):
        provider_id = selected.removeprefix("plugin:")
        if not provider_id:
            raise RuntimeError("VPS_PROVIDER plugin selection must include a provider id.")
        registry_path = os.getenv("VPS_PROVIDER_REGISTRY")
        trust_store = os.getenv("VPS_PROVIDER_TRUST_STORE")
        if not registry_path or not trust_store:
            raise RuntimeError("Third-party providers require VPS_PROVIDER_REGISTRY and VPS_PROVIDER_TRUST_STORE.")
        registry = ProviderRegistry(registry_path)
        package, raw_manifest = registry.active_package(provider_id, trust_store=trust_store, api_version=API_VERSION)
        manifest = ProviderManifest.model_validate(raw_manifest)
        provider = WorkerProvider(package, raw_manifest, activation_check=lambda: registry.is_active_enabled(provider_id, raw_manifest["version"]))
        selected = provider_id
    else:
        raise RuntimeError("VPS_PROVIDER must be hyperv, libvirt, or plugin:<provider-id>.")
    validate_provider(provider)
    return provider, manifest, selected
