import sqlite3
import json
import time
import os
import threading

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "meshtastic.db")

# Per-thread sqlite connection. We use threading.local (not gevent.local)
# because the hot writers (gps-reader, mesh reader, nav loop, ai worker) are
# native threading.Thread workers, not greenlets. gevent.local on a native
# thread lazily spins up a per-thread gevent hub, which can cascade into
# busy-loop behaviour when those threads also call into pyserial/select.
# Greenlets handling HTTP requests all share one OS thread anyway, so a
# single shared connection is fine for them (sqlite3 with WAL serialises
# writes; check_same_thread is satisfied since same OS thread).
_local = threading.local()


def normalize_node_id(node_id):
    if node_id is None:
        return None
    if isinstance(node_id, int):
        return "!" + format(node_id, "08x")

    text = str(node_id).strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in {"none", "null"}:
        return None

    if text.startswith("!"):
        return "!" + text[1:].lower()

    if text.isdigit():
        return "!" + format(int(text), "08x")

    return text

def get_db():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, timeout=10)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=10000")
        _local.conn.execute("PRAGMA synchronous=NORMAL")   # WAL mode keeps this safe; no fsync per commit
        _local.conn.execute("PRAGMA cache_size=-65536")    # 64 MB page cache
        _local.conn.execute("PRAGMA mmap_size=268435456")  # 256 MB memory-mapped I/O
        _local.conn.execute("PRAGMA temp_store=MEMORY")    # temp tables in RAM
    return _local.conn

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS nodes (
            node_id TEXT PRIMARY KEY,
            long_name TEXT,
            short_name TEXT,
            hw_model TEXT,
            mac_addr TEXT,
            snr REAL,
            rssi INTEGER,
            last_heard INTEGER,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            battery_level INTEGER,
            voltage REAL,
            channel_util REAL,
            air_util_tx REAL,
            uptime INTEGER,
            role TEXT,
            is_favorite INTEGER DEFAULT 0,
            alias TEXT,
            raw_json TEXT,
            updated_at INTEGER
        )
    """)
    # migrate: add alias column if it doesn't exist yet
    try:
        c.execute("ALTER TABLE nodes ADD COLUMN alias TEXT")
    except Exception:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id TEXT,
            to_id TEXT,
            channel INTEGER DEFAULT 0,
            text TEXT,
            rx_time INTEGER,
            rx_snr REAL,
            rx_rssi INTEGER,
            hop_limit INTEGER,
            hop_start INTEGER,
            is_direct INTEGER DEFAULT 0,
            ack INTEGER DEFAULT 0,
            raw_json TEXT,
            packet_id INTEGER
        )
    """)
    # Migrate existing databases — add packet_id column if absent
    try:
        c.execute("ALTER TABLE messages ADD COLUMN packet_id INTEGER")
    except Exception:
        pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT,
            timestamp INTEGER,
            battery_level INTEGER,
            voltage REAL,
            channel_util REAL,
            air_util_tx REAL,
            uptime INTEGER,
            temperature REAL,
            relative_humidity REAL,
            barometric_pressure REAL,
            current REAL,
            raw_json TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id TEXT,
            timestamp INTEGER,
            latitude REAL,
            longitude REAL,
            altitude REAL,
            speed REAL,
            heading REAL,
            sats_in_view INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS topology (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_id TEXT,
            to_id TEXT,
            snr REAL,
            rssi INTEGER,
            timestamp INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            alert_type TEXT,
            severity TEXT,
            node_id TEXT,
            title TEXT,
            message TEXT,
            acknowledged INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            channel_num INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            created_at INTEGER
        )
    """)

    # seed Channel 0 as General if no channels exist
    if c.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 0:
        c.execute("INSERT INTO channels (channel_num, name, created_at) VALUES (0, 'General', ?)",
                  (int(time.time()),))

    # ── App settings ───────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ── AI tables ──────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT DEFAULT 'New Chat',
            model TEXT,
            created_at INTEGER,
            updated_at INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            role TEXT,
            content TEXT,
            created_at INTEGER,
            tokens INTEGER,
            duration_ms INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            tags TEXT,
            embedding TEXT,
            created_at INTEGER,
            is_seed INTEGER DEFAULT 0
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # ── Waypoints ──────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS waypoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            altitude REAL,
            color TEXT DEFAULT '#00e5a0',
            created_at INTEGER
        )
    """)

    # ── Routes ─────────────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT,
            color TEXT DEFAULT '#3b82f6',
            created_at INTEGER,
            updated_at INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS route_waypoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            route_id INTEGER NOT NULL,
            waypoint_id INTEGER NOT NULL,
            order_idx INTEGER DEFAULT 0
        )
    """)

    # ── Hike sessions ──────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS hike_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            node_id TEXT,
            status TEXT DEFAULT 'active',
            start_time INTEGER,
            end_time INTEGER,
            total_distance_m REAL DEFAULT 0,
            elevation_gain_m REAL DEFAULT 0,
            notes TEXT
        )
    """)

    # ── Phone Tracker devices ──────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS tracker_devices (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            color TEXT DEFAULT '#3b82f6',
            latitude REAL,
            longitude REAL,
            accuracy REAL,
            altitude REAL,
            speed REAL,
            heading REAL,
            battery REAL,
            last_seen INTEGER,
            notes TEXT
        )
    """)

    # ── Navigation favorites ───────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS nav_favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)

    # ── Scheduled jobs ─────────────────────────────────────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_jobs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT    NOT NULL,
            job_type         TEXT    NOT NULL,  -- 'message' | 'gps'
            enabled          INTEGER DEFAULT 1,
            node_id          TEXT,              -- NULL = broadcast
            channel          INTEGER DEFAULT 0,
            message_text     TEXT,
            gps_lat          REAL,
            gps_lon          REAL,
            gps_alt          REAL    DEFAULT 0,
            schedule_type    TEXT    NOT NULL,  -- 'once' | 'interval' | 'daily' | 'weekly'
            run_at           INTEGER,           -- epoch (once) | secs-since-midnight (daily/weekly)
            interval_seconds INTEGER,
            days_of_week     INTEGER DEFAULT 0, -- bitmask bit0=Mon … bit6=Sun
            next_run         INTEGER,
            last_run         INTEGER,
            run_count        INTEGER DEFAULT 0,
            created_at       INTEGER
        )
    """)

    # ── Performance indexes ────────────────────────────────────────────
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_nodes_last_heard ON nodes(last_heard DESC)",
        "CREATE INDEX IF NOT EXISTS idx_messages_channel_time ON messages(channel, rx_time DESC)",
        "CREATE INDEX IF NOT EXISTS idx_messages_from_id ON messages(from_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_packet_id ON messages(packet_id) WHERE packet_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_telemetry_node_ts ON telemetry(node_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_positions_node_ts ON positions(node_id, timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_positions_timestamp ON positions(timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_topology_from_to ON topology(from_id, to_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_alerts_ack_ts ON alerts(acknowledged, timestamp DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ai_messages_chat_ts ON ai_messages(chat_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_hike_sessions_status ON hike_sessions(status)",
        "CREATE INDEX IF NOT EXISTS idx_jobs_enabled_next ON scheduled_jobs(enabled, next_run)",
    ]:
        c.execute(idx_sql)

    # Calendar events table
    c.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT DEFAULT '',
            start_ts    INTEGER NOT NULL,
            end_ts      INTEGER,
            all_day     INTEGER DEFAULT 0,
            color       TEXT DEFAULT 'blue',
            created_at  INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()

