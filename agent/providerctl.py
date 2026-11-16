"""Local administrator CLI for signed provider packages."""
import argparse
import json
import os
from pathlib import Path
from app.providers.package_manager import ProviderRegistry
from app.providers.runtime import WorkerProvider

ROOT = Path(os.getenv("VPS_PROVIDER_REGISTRY", "agent-data/providers"))
API_VERSION = "1.0.0"

def main():
    parser = argparse.ArgumentParser(prog="virtuewise-provider")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install"); install.add_argument("package", type=Path); install.add_argument("--signature", type=Path, required=True); install.add_argument("--trust-store", type=Path, required=True)
    for name in ("list", "events"): sub.add_parser(name)
    for name in ("enable", "disable", "rollback", "uninstall", "check"): sub.add_parser(name).add_argument("provider_id")
    args = parser.parse_args(); registry = ProviderRegistry(ROOT)
    if args.command == "install": result = registry.install(args.package, args.signature, args.trust_store, api_version=API_VERSION)
    elif args.command == "list": result = registry.list()
    elif args.command == "events": result = registry.events()
    elif args.command == "check":
        package, manifest = registry.active_package(args.provider_id, trust_store=Path(os.environ["VPS_PROVIDER_TRUST_STORE"]), api_version=API_VERSION)
        provider = WorkerProvider(package, manifest)
        result = {"id": args.provider_id, "inventory_count": len(provider.list_vms()), "host_metrics": provider.host_monitor(), "healthy": True}
    elif args.command == "uninstall": result = registry.uninstall(args.provider_id)
    elif args.command == "enable": result = registry.set_enabled(args.provider_id, True)
    elif args.command == "disable": result = registry.set_enabled(args.provider_id, False)
    else: result = registry.rollback(args.provider_id)
    print(json.dumps(result, indent=2))

if __name__ == "__main__": main()
