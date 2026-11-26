"""Codexio core/db.py — database helpers shared by app.py and studios/.

The connection manager itself (`_db`, local SQLite or Turso HTTP/Hrana) stays in app.py and is passed in as a
`db` argument (a context-manager factory: `with db() as conn:`). This keeps this module free of circular imports.

Contents
  * add_column_if_missing  - Turso-safe schema migration (no PRAGMA table_info)
  * list_chats_light       - chat list WITHOUT the big file_text column (fast startup)
  * load_chat_file         - lazy fetch of one chat's attachment (file_name, file_text)
  * saved_sites            - encrypted multi-site WordPress manager (Fernet)
  * usage_by_engine        - token / message aggregation from messages.meta
"""
import base64
import hashlib
import json

# ---------------------------------------------------------------- schema helpers


def add_column_if_missing(conn, table, column_ddl):
    """Turso's HTTP Hrana API does not always return PRAGMA table_info rows, so just try the ALTER and ignore
    'duplicate column' errors."""
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
    except Exception:
        pass


SAVED_SITES_DDL = """
CREATE TABLE IF NOT EXISTS saved_sites (
    id TEXT PRIMARY KEY,
    name TEXT,
    url TEXT,
    username TEXT,
    encrypted_creds TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def ensure_saved_sites_table(conn):
    conn.execute(SAVED_SITES_DDL)


# ---------------------------------------------------------------- chat list (lightweight)


def list_chats_light(db):
    """[(id, title, pinned, has_file)] oldest first. Never reads file_text (up to 300k chars per chat), so the
    startup query stays tiny even over Turso HTTP."""
    with db() as conn:
        return conn.execute(
            "SELECT id, title, pinned, "
            "CASE WHEN file_name IS NOT NULL AND file_name != '' THEN 1 ELSE 0 END AS has_file "
            "FROM chats ORDER BY updated_at ASC, rowid ASC"
        ).fetchall()


def load_chat_file(db, cid):
    """(file_name, file_text) of ONE chat — called only when that chat is opened."""
    with db() as conn:
        row = conn.execute("SELECT file_name, file_text FROM chats WHERE id = ?", (cid,)).fetchone()
    return (row[0], row[1]) if row else (None, None)


# ---------------------------------------------------------------- encryption


def derive_key(secret):
    """32-byte URL-safe base64 key derived from a secret with SHA-256 (Fernet format)."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())


def get_fernet(secret):
    """Fernet built from APP_PASSWORD or TURSO_AUTH_TOKEN. Returns None if no secret is set or the
    `cryptography` package is missing (the app then simply keeps sites in the session only)."""
    if not secret:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(derive_key(secret))
    except Exception:
        return None


def _site_id(name):
    return hashlib.sha256((name or "").strip().lower().encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------- saved sites CRUD


def save_site(db, fernet, entry):
    """entry: {name, url, user, pw, wc_key, wc_secret}. Application password and WooCommerce keys are encrypted."""
    creds = {"pw": entry.get("pw", ""), "wc_key": entry.get("wc_key", ""), "wc_secret": entry.get("wc_secret", "")}
    token = fernet.encrypt(json.dumps(creds).encode("utf-8")).decode("utf-8")
    with db() as conn:
        ensure_saved_sites_table(conn)
        conn.execute(
            """
            INSERT INTO saved_sites (id, name, url, username, encrypted_creds)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, url = excluded.url,
                username = excluded.username, encrypted_creds = excluded.encrypted_creds
            """,
            (_site_id(entry["name"]), entry["name"], entry["url"], entry.get("user", ""), token),
        )


def load_saved_sites(db, fernet):
    """Returns (sites, undecryptable_count). Sites use the same dict shape the WP Automation manager uses."""
    sites, bad = [], 0
    with db() as conn:
        ensure_saved_sites_table(conn)
        rows = conn.execute("SELECT name, url, username, encrypted_creds FROM saved_sites ORDER BY created_at").fetchall()
    for name, url, user, token in rows:
        try:
            c = json.loads(fernet.decrypt((token or "").encode("utf-8")).decode("utf-8"))
        except Exception:
            bad += 1  # key changed (APP_PASSWORD / TURSO_AUTH_TOKEN was rotated) or data corrupted
            continue
        sites.append({"name": name, "url": url, "user": user, "pw": c.get("pw", ""),
                      "wc_key": c.get("wc_key", ""), "wc_secret": c.get("wc_secret", "")})
    return sites, bad


def delete_saved_site(db, name):
    with db() as conn:
        ensure_saved_sites_table(conn)
        conn.execute("DELETE FROM saved_sites WHERE id = ?", (_site_id(name),))


# ---------------------------------------------------------------- usage tracker

ENGINES = ("Claude", "Gemini", "Groq")


def engine_of(model):
    low = (model or "").lower()
    if "claude" in low:
        return "Claude"
    if "gemini" in low:
        return "Gemini"
    return "Groq"


def usage_by_engine(db):
    """{engine: {"messages": n, "tokens": n, "secs": float}} aggregated from messages.meta (JSON)."""
    out = {e: {"messages": 0, "tokens": 0, "secs": 0.0} for e in ENGINES}
    with db() as conn:
        rows = conn.execute("SELECT meta FROM messages WHERE meta IS NOT NULL AND meta != ''").fetchall()
    for (raw,) in rows:
        try:
            meta = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        e = out[engine_of(meta.get("model"))]
        e["messages"] += 1
        try:
            e["tokens"] += int(meta.get("tokens", 0) or 0)
            e["secs"] += float(meta.get("secs", 0) or 0)
        except (TypeError, ValueError):
            pass
    return out