# --- Node operations ---
def upsert_node(node_data):
    node_id = normalize_node_id(node_data.get("node_id"))
    if not node_id:
        return False

    db = get_db()
    db.execute("""
        INSERT INTO nodes (node_id, long_name, short_name, hw_model, mac_addr,
            snr, rssi, last_heard, latitude, longitude, altitude,
            battery_level, voltage, channel_util, air_util_tx, uptime, role, raw_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            long_name=COALESCE(excluded.long_name, nodes.long_name),
            short_name=COALESCE(excluded.short_name, nodes.short_name),
            hw_model=COALESCE(excluded.hw_model, nodes.hw_model),
            mac_addr=COALESCE(excluded.mac_addr, nodes.mac_addr),
            snr=COALESCE(excluded.snr, nodes.snr),
            rssi=COALESCE(excluded.rssi, nodes.rssi),
            last_heard=CASE
                WHEN excluded.last_heard IS NULL THEN nodes.last_heard
                WHEN nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard THEN excluded.last_heard
                ELSE nodes.last_heard
            END,
            latitude=CASE
                WHEN excluded.latitude IS NULL THEN nodes.latitude
                WHEN excluded.last_heard IS NULL THEN excluded.latitude
                WHEN nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard THEN excluded.latitude
                ELSE nodes.latitude
            END,
            longitude=CASE
                WHEN excluded.longitude IS NULL THEN nodes.longitude
                WHEN excluded.last_heard IS NULL THEN excluded.longitude
                WHEN nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard THEN excluded.longitude
                ELSE nodes.longitude
            END,
            altitude=CASE
                WHEN excluded.altitude IS NULL THEN nodes.altitude
                WHEN excluded.last_heard IS NULL THEN excluded.altitude
                WHEN nodes.last_heard IS NULL OR excluded.last_heard >= nodes.last_heard THEN excluded.altitude
                ELSE nodes.altitude
            END,
            battery_level=COALESCE(excluded.battery_level, nodes.battery_level),
            voltage=COALESCE(excluded.voltage, nodes.voltage),
            channel_util=COALESCE(excluded.channel_util, nodes.channel_util),
            air_util_tx=COALESCE(excluded.air_util_tx, nodes.air_util_tx),
            uptime=COALESCE(excluded.uptime, nodes.uptime),
            role=COALESCE(excluded.role, nodes.role),
            raw_json=COALESCE(excluded.raw_json, nodes.raw_json),
            updated_at=excluded.updated_at
    """, (
        node_id, node_data.get("long_name"), node_data.get("short_name"),
        node_data.get("hw_model"), node_data.get("mac_addr"),
        node_data.get("snr"), node_data.get("rssi"), node_data.get("last_heard"),
        node_data.get("latitude"), node_data.get("longitude"), node_data.get("altitude"),
        node_data.get("battery_level"), node_data.get("voltage"),
        node_data.get("channel_util"), node_data.get("air_util_tx"),
        node_data.get("uptime"), node_data.get("role"),
        json.dumps({**node_data, "node_id": node_id}), int(time.time())
    ))
    db.commit()
    return True

