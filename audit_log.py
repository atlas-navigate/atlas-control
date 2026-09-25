"""
Local record of security-relevant actions: settings and network changes,
pairing, updates, rollbacks, factory reset, privacy deletions.

Entries say what happened and which paired device did it — never content:
no passwords, SSIDs, coordinates, messages, prompts or setting values.
Kept on the device only, capped at 2000 entries / 180 days.
"""
import json
import time

import database as db

MAX_ROWS = 2000
MAX_AGE_DAYS = 180


def record(action, actor=None, **detail):
    safe = {k: v for k, v in detail.items() if isinstance(v, (int, float, bool)) or
            (isinstance(v, str) and len(v) <= 80) or
            (isinstance(v, list) and all(isinstance(x, str) and len(x) <= 60 for x in v))}
    try:
        conn = db.get_db()
        conn.execute("INSERT INTO audit_log (ts, action, actor, detail) VALUES (?, ?, ?, ?)",
                     (int(time.time()), action, (actor or "")[:60], json.dumps(safe) if safe else None))
        conn.commit()
    except Exception:
        pass


def actor_from(environ):
    client = environ.get("atlas.client") if environ else None
    return client["name"] if client else None


def recent(limit=100):
    rows = db.get_db().execute(
        "SELECT ts, action, actor, detail FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["detail"] = json.loads(item["detail"]) if item["detail"] else {}
        out.append(item)
    return out


def prune():
    conn = db.get_db()
    conn.execute("DELETE FROM audit_log WHERE ts < ?", (int(time.time()) - MAX_AGE_DAYS * 86400,))
    conn.execute("DELETE FROM audit_log WHERE id NOT IN (SELECT id FROM audit_log ORDER BY id DESC LIMIT ?)",
                 (MAX_ROWS,))
    conn.commit()
