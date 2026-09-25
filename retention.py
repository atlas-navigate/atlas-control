"""
How long Atlas keeps personal history, and deleting it for real.

Defaults (privacy-leaning; the owner can change them in Settings → Privacy):

  Ray conversations           kept until deleted        retention_ai_days = 0
  Ray explain/provenance      30 days                   retention_explain_days
  Location history (track)    7 days                    retention_location_days
  Shared-phone positions      30 days                   retention_tracker_days

"0" means keep until the owner deletes. Deleting uses SQLite secure_delete
(freed pages are overwritten) and truncates the write-ahead log, so removed
records do not linger in the database file.
"""
import time

import database as db

DEFAULTS = {
    "retention_ai_days": 0,
    "retention_explain_days": 30,
    "retention_location_days": 7,
    "retention_tracker_days": 30,
}
LIMITS = (0, 3650)


def settings():
    stored = db.get_app_settings()
    out = {}
    for key, default in DEFAULTS.items():
        try:
            out[key] = max(LIMITS[0], min(LIMITS[1], int(stored.get(key, default))))
        except (TypeError, ValueError):
            out[key] = default
    return out


def update(values):
    clean = {}
    for key in DEFAULTS:
        if key in values:
            v = int(values[key])
            if not LIMITS[0] <= v <= LIMITS[1]:
                raise ValueError(f"{key} must be between {LIMITS[0]} and {LIMITS[1]} days")
            clean[key] = str(v)
    if clean:
        db.set_app_settings(clean)
    return settings()


class _SecureDelete:
    """Connection context with secure_delete on and the WAL truncated after."""

    def __enter__(self):
        self.conn = db.get_db()
        self.conn.execute("PRAGMA secure_delete = ON")
        return self.conn

    def __exit__(self, *exc):
        try:
            if exc[0] is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self.conn.execute("PRAGMA secure_delete = OFF")
        return False


def _cutoff(days):
    return int(time.time()) - days * 86400


def apply():
    """Daily: drop what has outlived its retention period. Returns counts."""
    cfg = settings()
    counts = {}
    with _SecureDelete() as conn:
        if cfg["retention_ai_days"]:
            cut = _cutoff(cfg["retention_ai_days"])
            old = [r[0] for r in conn.execute(
                "SELECT id FROM ai_chats WHERE COALESCE(updated_at, created_at, 0) < ?", (cut,))]
            for chat_id in old:
                conn.execute("DELETE FROM ai_messages WHERE chat_id=?", (chat_id,))
                conn.execute("DELETE FROM ai_chats WHERE id=?", (chat_id,))
            counts["ai_chats"] = len(old)
        if cfg["retention_explain_days"]:
            counts["explain"] = conn.execute(
                "UPDATE ai_messages SET explain=NULL WHERE explain IS NOT NULL AND created_at < ?",
                (_cutoff(cfg["retention_explain_days"]),)).rowcount
        if cfg["retention_location_days"]:
            counts["positions"] = conn.execute(
                "DELETE FROM positions WHERE timestamp < ?", (_cutoff(cfg["retention_location_days"]),)).rowcount
        if cfg["retention_tracker_days"]:
            counts["trackers"] = conn.execute(
                "DELETE FROM tracker_devices WHERE COALESCE(last_seen, 0) < ?",
                (_cutoff(cfg["retention_tracker_days"]),)).rowcount
    return counts


def delete_ai_history():
    with _SecureDelete() as conn:
        n = conn.execute("SELECT COUNT(*) FROM ai_chats").fetchone()[0]
        conn.execute("DELETE FROM ai_messages")
        conn.execute("DELETE FROM ai_chats")
    return {"conversations": n}


def delete_location_history():
    """Atlas's recorded track, shared-phone positions and recorded hikes.
    Saved places, routes and waypoints are the owner's own content and stay."""
    with _SecureDelete() as conn:
        counts = {
            "positions": conn.execute("DELETE FROM positions").rowcount,
            "trackers": conn.execute("DELETE FROM tracker_devices").rowcount,
            "hikes": conn.execute("DELETE FROM hike_sessions").rowcount,
        }
        conn.execute("UPDATE ai_messages SET explain=NULL WHERE explain IS NOT NULL")
    return counts