def get_all_nodes():
    db = get_db()
    rows = db.execute("SELECT * FROM nodes ORDER BY last_heard DESC").fetchall()
    nodes = []
    for row in rows:
        node = dict(row)
        normalized = normalize_node_id(node.get("node_id"))
        if not normalized:
            continue
        if normalized != node.get("node_id"):
            node["node_id"] = normalized
        nodes.append(_decorate_node(node))
    return nodes

def get_node(node_id):
    node_id = normalize_node_id(node_id)
    if not node_id:
        return None
    db = get_db()
    row = db.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()
    return _decorate_node(dict(row)) if row else None

def _decorate_node(node):
    raw = node.get("raw_json")
    parsed = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = {}
    user = parsed.get("user", {}) if isinstance(parsed, dict) else {}
    metadata = parsed.get("metadata", {}) if isinstance(parsed, dict) else {}
    public_key_present = bool(parsed.get("public_key_present") or user.get("publicKey"))
    has_pkc = bool(parsed.get("has_pkc") or metadata.get("hasPKC"))
    node["public_key_present"] = public_key_present
    node["has_pkc"] = has_pkc
    node["dm_ready"] = bool(public_key_present or has_pkc)
    return node

# --- Channel operations ---
def get_channels():
    db = get_db()
    rows = db.execute("SELECT * FROM channels ORDER BY channel_num").fetchall()
    return [dict(r) for r in rows]

def upsert_channel(channel_num, name):
    db = get_db()
    db.execute("""
        INSERT INTO channels (channel_num, name, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(channel_num) DO UPDATE SET name=excluded.name
    """, (channel_num, name, int(time.time())))
    db.commit()

def delete_channel(channel_num):
    if channel_num == 0:
        return False  # primary channel cannot be removed
    db = get_db()
    db.execute("DELETE FROM channels WHERE channel_num=?", (channel_num,))
    db.commit()
    return True

def update_node_alias(node_id, alias):
    node_id = normalize_node_id(node_id)
    if not node_id:
        return False
    db = get_db()
    db.execute("UPDATE nodes SET alias=? WHERE node_id=?", (alias if alias else None, node_id))
    db.commit()
    return True

# --- Message operations ---
def insert_message(msg_data):
    """Insert a message, silently ignoring duplicates (same packet_id)."""
    db = get_db()
    db.execute("""
        INSERT OR IGNORE INTO messages
            (from_id, to_id, channel, text, rx_time, rx_snr, rx_rssi,
             hop_limit, hop_start, is_direct, ack, raw_json, packet_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        msg_data.get("from_id"), msg_data.get("to_id"), msg_data.get("channel", 0),
        msg_data.get("text"), msg_data.get("rx_time", int(time.time())),
        msg_data.get("rx_snr"), msg_data.get("rx_rssi"),
        msg_data.get("hop_limit"), msg_data.get("hop_start"),
        msg_data.get("is_direct", 0), msg_data.get("ack", 0),
        json.dumps(msg_data), msg_data.get("packet_id"),
    ))
    db.commit()

def get_messages(limit=200, channel=None):
    db = get_db()
    if channel is not None:
        rows = db.execute(
            "SELECT * FROM messages WHERE channel=? ORDER BY rx_time DESC LIMIT ?",
            (channel, limit)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM messages ORDER BY rx_time DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_preferred_dm_channel(node_id, default=0):
    db = get_db()
    row = db.execute("""
        SELECT channel
        FROM messages
        WHERE from_id=? AND to_id='^all'
        ORDER BY rx_time DESC
        LIMIT 1
    """, (node_id,)).fetchone()
    if row and row["channel"] is not None:
        return int(row["channel"])
    row = db.execute("""
        SELECT channel
        FROM messages
        WHERE from_id=? OR to_id=?
        ORDER BY rx_time DESC
        LIMIT 1
    """, (node_id, node_id)).fetchone()
    if row and row["channel"] is not None:
        return int(row["channel"])
    return int(default or 0)

def get_last_broadcast_for_node(node_id):
    node_id = normalize_node_id(node_id)
    if not node_id:
        return None
    db = get_db()
    row = db.execute("""
        SELECT channel, rx_time
        FROM messages
        WHERE from_id=? AND to_id='^all'
        ORDER BY rx_time DESC
        LIMIT 1
    """, (node_id,)).fetchone()
    return dict(row) if row else None

# --- Telemetry operations ---
def insert_telemetry(tel_data):
    db = get_db()
    db.execute("""
        INSERT INTO telemetry (node_id, timestamp, battery_level, voltage,
            channel_util, air_util_tx, uptime, temperature, relative_humidity,
            barometric_pressure, current, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        tel_data.get("node_id"), tel_data.get("timestamp", int(time.time())),
        tel_data.get("battery_level"), tel_data.get("voltage"),
        tel_data.get("channel_util"), tel_data.get("air_util_tx"),
        tel_data.get("uptime"), tel_data.get("temperature"),
        tel_data.get("relative_humidity"), tel_data.get("barometric_pressure"),
        tel_data.get("current"), json.dumps(tel_data)
    ))
    db.commit()

