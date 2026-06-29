#!/usr/bin/env python3
"""
GPS Node — SparkFun NEO-M8U Dead Reckoning GPS
Ground-up rewrite for Atlas offline cyberdeck (Jetson Orin Nano).

Features:
  - UBX binary protocol: configure NAV-PVT, NAV-ATT, ESF-STATUS for richer fix data
  - Fix state machine: LOCKED / DEGRADED / MODULE_DR / SELF_DR / LOST
  - Kalman filter (4-state constant-velocity) for smooth, low-jitter positions
  - Self dead reckoning: extrapolates position when serial connection is lost
  - DR continues until position uncertainty exceeds 300 m or 5 minutes elapse
  - Meshtastic mesh broadcasting with configurable mode/interval
  - Thread-safe Socket.IO events, drop-in replacement for GpsManager
  - Auto-reconnect with exponential backoff
"""

import glob as _glob
import math
import os
import struct
import threading
import time
import logging
from typing import Optional, Callable

try:
    import fcntl          # Linux-only; needed for the I2C (DDC) GPS path
    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import serial
    _HAS_SERIAL = True
except ImportError:
    _HAS_SERIAL = False

try:
    import pynmea2
    _HAS_PYNMEA2 = True
except ImportError:
    _HAS_PYNMEA2 = False

import database as db

logger = logging.getLogger("gps-node")

# ── Fix type constants ──────────────────────────────────────────────────────

class FixType:
    NONE       = 0   # No position at all
    MODULE_DR  = 1   # Dead reckoning reported by NEO-M8U itself (fix_type=1 or mode=E)
    FIX_2D     = 2   # 2D GNSS fix
    FIX_3D     = 3   # 3D GNSS fix (best)
    GNSS_DR    = 4   # GNSS + DR combined (fix_type=4)
    TIME_ONLY  = 5   # Time-only fix
    SELF_DR    = 6   # Our own extrapolation when serial is lost
    DGPS       = 7   # Differential GPS

    # NMEA quality → our FixType
    _FROM_GGA = {0: 0, 1: 3, 2: 7, 4: 3, 5: 3, 6: 1}
    # UBX NAV-PVT fixType → our FixType
    _FROM_UBX = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}

    LABEL = {0: "No Fix", 1: "DR (module)", 2: "GPS 2D", 3: "GPS 3D",
             4: "GNSS+DR", 5: "Time Only", 6: "DR (self)", 7: "DGPS"}

    @staticmethod
    def label(ft): return FixType.LABEL.get(ft, "Unknown")
    @staticmethod
    def has_position(ft): return ft > FixType.NONE
    @staticmethod
    def is_dr(ft): return ft in (FixType.MODULE_DR, FixType.GNSS_DR, FixType.SELF_DR)
    @staticmethod
    def is_gnss(ft): return ft in (FixType.FIX_2D, FixType.FIX_3D, FixType.DGPS)


# ── UBX protocol helpers ────────────────────────────────────────────────────

_UBX_SYNC = (0xB5, 0x62)
# Class IDs
_CLS_NAV, _CLS_CFG, _CLS_ESF = 0x01, 0x06, 0x10
# Message IDs
_ID_NAV_PVT, _ID_NAV_ATT, _ID_NAV_STATUS = 0x07, 0x05, 0x03
_ID_ESF_STATUS, _ID_ESF_INS              = 0x10, 0x15
_ID_CFG_MSG, _ID_CFG_RATE, _ID_CFG_PRT  = 0x01, 0x08, 0x00


def _ubx_cksum(data: bytes):
    a = b = 0
    for byte in data:
        a = (a + byte) & 0xFF
        b = (b + a) & 0xFF
    return a, b


def _ubx_frame(cls: int, msg_id: int, payload: bytes = b"") -> bytes:
    hdr = bytes([0xB5, 0x62, cls, msg_id,
                 len(payload) & 0xFF, (len(payload) >> 8) & 0xFF])
    ck_a, ck_b = _ubx_cksum(bytes([cls, msg_id,
                                    len(payload) & 0xFF,
                                    (len(payload) >> 8) & 0xFF]) + payload)
    return hdr + payload + bytes([ck_a, ck_b])


def _ubx_cfg_msg(cls: int, msg_id: int, rate: int = 1) -> bytes:
    """UBX-CFG-MSG: enable/disable message on DDC, UART1, and USB ports."""
    # 8-byte payload: msgClass, msgID, rates for DDC/UART1/UART2/USB/SPI/reserved
    return _ubx_frame(_CLS_CFG, _ID_CFG_MSG, bytes([cls, msg_id, rate, rate, 0, rate, 0, 0]))


def _ubx_cfg_rate(meas_rate_ms: int = 1000, nav_rate: int = 1, time_ref: int = 1) -> bytes:
    """UBX-CFG-RATE: pin GPS measurement output to 1 Hz so the read thread
    never has to parse a higher-rate flood inherited from a previous flash
    config."""
    return _ubx_frame(_CLS_CFG, _ID_CFG_RATE, struct.pack(
        "<HHH", meas_rate_ms, nav_rate, time_ref
    ))


def _ubx_cfg_prt_usb() -> bytes:
    """UBX-CFG-PRT for USB (portID=3): enable UBX+NMEA output protocol."""
    # 20-byte payload: portID, reserved, txReady, mode, baudRate,
    #                  inProtoMask, outProtoMask, flags, reserved
    return _ubx_frame(_CLS_CFG, _ID_CFG_PRT, struct.pack(
        "<BBHIIHHHH",
        3,       # portID = USB
        0,       # reserved1
        0,       # txReady (disabled)
        0,       # mode (not used for USB)
        0,       # baudRate (not used for USB)
        0x0007,  # inProtoMask: UBX + NMEA + RTCM
        0x0003,  # outProtoMask: UBX + NMEA
        0,       # flags
        0,       # reserved2
    ))


# ── Kalman filter (4-state constant-velocity, 2D position) ─────────────────

