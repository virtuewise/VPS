"""Build and sign VirtueWise provider packages with Ed25519."""
import argparse
import base64
import hashlib
import json
import os
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.providers.manifest import ProviderManifest


def keygen(args):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    args.private_key.write_text(base64.b64encode(private).decode() + "\n", encoding="ascii")
    args.public_key.write_text(base64.b64encode(public).decode() + "\n", encoding="ascii")
    try: os.chmod(args.private_key, 0o600)
    except OSError: pass
    print(json.dumps({"key_id": args.key_id, "public_key_base64": base64.b64encode(public).decode(), "private_key": str(args.private_key), "public_key": str(args.public_key)}, indent=2))


def build(args):
    source = args.source.resolve()
    manifest_path = source / "manifest.json"
    manifest = ProviderManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    if manifest.runtime != "python-worker": raise SystemExit("Manifest runtime must be python-worker")
    files = [manifest_path]
    for path in source.rglob("*.py"):
        if path.is_symlink() or not path.is_file(): raise SystemExit("Symlink or non-file source rejected")
        files.append(path)
    if len(files) > 256: raise SystemExit("Too many source files")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            archive.write(path, path.relative_to(source).as_posix())
    print(json.dumps({"package": str(args.output), "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "provider": manifest.id, "version": manifest.version}, indent=2))


def sign(args):
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(args.private_key.read_text(encoding="ascii").strip(), validate=True))
    digest = hashlib.sha256(args.package.read_bytes()).digest()
    signature = private.sign(digest)
    args.output.write_text(json.dumps({"key_id": args.key_id, "signature": base64.b64encode(signature).decode()}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"signature": str(args.output), "sha256": digest.hex(), "key_id": args.key_id}, indent=2))


def main():
    parser = argparse.ArgumentParser(prog="virtuewise-provider-sign")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("keygen"); p.add_argument("--key-id", required=True); p.add_argument("--private-key", type=Path, required=True); p.add_argument("--public-key", type=Path, required=True); p.set_defaults(func=keygen)
    p = sub.add_parser("build"); p.add_argument("source", type=Path); p.add_argument("output", type=Path); p.set_defaults(func=build)
    p = sub.add_parser("sign"); p.add_argument("package", type=Path); p.add_argument("--key-id", required=True); p.add_argument("--private-key", type=Path, required=True); p.add_argument("--output", type=Path, required=True); p.set_defaults(func=sign)
    args = parser.parse_args(); args.func(args)

if __name__ == "__main__": main()