def get_telemetry(node_id=None, hours=24):
    db = get_db()
    since = int(time.time()) - (hours * 3600)
    if node_id:
        rows = db.execute(
            "SELECT * FROM telemetry WHERE node_id=? AND timestamp>? ORDER BY timestamp",
            (node_id, since)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM telemetry WHERE timestamp>? ORDER BY timestamp", (since,)
        ).fetchall()
    return [dict(r) for r in rows]

# --- Position operations ---
def update_node_position(node_id, latitude, longitude, altitude, last_heard):
    """Update only the GPS position fields on an existing node row."""
    db = get_db()
    db.execute(
        "UPDATE nodes SET latitude=?, longitude=?, altitude=?, last_heard=? WHERE node_id=?",
        (latitude, longitude, altitude, last_heard, node_id)
    )
    db.commit()

def delete_node(node_id):
    """Remove a node row (used to clean up the synthetic local_gps placeholder)."""
    db = get_db()
    db.execute("DELETE FROM nodes WHERE node_id=?", (node_id,))
    db.execute("DELETE FROM positions WHERE node_id=?", (node_id,))
    db.commit()

def insert_position(pos_data):
    db = get_db()
    db.execute("""
        INSERT INTO positions (node_id, timestamp, latitude, longitude, altitude,
            speed, heading, sats_in_view)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        pos_data.get("node_id"), pos_data.get("timestamp", int(time.time())),
        pos_data.get("latitude"), pos_data.get("longitude"), pos_data.get("altitude"),
        pos_data.get("speed"), pos_data.get("heading"), pos_data.get("sats_in_view")
    ))
    db.commit()

def get_positions(node_id=None):
    db = get_db()
    if node_id:
        rows = db.execute(
            "SELECT * FROM positions WHERE node_id=? ORDER BY timestamp DESC LIMIT 500",
            (node_id,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM positions ORDER BY timestamp DESC LIMIT 1000"
        ).fetchall()
    return [dict(r) for r in rows]

# --- Topology operations ---
def upsert_link(from_id, to_id, snr, rssi):
    db = get_db()
    db.execute("""
        INSERT INTO topology (from_id, to_id, snr, rssi, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (from_id, to_id, snr, rssi, int(time.time())))
    db.commit()

def get_topology():
    db = get_db()
    rows = db.execute("""
        SELECT from_id, to_id, snr, rssi, MAX(timestamp) as timestamp
        FROM topology
        GROUP BY from_id, to_id
        ORDER BY timestamp DESC
    """).fetchall()
    return [dict(r) for r in rows]

# --- Alert operations ---
def insert_alert(alert_data):
    db = get_db()
    db.execute("""
        INSERT INTO alerts (timestamp, alert_type, severity, node_id, title, message)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        int(time.time()), alert_data.get("alert_type"), alert_data.get("severity"),
        alert_data.get("node_id"), alert_data.get("title"), alert_data.get("message")
    ))
    db.commit()

def get_alerts(limit=100, unacked_only=False):
    db = get_db()
    if unacked_only:
        rows = db.execute(
            "SELECT * FROM alerts WHERE acknowledged=0 ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]

def ack_alert(alert_id):
    db = get_db()
    db.execute("UPDATE alerts SET acknowledged=1 WHERE id=?", (alert_id,))
    db.commit()

def ack_all_alerts():
    db = get_db()
    db.execute("UPDATE alerts SET acknowledged=1 WHERE acknowledged=0")
    db.commit()

def get_stats():
    db = get_db()
    since = int(time.time()) - 900
    row = db.execute("""
        SELECT
            (SELECT COUNT(*) FROM nodes)                              AS total_nodes,
            (SELECT COUNT(*) FROM messages)                           AS total_messages,
            (SELECT COUNT(*) FROM alerts WHERE acknowledged=0)        AS unacked_alerts,
            (SELECT COUNT(*) FROM nodes WHERE last_heard > ?)         AS online_nodes
    """, (since,)).fetchone()
    return {
        "total_nodes":    row[0],
        "total_messages": row[1],
        "unacked_alerts": row[2],
        "online_nodes":   row[3],
    }


def get_latest_telemetry_per_node(hours=24):
    """Return one telemetry row per node — the most recent within the last N hours."""
    db = get_db()
    since = int(time.time()) - (hours * 3600)
    rows = db.execute("""
        SELECT t.* FROM telemetry t
        INNER JOIN (
            SELECT node_id, MAX(timestamp) AS max_ts
            FROM telemetry WHERE timestamp > ?
            GROUP BY node_id
        ) latest ON t.node_id = latest.node_id AND t.timestamp = latest.max_ts
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def get_latest_positions_per_node():
    """Return the single most recent position row per node."""
    db = get_db()
    rows = db.execute("""
        SELECT p.* FROM positions p
        INNER JOIN (
            SELECT node_id, MAX(timestamp) AS max_ts
            FROM positions
            GROUP BY node_id
        ) latest ON p.node_id = latest.node_id AND p.timestamp = latest.max_ts
    """).fetchall()
    return [dict(r) for r in rows]


# ── App Settings ──────────────────────────────────────────────────────────────

_APP_SETTING_DEFAULTS = {
    "serial_port": "AUTO",
    "gps_port": "AUTO",
    "gps_baud": "9600",
    "web_port": "5000",
    "dashboard_refresh_s": "5",
    "battery_alert_pct": "20",
    "node_offline_min": "30",
    "map_default_zoom": "13",
    "show_offline_nodes": "true",
    "node_online_window_h": "2",
    "units": "metric",
    "date_format": "relative",
    "timezone": "auto",            # IANA tz name (e.g. America/New_York) or "auto" for browser

    "ai_enabled": "true",
    "comms_enabled": "true",
    "gps_share_mode": "off",        # off | all | selected
    "gps_share_nodes": "",          # comma-separated node IDs
    "gps_share_channels": "",       # comma-separated channel numbers
    "gps_share_interval": "30",     # seconds between broadcasts
    "accent_color": "green",
    "dashboard_title": "Atlas Control",
    "hidden_pages": "[]",
    "sidebar_order": "[]",
}

def get_app_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM app_settings").fetchall()
    result = dict(_APP_SETTING_DEFAULTS)
    for row in rows:
        result[row["key"]] = row["value"]
    return result

def set_app_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value))
    )
    db.commit()