class _Kalman2D:
    """
    State: [lat, lon, dlat/s, dlon/s]  (degrees and degrees/second)
    Measurement: [lat, lon]
    Process model: constant velocity
    """
    # Measurement noise variance by fix type (degrees^2)
    _R = {FixType.FIX_3D: 4e-9, FixType.DGPS: 2e-9,
          FixType.FIX_2D: 1.5e-8, FixType.GNSS_DR: 3e-8,
          FixType.MODULE_DR: 7e-8, FixType.SELF_DR: 2e-7}
    _R_DEFAULT = 5e-8
    _Q = 1e-10   # process noise (degrees/s)^2 per second

    def __init__(self):
        self.x = None   # [4,1]
        self.P = None   # [4,4]
        self._t = None
        self._ok = False

    def reset(self, lat, lon):
        if not _HAS_NUMPY:
            return
        self.x = np.array([[lat], [lon], [0.0], [0.0]])
        self.P = np.eye(4) * 1e-6
        self._t = time.monotonic()
        self._ok = True

    def update(self, lat, lon, fix_type, t=None):
        if not _HAS_NUMPY:
            return lat, lon
        t = t if t is not None else time.monotonic()
        if not self._ok:
            self.reset(lat, lon)
            return lat, lon

        dt = min(max(t - self._t, 0.0), 30.0)
        self._t = t
        if dt == 0:
            return float(self.x[0, 0]), float(self.x[1, 0])

        F = np.array([[1, 0, dt, 0],
                      [0, 1, 0,  dt],
                      [0, 0, 1,  0],
                      [0, 0, 0,  1]])
        q = self._Q
        Q = q * np.array([[dt**4/4, 0,       dt**3/2, 0      ],
                           [0,       dt**4/4, 0,       dt**3/2],
                           [dt**3/2, 0,       dt**2,   0      ],
                           [0,       dt**3/2, 0,       dt**2  ]])
        H = np.array([[1, 0, 0, 0],
                      [0, 1, 0, 0]])
        r = self._R.get(fix_type, self._R_DEFAULT)
        R = np.eye(2) * r

        x_p = F @ self.x
        P_p = F @ self.P @ F.T + Q
        S   = H @ P_p @ H.T + R
        K   = P_p @ H.T @ np.linalg.inv(S)
        z   = np.array([[lat], [lon]])
        self.x = x_p + K @ (z - H @ x_p)
        self.P = (np.eye(4) - K @ H) @ P_p
        return float(self.x[0, 0]), float(self.x[1, 0])

    def predict(self, dt):
        """Dead-reckoning predict step (no measurement). Returns smoothed (lat, lon) or None."""
        if not _HAS_NUMPY or not self._ok:
            return None
        dt = min(dt, 60.0)
        self.x[0, 0] += self.x[2, 0] * dt
        self.x[1, 0] += self.x[3, 0] * dt
        # Inflate position uncertainty
        self.P[0, 0] += self._Q * dt ** 2 * 1000
        self.P[1, 1] += self._Q * dt ** 2 * 1000
        self._t = time.monotonic()
        return float(self.x[0, 0]), float(self.x[1, 0])


# ── Port scanner ────────────────────────────────────────────────────────────

_GPS_UDEV = "/dev/gps"


# ── I2C (u-blox DDC) GPS support ─────────────────────────────────────────────
#
# The SparkFun NEO-M8U also exposes its receiver on a Qwiic / I2C connector
# (SDA/SCL on the Jetson 40-pin header pins 3 & 5 = /dev/i2c-7). u-blox calls
# this the DDC port. The whole GpsNode is written against a pyserial object,
# so rather than fork the read loop we wrap I2C in a tiny serial-compatible
# shim and feed it through unchanged.
#
# DDC protocol: register 0xFD/0xFE hold the 16-bit count of bytes available;
# register 0xFF is the data stream (NMEA + UBX, returns 0xFF when idle). We
# set the pointer to 0xFD, read the count, then read exactly that many bytes
# from the stream.

_I2C_SLAVE       = 0x0703    # <linux/i2c-dev.h> ioctl: bind slave address to fd
_DDC_DEFAULT_ADDR = 0x42     # u-blox default DDC (I2C) 7-bit address
_I2C_PORT_RE     = "i2c:"    # sentinel prefix for a resolved I2C GPS target


def _parse_i2c_port(spec):
    """Parse an 'i2c:<bus>:<addr>' sentinel into (bus:int, addr:int).
    Address is optional and defaults to the u-blox DDC address. Returns
    None if `spec` is not an I2C target."""
    if not isinstance(spec, str) or not spec.startswith(_I2C_PORT_RE):
        return None
    parts = spec[len(_I2C_PORT_RE):].split(":")
    try:
        bus = int(parts[0])
    except (ValueError, IndexError):
        return None
    addr = _DDC_DEFAULT_ADDR
    if len(parts) > 1 and parts[1]:
        try:
            addr = int(parts[1], 0)
        except ValueError:
            pass
    return bus, addr


class _I2CSerial:
    """Minimal pyserial-compatible reader for a u-blox GPS on the I2C/DDC bus.

    Implements only the surface GpsNode uses: ``is_open``, ``read(size)``,
    ``write(data)``, ``close()``. Bytes are pulled from the DDC stream into
    an internal buffer; ``read`` drains that buffer with the same timeout
    semantics as ``serial.Serial`` (returns what's available, possibly
    fewer than requested, b"" on timeout)."""

    def __init__(self, bus, addr=_DDC_DEFAULT_ADDR, baud=0, timeout=1.0):
        self.bus      = bus
        self.addr     = addr
        self.timeout  = timeout
        self._buf     = bytearray()
        self._fd      = os.open(f"/dev/i2c-{bus}", os.O_RDWR)
        try:
            fcntl.ioctl(self._fd, _I2C_SLAVE, addr)
        except OSError:
            os.close(self._fd)
            raise
        self.is_open  = True

    def _poll(self, cap=255):
        """Pull any pending DDC bytes into the buffer; return count read."""
        try:
            os.write(self._fd, b"\xFD")          # point at byte-count register
            hl = os.read(self._fd, 2)
            if len(hl) < 2 or hl[0] == 0xFF:     # 0xFFFF => bus idle
                return 0
            n = (hl[0] << 8) | hl[1]
            if n <= 0:
                return 0
            chunk = os.read(self._fd, min(n, cap))  # pointer is now at 0xFF
            if chunk:
                self._buf.extend(chunk)
            return len(chunk)
        except OSError:
            return 0

    def read(self, size=1):
        deadline = time.time() + (self.timeout or 0.0)
        while not self._buf:                      # wait for first byte(s)
            if self._poll():
                break
            if self.timeout is not None and time.time() >= deadline:
                return b""
            time.sleep(0.02)
        self._poll()                              # opportunistically top up
        n = min(size, len(self._buf))
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def write(self, data):
        try:
            os.write(self._fd, bytes(data))       # DDC accepts raw UBX/NMEA in
            return len(data)
        except OSError:
            return 0

    def reset_input_buffer(self):
        self._buf.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False
        try:
            os.close(self._fd)
        except OSError:
            pass


