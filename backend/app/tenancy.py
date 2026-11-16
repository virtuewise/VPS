from __future__ import annotations
import hashlib, hmac, secrets, sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

def _b64(value: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310000)
    return "pbkdf2_sha256$310000$" + _b64(salt) + "$" + _b64(digest)

def verify_password(password: str, encoded: str) -> bool:
    try:
        import base64
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256": return False
        dec = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), dec(salt), int(rounds))
        return hmac.compare_digest(actual, dec(expected))
    except (ValueError, TypeError): return False

def initialize_tenancy(path: Path, admin_password: str, default_quota: int = 3) -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')),
          vm_quota INTEGER NOT NULL DEFAULT 3, active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("""CREATE TABLE IF NOT EXISTS vm_ownership (
          vm_name TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        db.execute("""CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id INTEGER,
          actor_username TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
          details TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        if not db.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            db.execute("INSERT INTO users(username,password_hash,role,vm_quota) VALUES('admin',?,'admin',-1)", (hash_password(admin_password),))
        db.commit()

def audit(path: Path, actor: dict[str, Any], action: str, target: str = "", details: str = "") -> None:
    with closing(sqlite3.connect(path)) as db:
        db.execute("INSERT INTO audit_log(actor_user_id,actor_username,action,target,details) VALUES(?,?,?,?,?)",
                   (actor["id"], actor["username"], action, target[:200], details[:1000]))
        db.commit()