def set_app_settings(settings_dict):
    db = get_db()
    for key, value in settings_dict.items():
        db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )
    db.commit()

# ── AI Chat CRUD ──────────────────────────────────────────────────────────────

def ai_create_chat(model=None):
    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "INSERT INTO ai_chats (title, model, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("New Chat", model, now, now)
    )
    db.commit()
    row = db.execute("SELECT * FROM ai_chats WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row) if row else None


def ai_get_chats():
    db = get_db()
    rows = db.execute("SELECT * FROM ai_chats ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def ai_get_chat(chat_id):
    db = get_db()
    row = db.execute("SELECT * FROM ai_chats WHERE id=?", (chat_id,)).fetchone()
    return dict(row) if row else None


def ai_update_chat_title(chat_id, title):
    db = get_db()
    db.execute(
        "UPDATE ai_chats SET title=?, updated_at=? WHERE id=?",
        (title, int(time.time()), chat_id)
    )
    db.commit()


def ai_delete_chat(chat_id):
    db = get_db()
    db.execute("DELETE FROM ai_messages WHERE chat_id=?", (chat_id,))
    db.execute("DELETE FROM ai_chats WHERE id=?", (chat_id,))
    db.commit()


# ── AI Message CRUD ───────────────────────────────────────────────────────────

def ai_add_message(chat_id, role, content, tokens=None, duration_ms=None):
    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "INSERT INTO ai_messages (chat_id, role, content, created_at, tokens, duration_ms) VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, role, content, now, tokens, duration_ms)
    )
    db.execute("UPDATE ai_chats SET updated_at=? WHERE id=?", (now, chat_id))
    db.commit()
    row = db.execute("SELECT * FROM ai_messages WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row) if row else None


def ai_get_messages(chat_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM ai_messages WHERE chat_id=? ORDER BY created_at ASC",
        (chat_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ── AI Document CRUD ──────────────────────────────────────────────────────────

def ai_get_document_count():
    """Return count of documents without fetching rows."""
    db = get_db()
    return db.execute("SELECT COUNT(*) FROM ai_documents").fetchone()[0]


def ai_get_documents():
    """Return all documents without the embedding field (for display)."""
    db = get_db()
    rows = db.execute(
        "SELECT id, title, content, tags, created_at, is_seed FROM ai_documents ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def ai_get_documents_with_embeddings():
    """Return all documents including the embedding JSON (for RAG)."""
    db = get_db()
    rows = db.execute("SELECT * FROM ai_documents ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def ai_add_document(title, content, tags="", embedding=None, is_seed=False):
    """Insert a document and return its id."""
    db = get_db()
    now = int(time.time())
    cur = db.execute(
        "INSERT INTO ai_documents (title, content, tags, embedding, created_at, is_seed) VALUES (?, ?, ?, ?, ?, ?)",
        (title, content, tags, embedding, now, 1 if is_seed else 0)
    )
    db.commit()
    return cur.lastrowid


def ai_update_document_embedding(doc_id, embedding_json):
    db = get_db()
    db.execute("UPDATE ai_documents SET embedding=? WHERE id=?", (embedding_json, doc_id))
    db.commit()


def ai_delete_document(doc_id):
    db = get_db()
    db.execute("DELETE FROM ai_documents WHERE id=?", (doc_id,))
    db.commit()


def ai_seed_documents(docs):
    """Upsert seed documents: insert new ones, update content+tags of existing ones.
    Clears the embedding on update so the doc is re-embedded with fresh content.
    This ensures edits to SURVIVAL_DOCS are reflected in the DB on next restart."""
    db = get_db()
    now = int(time.time())
    changed = 0
    for doc in docs:
        row = db.execute(
            "SELECT id, content, tags FROM ai_documents WHERE title=? AND is_seed=1",
            (doc["title"],)
        ).fetchone()
        if row is None:
            # New document — insert it
            db.execute(
                "INSERT INTO ai_documents (title, content, tags, embedding, created_at, is_seed) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (doc["title"], doc["content"], doc.get("tags", ""), None, now, 1)
            )
            changed += 1
        else:
            # Existing document — update if content or tags changed, clear embedding to re-embed
            new_content = doc["content"]
            new_tags    = doc.get("tags", "")
            if row["content"] != new_content or row["tags"] != new_tags:
                db.execute(
                    "UPDATE ai_documents SET content=?, tags=?, embedding=NULL WHERE id=?",
                    (new_content, new_tags, row["id"])
                )
                changed += 1
    if changed:
        db.commit()


# ── AI Settings ───────────────────────────────────────────────────────────────

# Shared Markdown formatting directive. Defined once here so the in-code default
# (AI_DEFAULTS below) and the one-time migration of existing saved prompts use
# the exact same text. Formatting is prioritized over brevity on purpose.
AI_FORMATTING_GUIDE = (
    "FORMATTING — Prioritize clear structure over brevity. Reply in GitHub-flavored "
    "Markdown, and when a table, list, or headings present the answer more clearly, use "
    "them even if the reply runs longer:\n"
    "- **Bold** key terms; use `inline code` for commands, filenames, values, and node IDs.\n"
    "- Use ## / ### headings to label the sections of a longer answer.\n"
    "- Use - bullets for unordered points and 1. numbered lists for ordered steps.\n"
    "- Put multi-line commands, config, or output in ```fenced code blocks```.\n"
    "- Use Markdown tables for comparisons or structured data (e.g. node SNR/hops, specs, schedules).\n"
    "- Use > blockquotes for warnings or important callouts.\n"
    "Clarity and correct formatting come before keeping it short; still, keep a genuinely "
    "simple one-line answer plain — don't over-format or pad with filler."
)

# Canonical AI settings defaults — the SINGLE source of truth. ai_manager imports
# this as DEFAULT_SETTINGS, so the fresh-install defaults and the in-code fallbacks
# can never drift. Per-box values saved in the ai_settings table override these at
# runtime (see ai_get_settings).
AI_DEFAULTS = {
    "model": "qwen3.5:2b",
    "embed_model": "qwen3-embedding:0.6b",
    "system_prompt": (
        "You are Ray — an AI assistant built into Atlas Control, "
        "a Meshtastic mesh-network dashboard running on an off-grid Jetson device.\n\n"
        "You have two sources of information:\n"
        "1. LIVE DATA sections (SYSTEM STATUS, MESH NETWORK STATE, ALERTS, CURRENT POSITION) — always current and accurate.\n"
        "2. KNOWLEDGE BASE sections — curated reference docs on survival, radio, ballistics, and off-grid topics.\n\n"
        "GUIDELINES:\n"
        "- Be direct and practical. No filler phrases.\n\n"
        + AI_FORMATTING_GUIDE
    ),
    "warmup_on_start": "true",
    "keep_alive_hours": "10",
    "rag_enabled": "true",
    "rag_top_k": "3",
    "inject_mesh_context": "true",
    "num_ctx": "4096",
    "num_gpu": "-1",      # -1 = let Ollama auto-place layers (forced counts hard-fail on tight RAM)
    "num_thread": "6",    # Jetson Orin Nano: 6× Cortex-A78AE cores
    "num_batch": "512",   # prompt tokens processed per GPU batch
    # Qwen3-family non-thinking sampling (official recommendation); lower
    # temperatures cause repetition loops on Qwen3.x models.
    "temperature": "0.7",
    "top_p": "0.8",
    "top_k": "20",
    "num_predict": "512",
}


def ai_get_settings():
    """Return dict of all settings, with defaults filled in for missing keys."""
    db = get_db()
    rows = db.execute("SELECT key, value FROM ai_settings").fetchall()
    settings = dict(AI_DEFAULTS)
    for row in rows:
        settings[row["key"]] = row["value"]
    return settings


def ai_set_setting(key, value):
    db = get_db()
    db.execute(
        "INSERT INTO ai_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value))
    )
    db.commit()


def ai_set_settings(settings_dict):
    """Bulk update settings."""
    db = get_db()
    for key, value in settings_dict.items():
        db.execute(
            "INSERT INTO ai_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )
    db.commit()



# ── Phone Tracker ──────────────────────────────────────────────────────────────

def upsert_tracker_device(data):
    db = get_db()
    db.execute("""
        INSERT INTO tracker_devices (id, name, color, latitude, longitude, accuracy, altitude, speed, heading, battery, last_seen, notes)
        VALUES (:id, :name, :color, :latitude, :longitude, :accuracy, :altitude, :speed, :heading, :battery, :last_seen, :notes)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name, color=excluded.color,
            latitude=excluded.latitude, longitude=excluded.longitude,
            accuracy=excluded.accuracy, altitude=excluded.altitude,
            speed=excluded.speed, heading=excluded.heading,
            battery=excluded.battery, last_seen=excluded.last_seen,
            notes=excluded.notes
    """, {
        "id":        data["id"],
        "name":      data.get("name", "Unknown"),
        "color":     data.get("color", "#3b82f6"),
        "latitude":  data.get("latitude"),
        "longitude": data.get("longitude"),
        "accuracy":  data.get("accuracy"),
        "altitude":  data.get("altitude"),
        "speed":     data.get("speed"),
        "heading":   data.get("heading"),
        "battery":   data.get("battery"),
        "last_seen": data.get("last_seen", int(time.time())),
        "notes":     data.get("notes"),
    })
    db.commit()

def get_tracker_devices():
    db = get_db()
    rows = db.execute("SELECT * FROM tracker_devices ORDER BY last_seen DESC").fetchall()
    return [dict(r) for r in rows]

def get_tracker_device(device_id):
    db = get_db()
    r = db.execute("SELECT * FROM tracker_devices WHERE id=?", (device_id,)).fetchone()
    return dict(r) if r else None

def delete_tracker_device(device_id):
    db = get_db()
    db.execute("DELETE FROM tracker_devices WHERE id=?", (device_id,))
    db.commit()


def get_nav_favorites():
    db = get_db()
    rows = db.execute("SELECT * FROM nav_favorites ORDER BY name ASC").fetchall()
    return [dict(r) for r in rows]

def add_nav_favorite(name, lat, lon):
    import time as _t
    db = get_db()
    cur = db.execute(
        "INSERT INTO nav_favorites (name, lat, lon, created_at) VALUES (?, ?, ?, ?)",
        (name, float(lat), float(lon), int(_t.time()))
    )
    db.commit()
    return cur.lastrowid

def rename_nav_favorite(fav_id, name):
    db = get_db()
    db.execute("UPDATE nav_favorites SET name=? WHERE id=?", (name, fav_id))
    db.commit()

def delete_nav_favorite(fav_id):
    db = get_db()
    db.execute("DELETE FROM nav_favorites WHERE id=?", (fav_id,))
    db.commit()

def prune_old_data():
    """Remove time-series data on a tiered retention schedule to cap disk usage:
      - topology  : 1 day   (high-frequency link snapshots)
      - telemetry : 7 days  (per-node device metrics)
      - positions : 7 days  (GPS track history)
      - alerts    : 30 days (operational events)
      - messages  : 90 days (mesh chat log)
    """
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("DELETE FROM topology  WHERE timestamp < (strftime('%s','now') - 86400)")
        conn.execute("DELETE FROM telemetry WHERE timestamp < (strftime('%s','now') - 604800)")
        conn.execute("DELETE FROM positions WHERE timestamp < (strftime('%s','now') - 604800)")
        conn.execute("DELETE FROM alerts    WHERE timestamp < (strftime('%s','now') - 2592000)")
        conn.execute("DELETE FROM messages  WHERE rx_time   < (strftime('%s','now') - 7776000)")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        conn.execute("PRAGMA optimize")
    finally:
        conn.close()

# --- Scheduled jobs ---
def _job_row(r):
    return dict(r) if r else None

def get_jobs():
    db = get_db()
    return [dict(r) for r in db.execute(
        "SELECT * FROM scheduled_jobs ORDER BY created_at DESC"
    ).fetchall()]

def get_job(job_id):
    db = get_db()
    return _job_row(db.execute(
        "SELECT * FROM scheduled_jobs WHERE id=?", (job_id,)
    ).fetchone())

def get_due_jobs():
    db = get_db()
    now = int(time.time())
    return [dict(r) for r in db.execute(
        "SELECT * FROM scheduled_jobs WHERE enabled=1 AND next_run IS NOT NULL AND next_run <= ?",
        (now,)
    ).fetchall()]

def create_job(data):
    db = get_db()
    cur = db.execute("""
        INSERT INTO scheduled_jobs
          (name, job_type, enabled, node_id, channel, message_text,
           gps_lat, gps_lon, gps_alt, schedule_type, run_at,
           interval_seconds, days_of_week, next_run, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (data['name'], data['job_type'], data.get('enabled', 1),
          data.get('node_id'), data.get('channel', 0), data.get('message_text'),
          data.get('gps_lat'), data.get('gps_lon'), data.get('gps_alt', 0),
          data['schedule_type'], data.get('run_at'), data.get('interval_seconds'),
          data.get('days_of_week', 0), data['next_run'], int(time.time())))
    db.commit()
    return cur.lastrowid

def update_job(job_id, data):
    db = get_db()
    db.execute("""
        UPDATE scheduled_jobs SET
          name=?, job_type=?, enabled=?, node_id=?, channel=?, message_text=?,
          gps_lat=?, gps_lon=?, gps_alt=?, schedule_type=?, run_at=?,
          interval_seconds=?, days_of_week=?, next_run=?
        WHERE id=?
    """, (data['name'], data['job_type'], data.get('enabled', 1),
          data.get('node_id'), data.get('channel', 0), data.get('message_text'),
          data.get('gps_lat'), data.get('gps_lon'), data.get('gps_alt', 0),
          data['schedule_type'], data.get('run_at'), data.get('interval_seconds'),
          data.get('days_of_week', 0), data['next_run'], job_id))
    db.commit()

def delete_job(job_id):
    db = get_db()
    db.execute("DELETE FROM scheduled_jobs WHERE id=?", (job_id,))
    db.commit()

def update_job_after_run(job_id, last_run, next_run, enabled):
    db = get_db()
    db.execute("""
        UPDATE scheduled_jobs
        SET last_run=?, next_run=?, run_count=run_count+1, enabled=?
        WHERE id=?
    """, (last_run, next_run, enabled, job_id))
    db.commit()

# ── Calendar Events ────────────────────────────────────────────────────────────

def get_calendar_events(start_ts=None, end_ts=None):
    db = get_db()
    if start_ts and end_ts:
        rows = db.execute(
            "SELECT * FROM calendar_events WHERE start_ts <= ? AND (end_ts >= ? OR (end_ts IS NULL AND start_ts >= ?)) ORDER BY start_ts",
            (end_ts, start_ts, start_ts)
        ).fetchall()
    elif start_ts:
        rows = db.execute(
            "SELECT * FROM calendar_events WHERE start_ts >= ? ORDER BY start_ts",
            (start_ts,)
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM calendar_events ORDER BY start_ts").fetchall()
    return [dict(r) for r in rows]

def create_calendar_event(data):
    db = get_db()
    cursor = db.execute(
        """INSERT INTO calendar_events (title, description, start_ts, end_ts, all_day, color, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            data["title"], data.get("description", ""),
            int(data["start_ts"]), int(data["end_ts"]) if data.get("end_ts") else None,
            1 if data.get("all_day") else 0,
            data.get("color", "blue"), int(time.time()),
        )
    )
    db.commit()
    return cursor.lastrowid

def update_calendar_event(event_id, data):
    db = get_db()
    db.execute(
        """UPDATE calendar_events SET title=?, description=?, start_ts=?, end_ts=?, all_day=?, color=?
           WHERE id=?""",
        (
            data["title"], data.get("description", ""),
            int(data["start_ts"]), int(data["end_ts"]) if data.get("end_ts") else None,
            1 if data.get("all_day") else 0,
            data.get("color", "blue"), event_id,
        )
    )
    db.commit()

def delete_calendar_event(event_id):
    db = get_db()
    db.execute("DELETE FROM calendar_events WHERE id=?", (event_id,))
    db.commit()

# ── Factory reset ─────────────────────────────────────────────────────────────

# app_settings keys reserved for the Heltec snapshot — preserved across resets.
_FACTORY_DEFAULTS_KEY = "factory_defaults_device_v1"

def get_factory_defaults_device():
    """Return the stored Heltec config snapshot as a dict, or None if not yet captured."""
    db = get_db()
    row = db.execute(
        "SELECT value FROM app_settings WHERE key=?", (_FACTORY_DEFAULTS_KEY,)
    ).fetchone()
    if not row or not row["value"]:
        return None
    try:
        import json as _json
        return _json.loads(row["value"])
    except Exception:
        return None

def set_factory_defaults_device(snapshot):
    """Persist the Heltec config snapshot (a dict) as JSON in app_settings."""
    import json as _json
    db = get_db()
    db.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (_FACTORY_DEFAULTS_KEY, _json.dumps(snapshot)),
    )
    db.commit()

def factory_reset_data():
    """Wipe messaging, AI chat, node, and GPS-coordinate data.

    Preserves: app_settings (incl. factory_defaults snapshot), ai_settings,
    ai_documents (knowledge base), channels (general channel), calendar_events.
    """
    db = get_db()
    # Messaging
    db.execute("DELETE FROM messages")
    # AI chat data (chats + their messages); knowledge base & settings preserved
    db.execute("DELETE FROM ai_messages")
    db.execute("DELETE FROM ai_chats")
    # Node information & per-node time series
    db.execute("DELETE FROM nodes")
    db.execute("DELETE FROM telemetry")
    db.execute("DELETE FROM topology")
    db.execute("DELETE FROM alerts")
    # GPS coordinate information
    db.execute("DELETE FROM positions")
    db.execute("DELETE FROM tracker_devices")
    db.execute("DELETE FROM hike_sessions")
    db.execute("DELETE FROM nav_favorites")
    db.execute("DELETE FROM route_waypoints")
    db.execute("DELETE FROM routes")
    db.execute("DELETE FROM waypoints")
    # Scheduled jobs may carry GPS payloads / node refs — wipe to avoid dangling state
    db.execute("DELETE FROM scheduled_jobs")
    db.commit()
    # Reclaim space and rebuild indexes
    try:
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        db.execute("PRAGMA optimize")
    except Exception:
        pass