def _i2c_has_gps(bus, addr=_DDC_DEFAULT_ADDR, budget=2.0):
    """Return True if a u-blox-style GPS at (bus, addr) is streaming
    NMEA/UBX over DDC within `budget` seconds."""
    if not _HAS_FCNTL or not os.path.exists(f"/dev/i2c-{bus}"):
        return False
    try:
        s = _I2CSerial(bus, addr, timeout=0.5)
    except OSError:
        return False
    try:
        deadline = time.time() + budget
        buf = bytearray()
        while time.time() < deadline and len(buf) < 256:
            chunk = s.read(256)
            if chunk:
                buf.extend(chunk)
            if b"$G" in buf or b"$B" in buf or b"\xb5\x62" in buf:
                return True
        return b"$G" in buf or b"$B" in buf or b"\xb5\x62" in buf
    finally:
        s.close()


def _scan_gps_i2c(exclude_addrs=(0x41,)):
    """Probe the 40-pin header I2C buses for a u-blox GPS at 0x42 and
    return an 'i2c:<bus>:0x42' sentinel, or None. Bus 7 = header pins
    3/5 (also carries the UPS at 0x41), bus 1 = pins 27/28."""
    if not _HAS_FCNTL:
        return None
    addr = _DDC_DEFAULT_ADDR
    if addr in exclude_addrs:
        return None
    for bus in (7, 1, 0, 2):
        if _i2c_has_gps(bus, addr):
            return f"{_I2C_PORT_RE}{bus}:0x{addr:02x}"
    return None


def _sniff_nmea(port, baud, budget=2.5, read_timeout=0.4):
    """Listen briefly on `port` for an NMEA ``$`` sentence or a UBX sync
    pair (0xB5 0x62). Returns True if GPS-like traffic appears within
    `budget` seconds.

    Time-bounded on purpose: an on-board UART such as /dev/ttyTHS1 (the
    Jetson 40-pin header pins 8/10) always opens even when nothing is
    wired to it, so a fixed readline count would block budget×N seconds
    on a silent port and stall GPS startup. We bound by wall-clock
    instead.
    """
    try:
        with serial.Serial(port, baud, timeout=read_timeout) as s:
            deadline = time.time() + budget
            while time.time() < deadline:
                raw = s.readline()
                if not raw:
                    continue
                if raw[:1] == b"$" or (len(raw) >= 2 and raw[0] == 0xB5 and raw[1] == 0x62):
                    return True
    except Exception:
        return False
    return False


def _scan_gps_port(exclude=None, baud=9600, timeout=2):
    if not _HAS_SERIAL:
        return None
    excl = {os.path.realpath(p) for p in (exclude or [])
            if p and os.path.exists(p)}

    if os.path.exists(_GPS_UDEV):
        real = os.path.realpath(_GPS_UDEV)
        if real not in excl:
            return _GPS_UDEV

    for p in sorted(_glob.glob("/dev/serial/by-id/*")):
        if any(k in p.lower() for k in ("u-blox", "1546", "ublox")):
            if os.path.realpath(p) not in excl:
                return p

    # USB CDC/FTDI GPS first (historical default), then the on-board
    # high-speed UARTs exposed on the 40-pin GPIO header (/dev/ttyTHS*),
    # so a GPS wired to header pins 8 (TX) / 10 (RX) is auto-detected too.
    candidates = (sorted(_glob.glob("/dev/ttyACM*") + _glob.glob("/dev/ttyUSB*"))
                  + sorted(_glob.glob("/dev/ttyTHS*")))
    for port in candidates:
        if os.path.realpath(port) in excl:
            continue
        if _sniff_nmea(port, baud, budget=float(timeout)):
            return port

    # No UART/USB GPS — fall back to the I2C/DDC bus (Qwiic-wired u-blox).
    i2c = _scan_gps_i2c()
    if i2c:
        return i2c
    return None


# ── Haversine ───────────────────────────────────────────────────────────────

def _hav_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))


# ── Main GPS Node ────────────────────────────────────────────────────────────

_FALLBACK_NODE_ID = "local_gps"
# Persisted canonical id of *this* Atlas device's own node. Once we have ever
# learned it (from the live mesh radio or the Heltec's USB serial), we remember
# it so GPS fixes keep landing on the same "Atlas Control" row even when the
# radio is unplugged — instead of spawning a duplicate "Base Station" node.
_SELF_ID_SETTING = "local_node_id"


def _persisted_self_id() -> Optional[str]:
    try:
        v = db.get_app_settings().get(_SELF_ID_SETTING)
        return v if v and v != _FALLBACK_NODE_ID else None
    except Exception:
        return None


def _persist_self_id(nid: Optional[str]) -> None:
    if not nid or nid == _FALLBACK_NODE_ID:
        return
    try:
        if db.get_app_settings().get(_SELF_ID_SETTING) != nid:
            db.set_app_setting(_SELF_ID_SETTING, nid)
    except Exception:
        pass


def _heltec_node_id_from_udev() -> Optional[str]:
    """Derive the canonical Meshtastic node ID for the locally-attached
    Heltec V4 by reading the udev by-id symlinks. The Heltec V4 advertises
    its full chip MAC in the USB serial (e.g.
    ``usb-Espressif_Systems_heltec_wifi_lora_32_v4..._E8F60AC9F7C4-if00``)
    and Meshtastic uses the lower 32 bits of that MAC as the node ID.

    Returning the same node ID the radio would use means the GPS reader
    can populate the Atlas device's own row before — or instead of — the
    mesh radio ever talking to us, so the map and node list show a
    single unified entry rather than a stray ``local_gps`` placeholder
    alongside the Heltec node.
    """
    try:
        by_id_dir = "/dev/serial/by-id"
        if not os.path.isdir(by_id_dir):
            return None
        for entry in os.listdir(by_id_dir):
            low = entry.lower()
            if "heltec" not in low and "espressif" not in low:
                continue
            # Last 12 hex chars before "-if" / "-port" / etc. are the MAC.
            stem = entry.rsplit("-if", 1)[0]
            tail = stem.rstrip("_")
            # Grab the trailing run of hex characters from the stem.
            hex_chars = ""
            for ch in reversed(tail):
                if ch in "0123456789abcdefABCDEF":
                    hex_chars = ch + hex_chars
                else:
                    break
            if len(hex_chars) >= 8:
                return "!" + hex_chars[-8:].lower()
        return None
    except Exception:
        return None

# How long self-DR remains valid (seconds)
_SELF_DR_TIMEOUT    = 300.0
# Update interval for self-DR position emits
_SELF_DR_INTERVAL   = 2.0
# Minimum time between module-DR position emits
_MODULE_DR_INTERVAL = 2.0
# 1 m position dedup threshold in degrees (~1 m)
_POS_DEDUP_DEG = 1e-5


class GpsNode:
    """
    Drop-in replacement for GpsManager with full dead reckoning support.

    Dead reckoning modes:
      MODULE_DR  — NEO-M8U module reports DR positions (serial still connected,
                   module's internal IMU is estimating position)
      SELF_DR    — serial connection is gone; we extrapolate using last known
                   speed + heading until timeout or reconnect
    """

    def __init__(self, port="AUTO", baud=9600, socketio=None, mesh_manager=None):
        self.port         = port
        self.baud         = baud
        self.socketio     = socketio
        self.mesh_manager = mesh_manager

        self._ser          = None
        self._active_port  = None
        self.connected     = False
        self.running       = False
        self._thread       = None
        self._dr_thread    = None
        self._lock         = threading.Lock()

        # Current and last-good fix as plain dicts (backward compat)
        self.current_fix: Optional[dict] = None
        self._last_gnss_fix: Optional[dict] = None
        self._last_key      = None    # (lat5, lon5) dedup
        self._last_dr_emit  = 0.0
        self._last_accuracy = None    # last UBX accuracy; carried forward for NMEA fixes
        self._last_log_fix_type = None
        self._last_fix_log_ts = 0.0

        # Self-DR state
        self._self_dr_active = False
        self._self_dr_start  = 0.0

        # Kalman filter
        self._kf = _Kalman2D()

        # UBX stream parser state
        self._ubx_buf = bytearray()
        self._vehicle_heading: Optional[float] = None

        # GPS sharing config (thread-safe)
        self._share_lock     = threading.Lock()
        self._share_mode     = "off"
        self._share_nodes    = []
        self._share_channels = []
        self._share_interval = 30
        self._last_broadcast = 0.0

        # Optional callback: called on every fix dict
        self._on_fix_cb: Optional[Callable] = None

        # DB write throttle. The NEO-M8U emits multiple fixes per second
        # (UBX-NAV-PVT + NMEA GGA/RMC), and committing each one to SQLite
        # pegs the read thread at ~100% CPU and starves the rest of the
        # process. Persist at most once per second; the live socketio
        # emits below still fire at the native fix rate so the map dot
        # stays smooth.
        self._db_min_interval   = 1.0
        self._db_min_move_deg   = 1e-5     # ~1.1 m at the equator; force a write past this
        self._last_db_write_ts  = 0.0
        self._last_db_lat       = None
        self._last_db_lon       = None
        self._fallback_cleaned  = False

    # ── Public API (backward compatible + extended) ─────────────────────────

    def on_fix(self, cb: Callable):
        """Register callback for every position fix: cb(fix_dict)."""
        self._on_fix_cb = cb

    def get_status(self) -> dict:
        fix = self.current_fix
        return {
            "connected":  self.connected,
            "port":       self._active_port or self.port,
            "baud":       self.baud,
            "fix":        fix,
            "dr_active":  self._self_dr_active,
        }

    def set_share_config(self, mode: str, nodes_str: str, interval: int, channels_str: str = ""):
        node_list = [n.strip() for n in nodes_str.split(",") if n.strip()] if nodes_str else []
        channel_list = []
        if channels_str:
            for raw in channels_str.split(","):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ch = int(raw)
                except ValueError:
                    continue
                if 0 <= ch <= 7 and ch not in channel_list:
                    channel_list.append(ch)
        with self._share_lock:
            self._share_mode     = "selected" if mode == "nodes" else mode
            self._share_nodes    = node_list
            self._share_channels = channel_list
            self._share_interval = max(10, int(interval))
        logger.info(f"GPS share: mode={mode} nodes={node_list} channels={channel_list} interval={interval}s")

    def get_share_config(self) -> dict:
        with self._share_lock:
            return {"mode": self._share_mode,
                    "nodes": list(self._share_nodes),
                    "channels": list(self._share_channels),
                    "interval": self._share_interval}

    def start(self):
        if not _HAS_SERIAL or not _HAS_PYNMEA2:
            logger.error("GPS requires pyserial and pynmea2")
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="gps-reader")
        self._thread.start()
        self._dr_thread = threading.Thread(target=self._self_dr_loop, daemon=True, name="gps-self-dr")
        self._dr_thread.start()
        if self.mesh_manager and hasattr(self.mesh_manager, "set_gps_provider"):
            self.mesh_manager.set_gps_provider(lambda: self.current_fix)
        logger.info(f"GPS node started: port={self.port} baud={self.baud}")

    def stop(self):
        self.running = False
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass

    # ── Serial lifecycle ────────────────────────────────────────────────────

    def _run(self):
        backoff = 5.0
        while self.running:
            try:
                excl = self._mesh_port_exclusions()
                port = (self.port if self.port != "AUTO"
                        else _scan_gps_port(exclude=excl, baud=self.baud))
                if not port:
                    logger.warning("GPS: no serial port found — retry in 10 s")
                    time.sleep(10)
                    continue

                i2c_target = _parse_i2c_port(port)
                if i2c_target:
                    bus, addr = i2c_target
                    self._ser = _I2CSerial(bus, addr, baud=self.baud, timeout=1)
                else:
                    self._ser = serial.Serial(port, self.baud, timeout=1)
                self._active_port = port
                self.connected = True
                self._self_dr_active = False
                backoff = 5.0
                self._cleanup_fallback()
                self._configure_ubx()
                self._emit_status()
                logger.info(f"GPS serial opened: {port} @ {self.baud}")
                self._read_loop()

            except serial.SerialException as e:
                logger.warning(f"GPS serial error: {e}")
            except Exception as e:
                logger.error(f"GPS node error: {e}", exc_info=True)
            finally:
                self.connected = False
                self._active_port = None
                if self._ser:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                self._emit_status()

                # Activate self-DR if we have a recent fix to extrapolate from
                with self._lock:
                    ref = self.current_fix or self._last_gnss_fix
                if ref and FixType.has_position(ref.get("fix_type", 0)):
                    elapsed = time.time() - ref.get("timestamp", 0)
                    if elapsed < _SELF_DR_TIMEOUT:
                        logger.info("GPS serial lost — activating self dead reckoning")
                        self._self_dr_active = True
                        self._self_dr_start  = time.time()

                if self.running:
                    time.sleep(min(backoff, 60.0))
                    backoff = min(backoff * 1.5, 60.0)

    def _mesh_port_exclusions(self):
        excl = []
        if os.path.exists("/dev/meshtastic"):
            excl.append("/dev/meshtastic")
        if self.mesh_manager:
            for attr in ("_active_port", "port"):
                p = getattr(self.mesh_manager, attr, None)
                if p and p not in ("AUTO", "DEMO", None) and os.path.exists(p):
                    excl.append(p)
        return excl

    def _configure_ubx(self):
        """Enable UBX output on USB port, then enable NAV-PVT, NAV-ATT, ESF-STATUS."""
        if not (self._ser and self._ser.is_open):
            return
        try:
            # First enable UBX protocol output on the USB port
            self._ser.write(_ubx_cfg_prt_usb())
            time.sleep(0.1)
            # Force navigation output to 1 Hz so a previously-flashed high-rate
            # config can't pump tens of fixes per second through the parser.
            self._ser.write(_ubx_cfg_rate(1000, 1, 1))
            time.sleep(0.05)
            # Then enable specific UBX messages (DDC + UART1 + USB)
            self._ser.write(_ubx_cfg_msg(_CLS_NAV, _ID_NAV_PVT, 1))
            time.sleep(0.05)
            self._ser.write(_ubx_cfg_msg(_CLS_NAV, _ID_NAV_ATT, 1))
            time.sleep(0.05)
            self._ser.write(_ubx_cfg_msg(_CLS_ESF, _ID_ESF_STATUS, 5))
            time.sleep(0.05)
            logger.info("UBX protocol configured on NEO-M8U (%s) @ 1 Hz",
                        self._active_port or "auto")
        except Exception as e:
            logger.warning(f"UBX cfg (non-fatal): {e}")

    # ── Read loop ───────────────────────────────────────────────────────────

    def _read_loop(self):
        """Byte-level state machine: separate NMEA sentences from UBX frames."""
        nmea_buf = bytearray()
        ubx_buf  = bytearray()
        # Running NMEA assembly state
        lat = lon = alt = speed = heading = sats = None
        fix_type = FixType.NONE

        # Track consecutive empty / error reads so a misbehaving USB-CDC
        # device (e.g. a Jetson USB port that keeps reporting "ready" but
        # returns no data) cannot peg a CPU core in a tight serial.read
        # loop.  After a short burst of empty reads we yield; after a
        # longer burst we break out and let the outer _run loop re-open
        # the port.
        empty_streak = 0
        while self.running and self._ser and self._ser.is_open:
            try:
                chunk = self._ser.read(512)
                if not chunk:
                    empty_streak += 1
                    if empty_streak > 100:
                        logger.warning("GPS read: %d consecutive empty reads — re-opening port", empty_streak)
                        try:
                            self._ser.close()
                        except Exception:
                            pass
                        break
                    if empty_streak > 5:
                        time.sleep(0.05)
                    continue
                empty_streak = 0
                i = 0
                while i < len(chunk):
                    b = chunk[i]

                    # ── UBX frame start ──────────────────────────────────
                    if (not nmea_buf and b == 0xB5 and
                            i + 1 < len(chunk) and chunk[i + 1] == 0x62):
                        # Read UBX from remainder of chunk + serial if needed
                        tail = bytearray(chunk[i:])
                        result = self._parse_ubx_frame(tail)
                        if result is not None:
                            consumed, ubx_fix = result
                            i += consumed
                            if ubx_fix:
                                lat, lon = ubx_fix["lat"], ubx_fix["lon"]
                                alt      = ubx_fix.get("alt")
                                speed    = ubx_fix.get("speed")
                                heading  = ubx_fix.get("heading")
                                sats     = ubx_fix.get("sats")
                                fix_type = ubx_fix.get("fix_type", FixType.NONE)
                                if FixType.has_position(fix_type):
                                    self._handle_fix(lat, lon, alt, speed, heading,
                                                     sats, fix_type,
                                                     ubx_fix.get("accuracy"))
                        else:
                            i += 1
                        continue

                    # ── NMEA sentence ────────────────────────────────────
                    if b == ord("$") or nmea_buf:
                        nmea_buf.append(b)
                        if b == ord("\n"):
                            line = nmea_buf.decode("ascii", errors="replace").strip()
                            nmea_buf.clear()
                            r = self._parse_nmea_line(line)
                            if r:
                                if r.get("lat") is not None: lat = r["lat"]
                                if r.get("lon") is not None: lon = r["lon"]
                                if r.get("alt") is not None: alt = r["alt"]
                                if r.get("speed") is not None: speed = r["speed"]
                                if r.get("heading") is not None: heading = r["heading"]
                                if r.get("sats") is not None: sats = r["sats"]
                                if r.get("fix_type") is not None: fix_type = r["fix_type"]
                                if lat is not None and lon is not None and FixType.has_position(fix_type):
                                    self._handle_fix(lat, lon, alt, speed, heading,
                                                     sats, fix_type, None)
                    i += 1

            except serial.SerialException as e:
                # pyserial raises this when the device reports it is ready
                # to read but returns no bytes (USB disconnect, controller
                # in a bad state, etc.).  is_open stays True after the
                # raise, so without breaking here we would spin a CPU core
                # on a dead FD.  Close and bail; the outer _run loop will
                # reopen after a backoff.
                logger.warning("GPS read: %s — closing port for reopen", e)
                try:
                    self._ser.close()
                except Exception:
                    pass
                break
            except Exception as e:
                logger.debug(f"GPS read: {e}")
                if not (self._ser and self._ser.is_open):
                    break
                time.sleep(0.01)

    # ── UBX frame parser ────────────────────────────────────────────────────

    def _parse_ubx_frame(self, buf: bytearray):
        """
        Try to parse a UBX frame from buf. May read more bytes from serial.
        Returns (bytes_consumed, fix_dict_or_None) or None on failure.
        """
        if len(buf) < 8:
            return None
        if buf[0] != 0xB5 or buf[1] != 0x62:
            return None

        msg_cls = buf[2]
        msg_id  = buf[3]
        payload_len = struct.unpack_from("<H", buf, 4)[0]
        total   = 6 + payload_len + 2

        # Read more bytes from serial if we don't have the full frame
        if len(buf) < total and self._ser and self._ser.is_open:
            extra = self._ser.read(total - len(buf))
            if extra:
                buf.extend(extra)

        if len(buf) < total:
            return None

        ck_a, ck_b = _ubx_cksum(buf[2:6 + payload_len])
        if ck_a != buf[6 + payload_len] or ck_b != buf[7 + payload_len]:
            return 1, None  # bad checksum, skip one byte

        payload  = bytes(buf[6:6 + payload_len])
        fix_dict = None

        if msg_cls == _CLS_NAV:
            if msg_id == _ID_NAV_PVT:
                fix_dict = self._decode_nav_pvt(payload)
            elif msg_id == _ID_NAV_ATT:
                self._decode_nav_att(payload)
        elif msg_cls == _CLS_ESF and msg_id == _ID_ESF_STATUS:
            self._decode_esf_status(payload)

        return total, fix_dict

    def _decode_nav_pvt(self, payload: bytes) -> Optional[dict]:
        """
        UBX-NAV-PVT (0x01 0x07), 92 bytes.
        Extracts fix type, lat/lon/alt, speed, heading, accuracy, sat count.
        """
        if len(payload) < 84:
            return None
        fix_raw  = payload[20]
        flags    = payload[21]
        num_sv   = payload[23]
        lon_raw  = struct.unpack_from("<i", payload, 24)[0]
        lat_raw  = struct.unpack_from("<i", payload, 28)[0]
        h_msl    = struct.unpack_from("<i", payload, 36)[0]
        h_acc    = struct.unpack_from("<I", payload, 40)[0]
        g_speed  = struct.unpack_from("<i", payload, 60)[0]
        head_mot = struct.unpack_from("<i", payload, 64)[0]

        fix_type = FixType._FROM_UBX.get(fix_raw, FixType.NONE)
        gnss_ok  = bool(flags & 0x01)  # gnssFixOK flag

        # If module says DR-only, accept it even without gnssFixOK
        if not gnss_ok and fix_type not in (FixType.MODULE_DR, FixType.GNSS_DR):
            return None

        return {
            "lat":      lat_raw * 1e-7,
            "lon":      lon_raw * 1e-7,
            "alt":      h_msl * 1e-3,                # mm → m
            "speed":    abs(g_speed) * 1e-3,          # mm/s → m/s
            "heading":  (head_mot * 1e-5) % 360.0,    # 1e-5 deg → deg
            "accuracy": h_acc * 1e-3,                 # mm → m
            "sats":     num_sv,
            "fix_type": fix_type,
        }

    def _decode_nav_att(self, payload: bytes):
        """UBX-NAV-ATT (0x01 0x05) — vehicle heading from internal IMU."""
        if len(payload) < 24:
            return
        head_veh = struct.unpack_from("<i", payload, 8)[0]
        self._vehicle_heading = (head_veh * 1e-5) % 360.0

    def _decode_esf_status(self, payload: bytes):
        """UBX-ESF-STATUS (0x10 0x10) — sensor fusion quality."""
        if len(payload) < 16:
            return
        fusion_mode = payload[12]
        # 0=init, 1=not fused, 2=suspended, 3=fused
        if fusion_mode < 3:
            logger.debug(f"NEO-M8U ESF fusion mode {fusion_mode} (not fully fused yet)")

    # ── NMEA parser ─────────────────────────────────────────────────────────

    def _parse_nmea_line(self, line: str) -> Optional[dict]:
        if not line.startswith("$"):
            return None
        try:
            msg = pynmea2.parse(line)
        except pynmea2.ParseError:
            return None

        r = {}

        if isinstance(msg, pynmea2.GGA):
            qual = int(msg.gps_qual) if msg.gps_qual else 0
            if qual > 0:
                r["lat"]      = float(msg.latitude)
                r["lon"]      = float(msg.longitude)
                r["alt"]      = float(msg.altitude) if msg.altitude else None
                r["sats"]     = int(msg.num_sats) if msg.num_sats else None
                r["fix_type"] = FixType._FROM_GGA.get(qual, FixType.FIX_3D)
            else:
                r["fix_type"] = FixType.NONE

        elif isinstance(msg, pynmea2.VTG):
            if msg.spd_over_grnd_kmph is not None:
                r["speed"]   = float(msg.spd_over_grnd_kmph) / 3.6   # km/h → m/s
            if msg.true_track is not None:
                r["heading"] = float(msg.true_track)

        elif isinstance(msg, pynmea2.RMC):
            if msg.status == "A":
                r["lat"] = float(msg.latitude)
                r["lon"] = float(msg.longitude)
                if msg.spd_over_grnd is not None:
                    r["speed"]   = float(msg.spd_over_grnd) * 0.514444  # kn → m/s
                if msg.true_course is not None:
                    r["heading"] = float(msg.true_course)
                # NMEA 4.1 mode indicator 'E' = dead reckoning
                try:
                    mode = msg.data[11] if hasattr(msg, "data") and len(msg.data) > 11 else None
                    r["fix_type"] = FixType.MODULE_DR if mode == "E" else FixType.FIX_3D
                except Exception:
                    r["fix_type"] = FixType.FIX_3D

        elif isinstance(msg, pynmea2.GSA):
            try:
                m = int(msg.mode_fix_type) if msg.mode_fix_type else 1
                if m == 1:
                    r["fix_type"] = FixType.NONE
                elif m == 2:
                    r["fix_type"] = FixType.FIX_2D
                else:
                    r["fix_type"] = FixType.FIX_3D
            except Exception:
                pass

        return r if r else None

    # ── Fix processing ──────────────────────────────────────────────────────

    def _handle_fix(self, lat, lon, alt, speed, heading, sats, fix_type, accuracy):
        now   = time.time()
        is_dr = FixType.is_dr(fix_type)

        # Rate-limit DR emits
        if is_dr and (now - self._last_dr_emit) < _MODULE_DR_INTERVAL:
            return

        # Deduplicate stationary fixes
        if not is_dr:
            key = (round(lat, 5), round(lon, 5))
            if key == self._last_key:
                return
            self._last_key = key

        # Carry last known UBX accuracy forward when NMEA doesn't provide one
        if accuracy is not None:
            self._last_accuracy = accuracy
        else:
            accuracy = self._last_accuracy

        # Kalman smooth
        smooth_lat, smooth_lon = self._kf.update(lat, lon, fix_type, t=time.monotonic())

        # Broadcast the Kalman-smoothed position so the over-the-air coordinates
        # match exactly what is stored in the DB and displayed on the map.
        # (Calling _maybe_broadcast with raw lat/lon before smoothing caused the
        # device to store raw coords, which then oscillated against smooth coords
        # whenever a packet was received and _process_node re-read device state.)
        self._maybe_broadcast(smooth_lat, smooth_lon, alt, now)

        # Use vehicle heading from UBX if motion heading unavailable
        if heading is None and self._vehicle_heading is not None:
            heading = self._vehicle_heading

        fix = {
            "node_id":      self._get_node_id(),
            "latitude":     smooth_lat,
            "longitude":    smooth_lon,
            "altitude":     alt,
            "speed":        speed,
            "heading":      heading,
            "sats_in_view": sats,
            "fix_qual":     fix_type,   # legacy name kept for backward compat
            "fix_type":     fix_type,
            "fix_label":    FixType.label(fix_type),
            "accuracy":     accuracy,
            "timestamp":    now,
        }

        with self._lock:
            self.current_fix = fix
            if FixType.is_gnss(fix_type):
                self._last_gnss_fix = fix
                self._self_dr_active = False  # good GNSS → stop self-DR

        if is_dr:
            self._last_dr_emit = now

        self._store_fix(fix)
        self._emit_fix(fix)
        if self._on_fix_cb:
            try:
                self._on_fix_cb(fix)
            except Exception:
                pass

        should_log = (
            self._last_log_fix_type != fix_type
            or (now - self._last_fix_log_ts) >= 60.0
        )
        if should_log:
            self._last_log_fix_type = fix_type
            self._last_fix_log_ts = now
            acc_str = f"{accuracy:.1f}m" if accuracy is not None else "None"
            spd_str = f"{speed:.3f}m/s" if speed is not None else "None"
            hdg_str = f"{heading:.2f}°" if heading is not None else "None"
            alt_str = f"{alt:.1f}m" if alt is not None else "None"
            logger.info(
                f"GPS {fix['fix_label']}: {smooth_lat:.6f},{smooth_lon:.6f} "
                f"alt={alt_str} spd={spd_str} hdg={hdg_str} sats={sats} acc={acc_str}"
            )

    # ── Self dead reckoning thread ──────────────────────────────────────────

    def _self_dr_loop(self):
        """
        Runs independently of the serial reader.  When the serial connection
        is gone and self-DR is active, extrapolates position every
        _SELF_DR_INTERVAL seconds until timeout.
        """
        while self.running:
            time.sleep(_SELF_DR_INTERVAL)

            if not self._self_dr_active or self.connected:
                if self.connected:
                    self._self_dr_active = False
                continue

            now = time.time()
            elapsed_dr = now - self._self_dr_start

            if elapsed_dr > _SELF_DR_TIMEOUT:
                logger.warning("Self-DR timeout (5 min) — position lost")
                self._self_dr_active = False
                if self.socketio:
                    try:
                        self.socketio.emit("gps_dr_lost",
                                           {"message": "Position lost: dead reckoning timed out"},
                                           namespace="/")
                    except Exception:
                        pass
                continue

            with self._lock:
                ref = self.current_fix or self._last_gnss_fix

            if not ref:
                continue

            elapsed_fix = now - ref.get("timestamp", now)
            speed   = ref.get("speed")   or 0.0
            heading = ref.get("heading") or 0.0
            ref_lat = ref["latitude"]
            ref_lon = ref["longitude"]

            # Extrapolate: project distance along heading
            dist_m  = speed * elapsed_fix
            hdg_rad = math.radians(heading)
            dlat = (dist_m * math.cos(hdg_rad)) / 111_111.0
            dlon = (dist_m * math.sin(hdg_rad)) / (
                111_111.0 * math.cos(math.radians(ref_lat)) or 1.0)

            # Kalman predict for smooth motion
            pred = self._kf.predict(_SELF_DR_INTERVAL)
            if pred:
                new_lat, new_lon = pred
            else:
                new_lat = ref_lat + dlat
                new_lon = ref_lon + dlon

            # Uncertainty grows at ~0.5 m/s unknown acceleration
            base_acc  = ref.get("accuracy") or 10.0
            est_acc   = base_acc + elapsed_fix * 0.5
            fix_type  = FixType.SELF_DR

            fix = {
                "node_id":      self._get_node_id(),
                "latitude":     new_lat,
                "longitude":    new_lon,
                "altitude":     ref.get("altitude"),
                "speed":        speed,
                "heading":      heading,
                "sats_in_view": 0,
                "fix_qual":     fix_type,
                "fix_type":     fix_type,
                "fix_label":    FixType.label(fix_type),
                "accuracy":     round(est_acc, 1),
                "timestamp":    now,
            }

            with self._lock:
                self.current_fix = fix

            self._maybe_broadcast(new_lat, new_lon, fix.get("altitude"), now)
            self._store_fix(fix)
            self._emit_fix(fix)
            if self._on_fix_cb:
                try:
                    self._on_fix_cb(fix)
                except Exception:
                    pass

            logger.debug(
                f"Self-DR: {new_lat:.6f},{new_lon:.6f} ±{est_acc:.0f}m "
                f"elapsed={elapsed_fix:.0f}s"
            )

    # ── Storage ─────────────────────────────────────────────────────────────

    def _get_node_id(self) -> str:
        # 1. Live mesh connection knows our real node number.
        if self.mesh_manager and self.mesh_manager.my_node_id is not None:
            nid = self.mesh_manager.my_node_id
            sid = "!" + format(nid, "08x") if isinstance(nid, int) else str(nid)
            _persist_self_id(sid)
            return sid
        # 2. Heltec radio present on USB — derive its canonical Meshtastic node
        # ID from its USB serial so the GPS reader populates the same row the
        # radio will use once mesh comes back.
        derived = _heltec_node_id_from_udev()
        if derived:
            _persist_self_id(derived)
            return derived
        # 3. Radio unplugged / mesh down, but we've resolved our id before:
        # reuse it so GPS fixes stay on the one "Atlas Control" node instead
        # of forking a separate "Base Station" placeholder.
        persisted = _persisted_self_id()
        if persisted:
            return persisted
        # 4. No identity available at all — legacy placeholder.
        return _FALLBACK_NODE_ID

    def _cleanup_fallback(self):
        if self._fallback_cleaned:
            return
        real = self._get_node_id()
        if real == _FALLBACK_NODE_ID:
            return
        if db.get_node(_FALLBACK_NODE_ID):
            db.delete_node(_FALLBACK_NODE_ID)
            if self.socketio:
                try:
                    self.socketio.emit("node_removed", {"node_id": _FALLBACK_NODE_ID},
                                       namespace="/")
                except Exception:
                    pass
        self._fallback_cleaned = True

    def _store_fix(self, fix: dict):
        # Strict time-based throttle: never write to SQLite faster than
        # _db_min_interval. The NEO-M8U emits multiple fixes per second
        # (UBX-NAV-PVT + NMEA) and any motion-bypass falls apart immediately
        # because a stationary GPS still drifts by several meters of noise.
        # The live map dot still updates at the native fix rate via
        # _emit_fix; only the SQLite persistence is rate-limited.
        now_ts = fix["timestamp"]
        if (now_ts - self._last_db_write_ts) < self._db_min_interval:
            return
        self._last_db_write_ts = now_ts

        lat = fix["latitude"]
        lon = fix["longitude"]
        node_id = fix.get("node_id") or self._get_node_id()
        self._cleanup_fallback()
        now = int(now_ts)
        if db.get_node(node_id):
            db.update_node_position(node_id, lat, lon, fix.get("altitude"), now)
        else:
            is_fallback = node_id == _FALLBACK_NODE_ID
            db.upsert_node({
                "node_id":   node_id,
                "long_name": "Base Station" if is_fallback else None,
                "short_name":"BASE"         if is_fallback else None,
                "hw_model":  "NEO-M8U"      if is_fallback else None,
                "role":      "BASE_STATION" if is_fallback else None,
                "latitude":  lat,
                "longitude": lon,
                "altitude":  fix.get("altitude"),
                "last_heard": now,
            })
        db.insert_position({"node_id": node_id, "timestamp": now,
                            "latitude": lat, "longitude": lon,
                            "altitude": fix.get("altitude"),
                            "speed": fix.get("speed"), "heading": fix.get("heading"),
                            "sats_in_view": fix.get("sats_in_view")})

    # ── Events ──────────────────────────────────────────────────────────────

    def _emit_fix(self, fix: dict):
        if not self.socketio:
            return
        try:
            self.socketio.emit("node_update", {
                "node_id":    fix.get("node_id"),
                "latitude":   fix["latitude"],
                "longitude":  fix["longitude"],
                "altitude":   fix.get("altitude"),
                "last_heard": int(fix["timestamp"]),
            }, namespace="/")
            self.socketio.emit("gps_update", fix, namespace="/")
            # position_update drives live trail updates on the map page
            self.socketio.emit("position_update", {
                "node_id":   fix.get("node_id"),
                "latitude":  fix["latitude"],
                "longitude": fix["longitude"],
                "altitude":  fix.get("altitude"),
                "timestamp": fix["timestamp"],
                "speed":     fix.get("speed"),
                "heading":   fix.get("heading"),
            }, namespace="/")
        except Exception:
            pass

    def _emit_status(self):
        if not self.socketio:
            return
        try:
            self.socketio.emit("gps_status", {
                "connected": self.connected,
                "port":      self._active_port or self.port,
            }, namespace="/")
        except Exception:
            pass

    # ── Mesh broadcasting ────────────────────────────────────────────────────

    def _maybe_broadcast(self, lat, lon, alt, now):
        with self._share_lock:
            mode, nodes, channels, interval, last_tx = (
                self._share_mode, list(self._share_nodes),
                list(self._share_channels),
                self._share_interval, self._last_broadcast)
        if mode == "off" or not self.mesh_manager:
            return
        if now - last_tx < interval:
            return
        sent = False
        if mode == "all":
            sent = self.mesh_manager.send_position(lat, lon, alt)
        elif mode == "selected":
            if channels:
                for ch in channels:
                    if self.mesh_manager.send_position(lat, lon, alt, channel_index=ch):
                        sent = True
            if nodes:
                for nid in nodes:
                    if self.mesh_manager.send_position(lat, lon, alt, destination_id=nid, channel_index=0):
                        sent = True
        elif mode == "nodes" and nodes:
            for nid in nodes:
                if self.mesh_manager.send_position(lat, lon, alt, destination_id=nid):
                    sent = True
        if sent:
            with self._share_lock:
                self._last_broadcast = now


# Backward-compatible alias
GpsManager = GpsNode
