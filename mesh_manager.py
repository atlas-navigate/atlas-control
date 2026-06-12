"""
Meshtastic Device Manager
Handles connection to Heltec V4. Uses /dev/meshtastic udev symlink when available,
falls back to meshtastic library auto-scan across all serial ports.
"""
import base64
import os
import time
import json
import threading
import logging
import importlib
from collections import deque

try:
    from google.protobuf.json_format import MessageToDict
except ImportError:
    MessageToDict = None

# Stable udev symlink — created by 99-atlas-devices.rules
_UDEV_SYMLINK = "/dev/meshtastic"

logger = logging.getLogger("mesh_manager")

_SMBUS_MODULE = None
_SMBUS_IMPORT_ERROR = None
for _name in ("smbus", "smbus2"):
    try:
        _SMBUS_MODULE = importlib.import_module(_name)
        break
    except Exception as exc:
        _SMBUS_IMPORT_ERROR = exc

try:
    import meshtastic
    import meshtastic.serial_interface
    import meshtastic.util
    from meshtastic.protobuf import channel_pb2, apponly_pb2, portnums_pb2, mesh_pb2
    from pubsub import pub
    HAS_MESHTASTIC = True
except ImportError:
    HAS_MESHTASTIC = False
    logger.warning("meshtastic library not found — running in DEMO mode")

import database as db


# ── 3x21700 lithium pack voltage → SOC lookup table ─────────────────────────
# The Waveshare UPS Power Module C on this Atlas build measures a 3-cell pack
# through an INA219.  Model SOC from per-cell open-circuit voltage, then scale
# to the full 3S pack.  Linear interpolation between dense breakpoints avoids
# the visible percentage "gaps" produced by a coarse stepped table.
_LIION_1S_OCV_CURVE = [
    (4.20, 100), (4.18,  99), (4.15,  97), (4.11,  95), (4.08,  92),
    (4.05,  90), (4.02,  87), (3.99,  84), (3.98,  82), (3.96,  80),
    (3.94,  77), (3.92,  74), (3.90,  71), (3.88,  68), (3.87,  65),
    (3.85,  62), (3.84,  59), (3.82,  56), (3.81,  53), (3.80,  50),
    (3.79,  47), (3.77,  43), (3.75,  39), (3.74,  35), (3.72,  31),
    (3.70,  27), (3.68,  23), (3.66,  19), (3.63,  15), (3.60,  12),
    (3.56,   9), (3.52,   6), (3.45,   3), (3.30,   0),
]

def _interp_curve(voltage_v: float, curve) -> int:
    if voltage_v >= curve[0][0]:
        return int(curve[0][1])
    if voltage_v <= curve[-1][0]:
        return int(curve[-1][1])
    for i in range(len(curve) - 1):
        v_hi, p_hi = curve[i]
        v_lo, p_lo = curve[i + 1]
        if v_lo <= voltage_v <= v_hi:
            t = (voltage_v - v_lo) / (v_hi - v_lo)
            return int(round(p_lo + t * (p_hi - p_lo)))
    return int(curve[-1][1])

def _lipo_3s_pct(voltage_v: float, current_ma: float = 0.0, status: str | None = None) -> int:
    """Estimate SOC for the Waveshare 3-cell lithium pack.

    The raw INA219 voltage is observed under charge or load rather than at rest.
    Apply a small bounded compensation toward open-circuit voltage, then map the
    per-cell voltage through a dense lithium-ion curve.  This keeps the display
    responsive without the abrupt jumps of a coarse lookup table or linear model.
    """
    status_l = (status or "").strip().lower()
    current_a = abs(float(current_ma or 0.0)) / 1000.0

    compensated_v = float(voltage_v)
    if "discharg" in status_l:
        compensated_v += min(0.18, current_a * 0.09)
    elif "charg" in status_l:
        compensated_v -= min(0.12, current_a * 0.06)

    cell_v = max(3.0, min(4.2, compensated_v / 3.0))
    pct = _interp_curve(cell_v, _LIION_1S_OCV_CURVE)

    # Near-full handling: allow 100% once the pack is effectively full, even if
    # the charger still reports an active top-off state with very low current.
    # Keep normal bulk charging below 100 so the display does not jump early.
    if compensated_v >= 12.56 and abs(current_ma) <= 180:
        return 100

    if "charg" in status_l or current_ma > 200:
        if compensated_v >= 12.54:
            return 99
        if compensated_v >= 12.45:
            return max(pct, 98)
        if compensated_v >= 12.36:
            return max(pct, 96)

    return max(0, min(99 if "charg" in status_l else 100, pct))

def _normalize_battery_status(
    status: str | None,
    current_ma: float | None = None,
    voltage_v: float | None = None,
) -> str:
    """Collapse raw power-source status strings into one canonical state.

    Linux power_supply often reports values such as "Not charging", while the
    UPS INA219 path infers status from current direction. Keep one stable label
    so the UI never shows contradictory combinations like charging/discharging.
    """
    status_l = (status or "").strip().lower().replace("_", " ").replace("-", " ")
    current_ma = float(current_ma or 0.0)
    voltage_v = float(voltage_v or 0.0)

    if "full" in status_l:
        return "Full"
    if "not charging" in status_l:
        if voltage_v >= 12.56 and abs(current_ma) <= 120:
            return "Full"
        if current_ma < -120:
            return "Discharging"
        if current_ma > 120:
            return "Charging"
        return "Idle"
    if "discharg" in status_l:
        return "Discharging"
    if "charg" in status_l:
        if current_ma < -120:
            return "Discharging"
        return "Charging"

    if current_ma > 120:
        return "Charging"
    if current_ma < -120:
        return "Discharging"
    if voltage_v >= 12.56 and abs(current_ma) <= 120:
        return "Full"
    return "Idle"

def _power_phase(status: str | None) -> str:
    status_l = _normalize_battery_status(status).lower()
    if "full" in status_l:
        return "full"
    if "discharg" in status_l:
        return "discharging"
    if "charg" in status_l:
        return "charging"
    return "idle"

def _battery_state(voltage_v: float | None, current_ma: float | None, status: str | None) -> dict:
    current_ma = float(current_ma or 0.0)
    voltage_v = float(voltage_v or 0.0)
    normalized_status = _normalize_battery_status(status, current_ma=current_ma, voltage_v=voltage_v)
    phase = _power_phase(normalized_status)

    if phase == "full":
        return {
            "battery_status": "Full",
            "battery_phase": "full",
            "battery_is_full": True,
            "battery_soc_note": "Full",
        }

    if phase == "charging":
        if voltage_v >= 12.56 and abs(current_ma) <= 180:
            return {
                "battery_status": "Full",
                "battery_phase": "full",
                "battery_is_full": True,
                "battery_soc_note": "Full",
            }
        if voltage_v >= 12.45:
            return {
                "battery_status": "Charging",
                "battery_phase": "topping_off",
                "battery_is_full": False,
                "battery_soc_note": "Charging, near full",
            }
        return {
            "battery_status": "Charging",
            "battery_phase": "charging",
            "battery_is_full": False,
            "battery_soc_note": "Charging",
        }

    if phase == "discharging":
        return {
            "battery_status": "Discharging",
            "battery_phase": "discharging",
            "battery_is_full": False,
            "battery_soc_note": "Discharging",
        }

    is_full = voltage_v >= 12.56 and abs(current_ma) <= 120
    return {
        "battery_status": "Full" if is_full else "Idle",
        "battery_phase": "full" if is_full else "idle",
        "battery_is_full": bool(is_full),
        "battery_soc_note": "Full" if is_full else "Idle",
    }

def _display_power_w(voltage_v: float | None, current_ma: float | None) -> float | None:
    """Compute displayed power from the same voltage/current shown in the UI."""
    if voltage_v is None or current_ma is None:
        return None
    try:
        return round(float(voltage_v) * (float(current_ma) / 1000.0), 3)
    except Exception:
        return None


class MeshManager:
    def __init__(self, port="AUTO", socketio=None):
        self.port = port          # configured value ("AUTO" or specific path)
        self._active_port = None  # resolved port after successful connection
        self.socketio = socketio
        self.interface = None
        self.connected = False
        self.connecting = False
        self.my_node_id = None
        self.my_node_info = {}
        self._reconnect_thread = None
        self._stop = threading.Event()
        self._last_positions = {}  # node_id -> (lat, lon) to dedup position inserts
        self._seen_packet_ids = deque(maxlen=500)  # in-memory dedup ring buffer
        self._rx_err_ts = 0.0
        self._rx_err_count = 0
        self._recent_dm_sends = {}
        self._host_power_history = {}
        self._host_power_cache = {"ts": 0.0, "data": {}}
        self._cached_owner_info = {}
        self._device_info_cache = {"ts": 0.0, "data": {}}
        self._get_gps_fix = None  # set by gps_node to provide current GPS fix

    def set_gps_provider(self, provider):
        """Register a callable that returns the current GPS fix dict (or None)."""
        self._get_gps_fix = provider

    def _read_host_power_status(self):
        """Read the Atlas host battery state.

        The Waveshare UPS INA219 is tried first because it provides complete
        data (voltage, current, power) that sysfs power_supply cannot.  Linux
        sysfs is kept as a fallback for generic setups where the INA219 is
        absent.
        """
        # Prefer the Waveshare UPS INA219 — complete V/I/W data
        ups_data = self._read_waveshare_ups_status()
        if ups_data:
            return ups_data

        # Fallback: Linux sysfs power_supply (voltage + percent only)
        base = "/sys/class/power_supply"
        try:
            names = sorted(os.listdir(base))
        except Exception:
            return {}

        def _read_text(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception:
                return None

        def _read_percent(dir_path):
            capacity = _read_text(os.path.join(dir_path, "capacity"))
            if capacity is None:
                return None
            try:
                value = int(float(capacity))
            except Exception:
                return None
            return max(0, min(100, value))

        def _read_volts(dir_path):
            for filename in ("voltage_now", "voltage_avg", "voltage_boot"):
                raw = _read_text(os.path.join(dir_path, filename))
                if raw is None:
                    continue
                try:
                    value = float(raw)
                except Exception:
                    continue
                # Sysfs voltage fields are typically reported in microvolts.
                if value > 1000:
                    value /= 1_000_000.0
                return round(value, 3)
            return None

        candidates = []
        for name in names:
            dir_path = os.path.join(base, name)
            typ = (_read_text(os.path.join(dir_path, "type")) or "").lower()
            pct = _read_percent(dir_path)
            if typ not in ("battery", "ups") and pct is None:
                continue
            candidates.append({
                "name": name,
                "type": typ or None,
                "battery_pct": pct,
                "battery_status": _read_text(os.path.join(dir_path, "status")),
                "battery_voltage": _read_volts(dir_path),
            })

        if not candidates:
            return {}

        preferred = next(
            (c for c in candidates if c["type"] == "battery" and c["battery_pct"] is not None),
            None,
        ) or next((c for c in candidates if c["battery_pct"] is not None), None) or candidates[0]

        return self._stabilize_host_power({
            "battery_pct": preferred.get("battery_pct"),
            "battery_status": _normalize_battery_status(
                preferred.get("battery_status"),
                voltage_v=preferred.get("battery_voltage"),
            ),
            "battery_voltage": preferred.get("battery_voltage"),
            "battery_source": preferred.get("name"),
        })

    def _read_waveshare_ups_status(self):
        """Read a Waveshare UPS module via INA219 when Linux power_supply is absent."""
        if _SMBUS_MODULE is None:
            return {}
        explicit_bus = self._env_int("ATLAS_UPS_I2C_BUS", None)
        explicit_addr = self._env_int("ATLAS_UPS_I2C_ADDR", None)
        candidates = []
        if explicit_bus is not None and explicit_addr is not None:
            if explicit_bus == 7 and explicit_addr == 0x41:
                explicit_kind = "waveshare-ups-c"
            elif explicit_bus == 1 and explicit_addr == 0x42:
                explicit_kind = "waveshare-generic"
            else:
                explicit_kind = "waveshare-generic"
            candidates.append({
                "name": "env",
                "bus": explicit_bus,
                "addr": explicit_addr,
                "kind": explicit_kind,
            })
        candidates.extend([
            {
                "name": "waveshare-ups-c",
                "bus": 7,
                "addr": 0x41,
                "kind": "waveshare-ups-c",
            },
            {
                "name": "waveshare-generic",
                "bus": 1,
                "addr": 0x42,
                "kind": "waveshare-generic",
            },
        ])

        seen = set()
        for candidate in candidates:
            key = (candidate["bus"], candidate["addr"], candidate["kind"])
            if key in seen:
                continue
            seen.add(key)
            data = self._read_ina219_status(candidate)
            if data:
                return self._stabilize_host_power(data)
        return {}

    def _read_ina219_status(self, candidate):
        bus_num = candidate["bus"]
        addr = candidate["addr"]
        kind = candidate["kind"]
        try:
            smbus = _SMBUS_MODULE.SMBus(bus_num)
        except Exception:
            return {}

        reg_config = 0x00
        reg_shunt_voltage = 0x01
        reg_bus_voltage = 0x02
        reg_power = 0x03
        reg_current = 0x04
        reg_calibration = 0x05

        if kind == "waveshare-ups-c":
            cal_value = 26868
            current_lsb_ma = 0.1524
            power_lsb_w = 0.003048
            # 0x0EEF: BRNG=16V | PG=/2 (80mV) | BADC=32-sample | SADC=32-sample | continuous
            # Matches the Waveshare reference driver (ina219.py set_calibration_16V_5A).
            # Previous value 0x1A6F used PG=/8 (320mV) — 4× worse current resolution at
            # the 2–5 A operating range of the UPS module.
            config_value = 0x0EEF
        else:
            cal_value = 4096
            current_lsb_ma = 0.1
            power_lsb_w = 0.002
            config_value = 0x3EEF
            pct_floor = 6.0
            pct_span = 2.4

        def _write_word(register, value):
            smbus.write_i2c_block_data(addr, register, [(value >> 8) & 0xFF, value & 0xFF])

        def _read_word(register):
            data = smbus.read_i2c_block_data(addr, register, 2)
            return (data[0] << 8) | data[1]

        def _read_signed(register):
            value = _read_word(register)
            if value > 0x7FFF:
                value -= 0x10000
            return value

        try:
            _write_word(reg_calibration, cal_value)
            _write_word(reg_config, config_value)
            # 32-sample averaging (0x0EEF) takes ~17 ms per conversion; wait
            # long enough for at least two full conversion cycles to settle.
            time.sleep(0.040)

            shunt_voltage_v = _read_signed(reg_shunt_voltage) * 0.00001
            bus_voltage_v = (_read_word(reg_bus_voltage) >> 3) * 0.004

            _write_word(reg_calibration, cal_value)
            current_ma = _read_signed(reg_current) * current_lsb_ma

            _write_word(reg_calibration, cal_value)
            power_w = _read_signed(reg_power) * power_lsb_w
        except Exception:
            try:
                smbus.close()
            except Exception:
                pass
            return {}

        try:
            smbus.close()
        except Exception:
            pass

        load_voltage_v = bus_voltage_v + shunt_voltage_v
        # Use battery-side voltage (before shunt drop) for SOC — matches what
        # the pack's own indicator reads.
        battery_v = load_voltage_v if kind == "waveshare-ups-c" else bus_voltage_v
        if kind == "waveshare-ups-c":
            battery_pct = _lipo_3s_pct(battery_v, current_ma=current_ma)
        else:
            battery_pct = round(max(0.0, min(100.0, ((bus_voltage_v - pct_floor) / pct_span) * 100.0)))
        if kind == "waveshare-ups-c":
            if current_ma > 80:
                battery_status = "Charging"
            elif current_ma < -80:
                battery_status = "Discharging"
            elif battery_v >= 12.56:
                battery_status = "Full"
            else:
                battery_status = "Idle"
        else:
            if current_ma > 50:
                battery_status = "Charging"
            elif current_ma < -50:
                battery_status = "Discharging"
            else:
                battery_status = "Idle"
        if kind == "waveshare-ups-c":
            battery_status = _normalize_battery_status(
                battery_status,
                current_ma=current_ma,
                voltage_v=battery_v,
            )
            battery_pct = _lipo_3s_pct(battery_v, current_ma=current_ma, status=battery_status)
        else:
            battery_status = _normalize_battery_status(
                battery_status,
                current_ma=current_ma,
                voltage_v=battery_v,
            )

        return {
            "battery_pct": int(battery_pct),
            "battery_status": battery_status,
            "battery_voltage": round(battery_v, 3),
            "battery_current_ma": round(current_ma, 1),
            "battery_power_w": _display_power_w(battery_v, current_ma),
            "battery_power_w_raw": round(power_w, 3),
            "battery_source": f"{candidate['name']}@i2c-{bus_num}:0x{addr:02x}",
            "battery_soc_model": "3s-21700-lithium-voltage",
            **_battery_state(battery_v, current_ma, battery_status),
        }

    def _stabilize_host_power(self, data):
        """Reject one-off UPS percentage spikes from noisy INA219 polls."""
        pct = data.get("battery_pct")
        source = data.get("battery_source")
        if pct is None or not source:
            return data

        if not str(source).startswith("waveshare-"):
            return data

        data = dict(data)
        raw_pct = int(pct)
        raw_voltage = data.get("battery_voltage")
        data["battery_pct_raw"] = raw_pct

        if raw_voltage is None:
            return data

        history_key = f"{source}:{_power_phase(data.get('battery_status'))}"
        history = self._host_power_history.setdefault(history_key, deque(maxlen=5))
        try:
            history.append(float(raw_voltage))
        except Exception:
            return data

        window = sorted(history)
        if not window:
            return data

        smoothed_voltage = window[len(window) // 2]
        data["battery_voltage_raw"] = round(float(raw_voltage), 3)
        data["battery_voltage"] = round(smoothed_voltage, 3)
        data["battery_pct"] = _lipo_3s_pct(
            smoothed_voltage,
            current_ma=float(data.get("battery_current_ma") or 0.0),
            status=data.get("battery_status"),
        )
        data["battery_power_w"] = _display_power_w(
            data.get("battery_voltage"),
            data.get("battery_current_ma"),
        )
        return data

    def _env_int(self, key, default):
        raw = os.environ.get(key)
        if raw in (None, ""):
            return default
        try:
            return int(str(raw), 0)
        except Exception:
            logger.warning("Invalid %s=%r; using default %r", key, raw, default)
            return default

    def _json_safe(self, value):
        """Convert Meshtastic/protobuf objects into plain JSON-safe values."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {str(k): self._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set, deque)):
            return [self._json_safe(v) for v in value]
        if MessageToDict is not None:
            try:
                return self._json_safe(
                    MessageToDict(
                        value,
                        preserving_proto_field_name=True,
                        always_print_fields_with_no_presence=False,
                    )
                )
            except Exception:
                pass
        if hasattr(value, "items"):
            try:
                return {str(k): self._json_safe(v) for k, v in value.items()}
            except Exception:
                pass
        if hasattr(value, "__dict__"):
            try:
                return {
                    str(k): self._json_safe(v)
                    for k, v in vars(value).items()
                    if not str(k).startswith("_")
                }
            except Exception:
                pass
        return str(value)

    # ------------------------------------------------------------------ connect
    def connect(self):
        if not HAS_MESHTASTIC:
            logger.info("DEMO mode — no real device")
            self._load_demo_data()
            return True

        self.connecting = True
        try:
            candidates = []
            symlink_target = None
            if self.port in ("AUTO", None):
                if os.path.exists(_UDEV_SYMLINK):
                    logger.info("Using udev symlink %s", _UDEV_SYMLINK)
                    candidates.append(_UDEV_SYMLINK)
                    try:
                        symlink_target = os.path.realpath(_UDEV_SYMLINK)
                    except OSError:
                        symlink_target = None
                # Fall back to Meshtastic's own auto-scan only when the
                # symlink isn't pointing at anything — otherwise auto-scan
                # will pick the same /dev/ttyACMx and burn another 30 s on
                # the same unresponsive radio.
                if symlink_target is None:
                    candidates.append(None)
            else:
                candidates.append(self.port)

            seen = set()
            last_error = "Meshtastic radio not found"
            for dev_path in candidates:
                key = dev_path or "__auto__"
                if key in seen:
                    continue
                seen.add(key)
                display = dev_path or "auto-scan"

                # Tear down any prior interface and release stale FDs the
                # meshtastic library may have leaked on a previous failed
                # attempt.
                self._teardown_interface()
                if dev_path:
                    self._release_stale_serial_fds(dev_path)

                iface = None
                try:
                    logger.info("Connecting to Meshtastic device (%s)...", display)
                    iface = meshtastic.serial_interface.SerialInterface(dev_path)
                    self.interface = iface

                    self._active_port = getattr(iface, "devPath", dev_path) or dev_path or self.port
                    self.connected = True

                    my_info = iface.getMyNodeInfo()
                    if my_info:
                        self.my_node_id = my_info.get("num")
                        self.my_node_info = my_info
                        self._cached_owner_info = self._owner_info_from_my_info(my_info)

                    for topic, handler in [
                        ("meshtastic.receive", self._on_receive),
                        ("meshtastic.connection.established", self._on_connection),
                        ("meshtastic.connection.lost", self._on_disconnect),
                    ]:
                        try:
                            pub.subscribe(handler, topic)
                        except Exception:
                            pass

                    self._sync_node_db()

                    logger.info("Connected! My node: %s on %s", self.my_node_id, self._active_port)
                    self._emit("connection_status", {"connected": True, "port": self._active_port})
                    return True

                except Exception as e:
                    last_error = str(e)
                    logger.error("Connection failed (%s): %s", display, e)
                    self._teardown_interface()
                    self.connected = False
                    self._active_port = None
                    if dev_path is not None and None in candidates and "__auto__" not in seen:
                        logger.info("Meshtastic symlink path failed; falling back to auto-scan")

            self._emit("connection_status", {"connected": False, "error": last_error})
            self._start_reconnect()
            return False
        finally:
            self.connecting = False

    def _teardown_interface(self):
        """Tear down self.interface, joining the reader thread and releasing
        the serial port.  Safe to call repeatedly and on a partial interface
        whose constructor raised."""
        iface = self.interface
        if iface is None:
            return
        self.interface = None
        try:
            iface._wantExit = True
        except Exception:
            pass
        try:
            iface.close()
        except Exception:
            pass
        # close() only releases the serial port via the reader-thread exit
        # path.  If the reader was never started (connectNow=False and
        # iface.connect() never reached) the stream stays open — close it
        # explicitly so the exclusive lock drops.
        stream = getattr(iface, "stream", None)
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
            try:
                iface.stream = None
            except Exception:
                pass

    @staticmethod
    def _release_stale_serial_fds(dev_path):
        """Close any FD in this process that still points at dev_path.

        The meshtastic library opens the serial port with exclusive=True
        inside SerialInterface.__init__ and does not always release it when
        a later step in the constructor raises.  The leaked FD then blocks
        every reconnect with 'Could not exclusively lock port' until the
        service restarts.  Closing the FD here forces any orphan reader
        thread to exit on its next read() and frees the lock."""
        try:
            targets = {dev_path}
            try:
                targets.add(os.path.realpath(dev_path))
            except OSError:
                pass
            fd_dir = "/proc/self/fd"
            for entry in os.listdir(fd_dir):
                try:
                    link = os.readlink(os.path.join(fd_dir, entry))
                except OSError:
                    continue
                if link in targets:
                    try:
                        os.close(int(entry))
                        logger.warning("Released stale serial FD %s -> %s", entry, link)
                    except (OSError, ValueError):
                        pass
        except Exception as e:
            logger.debug("_release_stale_serial_fds error: %s", e)

    def disconnect(self):
        self._stop.set()
        for topic, handler in [
            ("meshtastic.receive", self._on_receive),
            ("meshtastic.connection.established", self._on_connection),
            ("meshtastic.connection.lost", self._on_disconnect),
        ]:
            try:
                pub.unsubscribe(handler, topic)
            except Exception:
                pass
        if self.interface:
            try:
                self.interface.close()
            except Exception:
                pass
        self.interface = None
        self.connected = False
        self._emit("connection_status", {"connected": False})

    def reconnect(self):
        self._stop.clear()
        self._reconnect_thread = None
        return self.connect()

    def sync_gps_privacy(self):
        """Ensure the Heltec cannot broadcast position when sharing is off.

        Three separate mechanisms can cause the device to broadcast position:
          1. position_broadcast_secs  — periodic timer (must be 0)
          2. position_broadcast_smart_enabled — movement-triggered (must be False)
          3. fixed_position — a stored position in flash the device re-broadcasts
                              (must be cleared via removeFixedPosition admin command)

        All three are enforced here.  Called both when the user saves 'off' mode
        and on every reconnect so a device restart cannot re-enable broadcasting.
        """
        if not self.interface or not HAS_MESHTASTIC:
            return False
        try:
            settings = db.get_app_settings()
            if settings.get("gps_share_mode", "off") != "off":
                return True

            position = self.interface.localNode.localConfig.position

            # -- Disable periodic and smart position broadcasts ---------------
            updates = {}
            if int(getattr(position, "position_broadcast_secs", 0) or 0) != 0:
                updates["position_broadcast_secs"] = 0
            if bool(getattr(position, "position_broadcast_smart_enabled", False)):
                updates["position_broadcast_smart_enabled"] = False
            if bool(getattr(position, "fixed_position", False)):
                updates["fixed_position"] = False

            if updates:
                logger.info("GPS privacy: disabling radio position config: %s", updates)
                if not self.set_device_config("position", updates):
                    return False

            # -- Clear any stored fixed position from device flash ------------
            # removeFixedPosition() is idempotent; safe to call even if not set.
            try:
                self.interface.localNode.removeFixedPosition()
                logger.info("GPS privacy: fixed position cleared from device")
            except Exception as e:
                logger.warning("GPS privacy: removeFixedPosition non-fatal: %s", e)

            return True
        except Exception as e:
            logger.error("sync_gps_privacy error: %s", e)
            return False

    # ------------------------------------------------------------------ reconnect logic
    def _start_reconnect(self):
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        # Exponential backoff capped at 5 min.  Each connect attempt takes
        # 30 s when the radio is unreachable (meshtastic waitForConfig
        # timeout); hammering every 10 s wastes CPU and keeps a leaked
        # reader thread thrashing in the background.
        backoff = 10
        while not self._stop.is_set():
            if self._stop.wait(backoff):
                return
            if self.connected:
                return
            logger.info("Attempting reconnect (backoff was %ds)", backoff)
            if self.connect():
                return
            backoff = min(backoff * 2, 300)

    # ------------------------------------------------------------------ nodeDB sync
    def _sync_node_db(self):
        if not self.interface:
            return
        try:
            nodes = self.interface.nodes
            if nodes:
                for node_id, node in nodes.items():
                    self._process_node(node_id, node)
                logger.info("Synced %d nodes from device", len(nodes))
        except Exception as e:
            logger.error("Node sync error: %s", e)

    def _process_node(self, node_id_str, node):
        try:
            user = self._json_safe(node.get("user", {}))
            position = self._json_safe(node.get("position", {}))
            metrics = self._json_safe(node.get("deviceMetrics", {}))
            metadata = self._json_safe(node.get("metadata", {}))
            snr = node.get("snr", None)

            # Decode lat/lon — meshtastic-python may produce camelCase (latitudeI)
            # or proto snake_case (latitude_i) depending on library version and
            # whether the position dict came through MessageToDict.
            def _decode_coord(pos, float_key, camel_key, snake_key):
                v = pos.get(float_key)
                if v:
                    return float(v)
                raw = pos.get(camel_key) or pos.get(snake_key)
                return raw / 1e7 if raw else None

            lat = _decode_coord(position, "latitude",  "latitudeI",  "latitude_i")
            lon = _decode_coord(position, "longitude", "longitudeI", "longitude_i")

            # For our own node, GPS hardware is the authoritative position source.
            # If an active GPS fix is available, suppress the device-stored coordinates
            # so _process_node never overwrites the Kalman-smoothed GPS position.
            my_str = None
            if self.my_node_id is not None:
                my_str = ("!" + format(self.my_node_id, "08x")
                          if isinstance(self.my_node_id, int)
                          else str(self.my_node_id))
            alt = position.get("altitude")
            if my_str and node_id_str == my_str and self._get_gps_fix:
                gps_fix = self._get_gps_fix()
                if gps_fix is not None:
                    lat = None
                    lon = None
                    alt = None

            data = {
                "node_id": node_id_str,
                "long_name": user.get("longName", "Unknown"),
                "short_name": user.get("shortName", "???"),
                "hw_model": user.get("hwModel", "UNKNOWN"),
                "mac_addr": user.get("macaddr", ""),
                "snr": snr,
                "rssi": node.get("rssi"),
                "last_heard": node.get("lastHeard", 0),
                "latitude": lat,
                "longitude": lon,
                "altitude": alt,
                "battery_level": metrics.get("batteryLevel"),
                "voltage": metrics.get("voltage"),
                "channel_util": metrics.get("channelUtilization"),
                "air_util_tx": metrics.get("airUtilTx"),
                "uptime": metrics.get("uptimeSeconds"),
                "role": user.get("role", "CLIENT"),
                "user": user,
                "position": position,
                "deviceMetrics": metrics,
                "metadata": metadata,
                "public_key_present": bool(user.get("publicKey")),
                "has_pkc": bool((metadata or {}).get("hasPKC")),
            }
            db.upsert_node(data)
            # Strip null GPS fields from the emit — the DB preserves existing
            # coordinates via CASE logic, but the frontend's spread merge does not.
            emit_data = {k: v for k, v in data.items()
                         if not (k in ('latitude', 'longitude', 'altitude') and v is None)}
            self._emit("node_update", emit_data)

            if lat and lon:
                last = self._last_positions.get(node_id_str)
                if last != (round(lat, 6), round(lon, 6)):
                    self._last_positions[node_id_str] = (round(lat, 6), round(lon, 6))
                    pos_data = {
                        "node_id": node_id_str,
                        "latitude": lat,
                        "longitude": lon,
                        "altitude": position.get("altitude"),
                        "speed": position.get("groundSpeed"),
                        "heading": position.get("groundTrack"),
                        "sats_in_view": position.get("satsInView"),
                        "timestamp": node.get("lastHeard", int(time.time())),
                    }
                    db.insert_position(pos_data)
                    self._emit("position_update", pos_data)
                    logger.debug("GPS fix (local): %s lat=%.5f lon=%.5f", node_id_str, lat, lon)

            self._check_node_alerts(data)

        except Exception as e:
            logger.error("Process node error: %s", e)

    # ------------------------------------------------------------------ packet handler
    def _on_receive(self, packet, interface=None):
        try:
            decoded = packet.get("decoded", {})
            portnum = decoded.get("portnum", "")
            from_id = str(packet.get("fromId", packet.get("from", "")))
            to_id = str(packet.get("toId", packet.get("to", "")))

            snr = packet.get("rxSnr")
            rssi = packet.get("rxRssi")
            if from_id and snr is not None:
                db.upsert_link(from_id, str(self.my_node_id or "local"), snr, rssi)
                self._emit("topology_update", {
                    "from": from_id,
                    "to": str(self.my_node_id or "local"),
                    "snr": snr,
                    "rssi": rssi,
                })

            if portnum == "TEXT_MESSAGE_APP":
                self._handle_text(packet, decoded, from_id, to_id)
            elif portnum == "POSITION_APP":
                self._handle_position(packet, decoded, from_id)
            elif portnum == "TELEMETRY_APP":
                self._handle_telemetry(packet, decoded, from_id)
            elif portnum == "NODEINFO_APP":
                self._handle_nodeinfo(packet, decoded, from_id)
            elif portnum == "NEIGHBORINFO_APP":
                self._handle_neighbor(packet, decoded, from_id)

            found = False
            if self.interface and self.interface.nodes:
                for nid, n in self.interface.nodes.items():
                    # nid is "!xxxxxxxx" hex format; from_id is also "!xxxxxxxx"
                    # n.get("num") is an integer — build the hex id for comparison
                    n_num = n.get("num")
                    n_hex_id = ("!" + format(n_num, "08x")) if isinstance(n_num, int) else None
                    if nid == from_id or (n_hex_id and n_hex_id == from_id):
                        self._process_node(nid, n)
                        found = True
                        break
            if not found and from_id and portnum != "NODEINFO_APP":
                existing = db.get_node(from_id) or {}
                node_data = {
                    "node_id": from_id,
                    "long_name": existing.get("long_name") or "Unknown",
                    "short_name": existing.get("short_name") or "???",
                    "hw_model": existing.get("hw_model") or "UNKNOWN",
                    "last_heard": packet.get("rxTime", int(time.time())),
                    "snr": snr,
                    "rssi": rssi,
                }
                # Pull position and metrics from DB so a node first seen via a
                # position packet gets coordinates in its first node_update emit.
                for _field in ("latitude", "longitude", "altitude", "battery_level", "voltage", "role"):
                    _val = existing.get(_field)
                    if _val is not None:
                        node_data[_field] = _val
                db.upsert_node(node_data)
                self._emit("node_update", node_data)

        except Exception as e:
            now_t = time.time()
            self._rx_err_count += 1
            if now_t - self._rx_err_ts >= 60:
                logger.error(
                    "Receive error (x%d in last 60s): %s",
                    self._rx_err_count, e, exc_info=True
                )
                self._rx_err_ts = now_t
                self._rx_err_count = 0

    def _handle_text(self, packet, decoded, from_id, to_id):
        packet_id = packet.get("id")
        if packet_id is not None:
            if packet_id in self._seen_packet_ids:
                logger.debug("Dropping duplicate packet_id=%s from %s", packet_id, from_id)
                return
            self._seen_packet_ids.append(packet_id)

        text = decoded.get("text", "")
        msg = {
            "from_id": from_id,
            "to_id": to_id,
            "channel": packet.get("channel", 0),
            "text": text,
            "rx_time": packet.get("rxTime", int(time.time())),
            "rx_snr": packet.get("rxSnr"),
            "rx_rssi": packet.get("rxRssi"),
            "hop_limit": packet.get("hopLimit"),
            "hop_start": packet.get("hopStart"),
            "packet_id": packet_id,
            "is_direct": int(bool(to_id and to_id != "^all")),
        }
        db.insert_message(msg)

        node = db.get_node(from_id)
        name = node["long_name"] if node else from_id
        db.insert_alert({
            "alert_type": "message",
            "severity": "info",
            "node_id": from_id,
            "title": f"Message from {name}",
            "message": text[:200]
        })

        self._emit("new_message", msg)
        self._emit("new_alert", {"type": "message", "from": name, "text": text[:100]})

    def _handle_position(self, packet, decoded, from_id):
        pos = decoded.get("position", decoded)
        # Accept both camelCase (latitudeI) and proto snake_case (latitude_i) keys.
        def _coord(float_key, camel_key, snake_key):
            v = pos.get(float_key)
            if v:
                return float(v)
            raw = pos.get(camel_key) or pos.get(snake_key)
            return raw / 1e7 if raw else None
        lat = _coord("latitude",  "latitudeI",  "latitude_i")
        lon = _coord("longitude", "longitudeI", "longitude_i")
        data = {
            "node_id": from_id,
            "latitude": lat,
            "longitude": lon,
            "altitude": pos.get("altitude"),
            "speed": pos.get("groundSpeed"),
            "heading": pos.get("groundTrack"),
            "sats_in_view": pos.get("satsInView"),
            "timestamp": packet.get("rxTime", int(time.time()))
        }
        if lat and lon:
            # Deduplicate against _last_positions so we don't insert a duplicate row
            # when _process_node() runs immediately after in _on_receive() for the same
            # packet — that call reads the same coordinates from interface.nodes and would
            # otherwise insert a second row for an unchanged position.
            coord = (round(lat, 6), round(lon, 6))
            if self._last_positions.get(from_id) != coord:
                self._last_positions[from_id] = coord
                db.insert_position(data)
            db.upsert_node({
                "node_id": from_id,
                "latitude": lat,
                "longitude": lon,
                "altitude": pos.get("altitude"),
                "last_heard": packet.get("rxTime", int(time.time()))
            })
            self._emit("position_update", data)
            logger.debug("GPS fix: %s lat=%.5f lon=%.5f alt=%sm sats=%s", from_id, lat, lon, data.get("altitude"), data.get("sats_in_view"))

    def _handle_telemetry(self, packet, decoded, from_id):
        tel = decoded.get("telemetry", decoded)
        device = tel.get("deviceMetrics", {})
        env = tel.get("environmentMetrics", {})
        power = tel.get("powerMetrics", {})
        data = {
            "node_id": from_id,
            "timestamp": packet.get("rxTime", int(time.time())),
            "battery_level": device.get("batteryLevel"),
            "voltage": device.get("voltage"),
            "channel_util": device.get("channelUtilization"),
            "air_util_tx": device.get("airUtilTx"),
            "uptime": device.get("uptimeSeconds"),
            "temperature": env.get("temperature"),
            "relative_humidity": env.get("relativeHumidity"),
            "barometric_pressure": env.get("barometricPressure"),
            "current": power.get("ch1Current"),
        }
        db.insert_telemetry(data)

        # Push live metrics into the node record so popups always show current
        # battery/channel data instead of stale values from the last nodeinfo packet.
        node_fields = {k: v for k, v in {
            "node_id":      from_id,
            "last_heard":   data["timestamp"],
            "battery_level": data["battery_level"],
            "voltage":      data["voltage"],
            "channel_util": data["channel_util"],
            "air_util_tx":  data["air_util_tx"],
            "uptime":       data["uptime"],
        }.items() if v is not None}
        db.upsert_node(node_fields)
        self._emit("node_update", node_fields)

        self._emit("telemetry_update", data)

        bl = data.get("battery_level")
        if bl is not None and bl < 20:
            node = db.get_node(from_id)
            name = node["long_name"] if node else from_id
            severity = "critical" if bl < 10 else "warning"
            db.insert_alert({
                "alert_type": "battery",
                "severity": severity,
                "node_id": from_id,
                "title": f"Low battery: {name}",
                "message": f"Battery at {bl}% ({data.get('voltage', '?')}V)"
            })
            self._emit("new_alert", {
                "type": "battery",
                "severity": severity,
                "node": name,
                "level": bl
            })

    def _handle_nodeinfo(self, packet, decoded, from_id):
        user = self._json_safe(decoded.get("user", {}))
        data = {
            "node_id": from_id,
            "long_name": user.get("longName", "Unknown"),
            "short_name": user.get("shortName", "???"),
            "hw_model": user.get("hwModel", "UNKNOWN"),
            "mac_addr": user.get("macaddr", ""),
            "last_heard": packet.get("rxTime", int(time.time())),
            "snr": packet.get("rxSnr"),
            "rssi": packet.get("rxRssi"),
            "role": user.get("role", "CLIENT"),
            "user": user,
            "public_key_present": bool(user.get("publicKey")),
        }
        db.upsert_node(data)
        self._emit("node_update", data)

    def _handle_neighbor(self, packet, decoded, from_id):
        neighbors = self._json_safe(decoded.get("neighbors", []))
        for n in neighbors:
            neighbor_id = str(n.get("nodeId", ""))
            if neighbor_id:
                db.upsert_link(from_id, neighbor_id, n.get("snr"), n.get("rssi"))
                self._emit("topology_update", {
                    "from": from_id,
                    "to": neighbor_id,
                    "snr": n.get("snr"),
                    "rssi": n.get("rssi"),
                })

    def _check_node_alerts(self, data):
        if data.get("last_heard"):
            age = int(time.time()) - data["last_heard"]
            if 1800 < age < 1830:
                db.insert_alert({
                    "alert_type": "offline",
                    "severity": "warning",
                    "node_id": data["node_id"],
                    "title": f"Node offline: {data.get('long_name', 'Unknown')}",
                    "message": f"Last heard {age // 60} minutes ago"
                })

    # ------------------------------------------------------------------ send message
    def send_message(self, text, destination=None, channel=0):
        if not HAS_MESHTASTIC or not self.interface:
            logger.warning("Cannot send — not connected")
            return {"ok": False, "error": "Meshtastic radio is not connected"}
        try:
            if destination and destination != "^all":
                self.interface.sendText(text, destinationId=destination, channelIndex=int(channel or 0))
            else:
                self.interface.sendText(text, channelIndex=int(channel or 0))

            msg = {
                "from_id": self._node_id_str(),
                "to_id": destination or "^all",
                "channel": int(channel or 0),
                "text": text,
                "rx_time": int(time.time()),
                "is_direct": int(bool(destination and destination != "^all")),
            }
            db.insert_message(msg)
            self._emit("new_message", msg)
            if destination and destination != "^all":
                self._recent_dm_sends[str(destination)] = {
                    "ok": True,
                    "status": "sent",
                    "transport": "direct",
                    "channel": int(channel or 0),
                    "updated_at": int(time.time()),
                }
            return {"ok": True, "transport": "direct" if destination and destination != "^all" else "channel", "channel": int(channel or 0)}
        except Exception as e:
            logger.error("Send failed: %s", e)
            if destination:
                self._recent_dm_sends[str(destination)] = {
                    "ok": False,
                    "status": "failed",
                    "error": str(e),
                    "updated_at": int(time.time()),
                }
            return {"ok": False, "error": str(e)}

    def send_position(self, lat, lon, alt=None, destination_id=None, channel_index=0):
        if not HAS_MESHTASTIC or not self.interface:
            logger.debug("Cannot send position — not connected")
            return False
        try:
            kwargs = dict(
                latitude=float(lat),
                longitude=float(lon),
                altitude=int(alt) if alt is not None else 0,
                wantAck=False,
            )
            if destination_id is not None:
                kwargs["destinationId"] = destination_id
            if channel_index is not None:
                kwargs["channelIndex"] = int(channel_index or 0)
            self.interface.sendPosition(**kwargs)
            dest = destination_id or "broadcast"
            logger.info("Position sent → %s: lat=%.6f lon=%.6f", dest, lat, lon)
            return True
        except Exception as e:
            logger.error("sendPosition failed: %s", e)
            return False

    def _node_id_str(self):
        if self.my_node_id is None:
            return "local"
        if isinstance(self.my_node_id, int):
            return "!" + format(self.my_node_id, "08x")
        return str(self.my_node_id)

    def _owner_info_from_my_info(self, my_info):
        user = (my_info or {}).get("user", {}) or {}
        return {
            "long_name": user.get("longName", ""),
            "short_name": user.get("shortName", ""),
            "node_id": self._node_id_str(),
            "hw_model": user.get("hwModel", ""),
        }

    def _owner_info_fallback(self):
        result = dict(self._cached_owner_info or {})
        node_id = self._node_id_str()
        if node_id and node_id != "local":
            result.setdefault("node_id", node_id)
        try:
            if node_id and node_id != "local":
                row = db.get_db().execute(
                    "SELECT long_name, short_name, hw_model FROM nodes WHERE node_id=?",
                    (node_id,),
                ).fetchone()
                if row:
                    if row["long_name"]:
                        result["long_name"] = row["long_name"]
                    if row["short_name"]:
                        result["short_name"] = row["short_name"]
                    if row["hw_model"]:
                        result["hw_model"] = row["hw_model"]
        except Exception:
            pass
        return result

    def _get_host_power_cached(self, ttl=5.0):
        now = time.time()
        cached = self._host_power_cache
        if now - cached.get("ts", 0.0) < ttl and cached.get("data"):
            return dict(cached["data"])
        try:
            data = self._read_host_power_status()
        except Exception as e:
            logger.debug("Host power read failed: %s", e)
            data = {}
        if data:
            self._host_power_cache = {"ts": now, "data": dict(data)}
            return dict(data)
        return dict(cached.get("data") or {})

    def get_device_info(self):
        now = time.time()
        cached = self._device_info_cache
        if now - cached.get("ts", 0.0) < 2.0 and cached.get("data"):
            return dict(cached["data"])

        info = {
            "connected": self.connected,
            "connecting": self.connecting,
            "port": self._active_port or self.port,
            "my_node_id": self._node_id_str() if self.my_node_id else None,
            "has_meshtastic": HAS_MESHTASTIC,
        }
        if self.connected and self.interface and hasattr(self.interface, "myInfo"):
            mi = self.interface.myInfo
            if mi:
                info["firmware"] = getattr(mi, "firmware_version", "unknown")
        owner = self._owner_info_fallback()
        info["long_name"] = owner.get("long_name", "")
        info["short_name"] = owner.get("short_name", "")
        info.update(self._get_host_power_cached())
        self._device_info_cache = {"ts": now, "data": dict(info)}
        return info

    # ------------------------------------------------------------------ connection callbacks
    def _on_connection(self, interface, topic=pub.AUTO_TOPIC):
        self.connected = True
        self.connecting = False
        logger.info("Connection established")
        self._emit("connection_status", {"connected": True})
        self._sync_node_db()
        # Re-enforce privacy settings — device restart reloads flash config
        # which may re-enable position broadcasting.
        threading.Thread(target=self.sync_gps_privacy, daemon=True, name="gps-privacy-sync").start()

    def _on_disconnect(self, interface, topic=pub.AUTO_TOPIC):
        self.connected = False
        self.connecting = False
        logger.warning("Connection lost")
        self._emit("connection_status", {"connected": False})
        db.insert_alert({
            "alert_type": "connection",
            "severity": "critical",
            "node_id": "local",
            "title": "Device disconnected",
            "message": f"Lost connection to {self._active_port or self.port}"
        })
        self._start_reconnect()

    # ------------------------------------------------------------------ SocketIO emit
    def _emit(self, event, data):
        if self.socketio:
            try:
                self.socketio.emit(event, data, namespace="/")
            except Exception:
                pass

    # ------------------------------------------------------------------ device config
    def get_device_config(self):
        if not self.interface or not HAS_MESHTASTIC:
            return {"connected": False}
        try:
            lc = self.interface.localNode.localConfig
            mc = getattr(self.interface.localNode, "moduleConfig", None)
            result = {
                "connected": True,
                "device": {
                    "role": int(lc.device.role),
                    "serial_enabled": bool(lc.device.serial_enabled),
                    "rebroadcast_mode": int(lc.device.rebroadcast_mode),
                    "led_heartbeat_disabled": bool(lc.device.led_heartbeat_disabled),
                    "node_info_broadcast_secs": int(lc.device.node_info_broadcast_secs),
                    "double_tap_as_button_press": bool(getattr(lc.device, "double_tap_as_button_press", False)),
                    "is_managed": bool(getattr(lc.device, "is_managed", False)),
                    "disable_triple_click": bool(getattr(lc.device, "disable_triple_click", False)),
                    "tzdef": str(getattr(lc.device, "tzdef", "") or ""),
                    "button_gpio": int(getattr(lc.device, "button_gpio", 0)),
                    "buzzer_gpio": int(getattr(lc.device, "buzzer_gpio", 0)),
                },
                "lora": {
                    "use_preset": bool(lc.lora.use_preset),
                    "modem_preset": int(lc.lora.modem_preset),
                    "region": int(lc.lora.region),
                    "bandwidth": int(lc.lora.bandwidth),
                    "spread_factor": int(lc.lora.spread_factor),
                    "coding_rate": int(lc.lora.coding_rate),
                    "hop_limit": int(lc.lora.hop_limit),
                    "tx_enabled": bool(lc.lora.tx_enabled),
                    "tx_power": int(lc.lora.tx_power),
                    "frequency_offset": int(lc.lora.frequency_offset),
                    "sx126x_rx_boosted_gain": bool(getattr(lc.lora, "sx126x_rx_boosted_gain", False)),
                    "override_frequency": float(getattr(lc.lora, "override_frequency", 0.0)),
                    "channel_num": int(getattr(lc.lora, "channel_num", 0)),
                    "ignore_mqtt": bool(getattr(lc.lora, "ignore_mqtt", False)),
                },
                "position": {
                    "gps_update_interval": int(lc.position.gps_update_interval),
                    "position_broadcast_secs": int(lc.position.position_broadcast_secs),
                    "fixed_position": bool(lc.position.fixed_position),
                    "position_broadcast_smart_enabled": bool(lc.position.position_broadcast_smart_enabled),
                    "gps_enabled": bool(getattr(lc.position, "gps_enabled", True)),
                    "broadcast_smart_minimum_distance": int(getattr(lc.position, "broadcast_smart_minimum_distance", 0)),
                    "broadcast_smart_minimum_interval_secs": int(getattr(lc.position, "broadcast_smart_minimum_interval_secs", 0)),
                    "gps_attempt_time": int(getattr(lc.position, "gps_attempt_time", 0)),
                    "position_flags": int(getattr(lc.position, "position_flags", 0)),
                },
                "power": {
                    "is_power_saving": bool(lc.power.is_power_saving),
                    "on_battery_shutdown_after_secs": int(lc.power.on_battery_shutdown_after_secs),
                    "wait_bluetooth_secs": int(lc.power.wait_bluetooth_secs),
                    "min_wake_secs": int(getattr(lc.power, "min_wake_secs", 0)),
                    "ls_secs": int(getattr(lc.power, "ls_secs", 0)),
                    "sds_secs": int(getattr(lc.power, "sds_secs", 0)),
                    "adc_multiplier_override": float(getattr(lc.power, "adc_multiplier_override", 0.0)),
                },
                "bluetooth": {
                    "enabled": bool(lc.bluetooth.enabled),
                    "mode": int(lc.bluetooth.mode),
                    "fixed_pin": int(lc.bluetooth.fixed_pin),
                },
                "network": {
                    "wifi_enabled": bool(lc.network.wifi_enabled),
                    "wifi_ssid": str(lc.network.wifi_ssid or ""),
                    "wifi_psk": str(lc.network.wifi_psk or ""),
                    "ntp_server": str(lc.network.ntp_server or ""),
                    "eth_enabled": bool(getattr(lc.network, "eth_enabled", False)),
                    "address_mode": int(lc.network.address_mode),
                    "rsyslog_server": str(getattr(lc.network, "rsyslog_server", "") or ""),
                },
            }

            # Display config (screen behaviour, units, etc.)
            try:
                d = lc.display
                result["display"] = {
                    "screen_on_secs": int(d.screen_on_secs),
                    "auto_screen_carousel_secs": int(getattr(d, "auto_screen_carousel_secs", 0)),
                    "compass_north_top": bool(getattr(d, "compass_north_top", False)),
                    "flip_screen": bool(getattr(d, "flip_screen", False)),
                    "units": int(getattr(d, "units", 0)),
                    "displaymode": int(getattr(d, "displaymode", 0)),
                    "heading_bold": bool(getattr(d, "heading_bold", False)),
                    "wake_on_tap_or_motion": bool(getattr(d, "wake_on_tap_or_motion", False)),
                    "gps_format": int(getattr(d, "gps_format", 0)),
                    "oled": int(getattr(d, "oled", 0)),
                }
            except Exception as e:
                logger.debug("display config unavailable: %s", e)

            # Security config (excluding raw key material)
            try:
                s = lc.security
                result["security"] = {
                    "is_managed": bool(getattr(s, "is_managed", False)),
                    "serial_enabled": bool(getattr(s, "serial_enabled", True)),
                    "debug_log_api_enabled": bool(getattr(s, "debug_log_api_enabled", False)),
                    "admin_channel_enabled": bool(getattr(s, "admin_channel_enabled", False)),
                }
            except Exception as e:
                logger.debug("security config unavailable: %s", e)

            # Module configs (telemetry, neighbor info, mqtt, etc.)
            if mc is not None:
                modules = {}
                try:
                    t = mc.telemetry
                    modules["telemetry"] = {
                        "device_update_interval": int(getattr(t, "device_update_interval", 0)),
                        "environment_update_interval": int(getattr(t, "environment_update_interval", 0)),
                        "environment_measurement_enabled": bool(getattr(t, "environment_measurement_enabled", False)),
                        "environment_screen_enabled": bool(getattr(t, "environment_screen_enabled", False)),
                        "air_quality_enabled": bool(getattr(t, "air_quality_enabled", False)),
                        "air_quality_interval": int(getattr(t, "air_quality_interval", 0)),
                        "power_measurement_enabled": bool(getattr(t, "power_measurement_enabled", False)),
                        "power_update_interval": int(getattr(t, "power_update_interval", 0)),
                        "power_screen_enabled": bool(getattr(t, "power_screen_enabled", False)),
                    }
                except Exception:
                    pass
                try:
                    n = mc.neighbor_info
                    modules["neighbor_info"] = {
                        "enabled": bool(n.enabled),
                        "update_interval": int(getattr(n, "update_interval", 0)),
                        "transmit_over_lora": bool(getattr(n, "transmit_over_lora", False)),
                    }
                except Exception:
                    pass
                try:
                    q = mc.mqtt
                    modules["mqtt"] = {
                        "enabled": bool(q.enabled),
                        "address": str(getattr(q, "address", "") or ""),
                        "username": str(getattr(q, "username", "") or ""),
                        "encryption_enabled": bool(getattr(q, "encryption_enabled", False)),
                        "json_enabled": bool(getattr(q, "json_enabled", False)),
                        "tls_enabled": bool(getattr(q, "tls_enabled", False)),
                        "proxy_to_client_enabled": bool(getattr(q, "proxy_to_client_enabled", False)),
                        "map_reporting_enabled": bool(getattr(q, "map_reporting_enabled", False)),
                    }
                except Exception:
                    pass
                try:
                    r = mc.range_test
                    modules["range_test"] = {
                        "enabled": bool(r.enabled),
                        "sender": int(getattr(r, "sender", 0)),
                        "save": bool(getattr(r, "save", False)),
                    }
                except Exception:
                    pass
                try:
                    sf = mc.store_forward
                    modules["store_forward"] = {
                        "enabled": bool(sf.enabled),
                        "heartbeat": bool(getattr(sf, "heartbeat", False)),
                        "records": int(getattr(sf, "records", 0)),
                        "history_return_max": int(getattr(sf, "history_return_max", 0)),
                        "history_return_window": int(getattr(sf, "history_return_window", 0)),
                    }
                except Exception:
                    pass
                try:
                    sr = mc.serial
                    modules["serial"] = {
                        "enabled": bool(sr.enabled),
                        "echo": bool(getattr(sr, "echo", False)),
                        "baud": int(getattr(sr, "baud", 0)),
                        "timeout": int(getattr(sr, "timeout", 0)),
                        "mode": int(getattr(sr, "mode", 0)),
                    }
                except Exception:
                    pass
                try:
                    en = mc.external_notification
                    modules["external_notification"] = {
                        "enabled": bool(en.enabled),
                        "output_ms": int(getattr(en, "output_ms", 0)),
                        "output": int(getattr(en, "output", 0)),
                        "active": bool(getattr(en, "active", False)),
                        "alert_message": bool(getattr(en, "alert_message", False)),
                        "alert_message_buzzer": bool(getattr(en, "alert_message_buzzer", False)),
                        "alert_message_vibra": bool(getattr(en, "alert_message_vibra", False)),
                        "alert_bell": bool(getattr(en, "alert_bell", False)),
                        "use_pwm": bool(getattr(en, "use_pwm", False)),
                    }
                except Exception:
                    pass
                if modules:
                    result["module_config"] = modules
            return result
        except Exception as e:
            logger.error("get_device_config error: %s", e)
            return {"connected": self.connected, "error": str(e)}

    def set_device_config(self, section, values):
        if not self.interface or not HAS_MESHTASTIC:
            return False
        try:
            node = self.interface.localNode
            if node is None or getattr(node, "localConfig", None) is None:
                logger.warning("set_device_config: localConfig not loaded yet")
                return False
            if not hasattr(node.localConfig, section):
                logger.warning("set_device_config: unknown section '%s'", section)
                return False

            section_obj = getattr(node.localConfig, section)
            for key, val in values.items():
                if not hasattr(section_obj, key):
                    continue
                current = getattr(section_obj, key)
                if isinstance(current, bool):
                    setattr(section_obj, key, bool(val))
                elif isinstance(current, int):
                    setattr(section_obj, key, int(val))
                elif isinstance(current, float):
                    setattr(section_obj, key, float(val))
                else:
                    setattr(section_obj, key, val)

            # Wrap the write in a settings transaction so the radio actually
            # persists the change to flash and reboots if needed.  Without the
            # transaction, firmware ≥ 2.5 drops bare `writeConfig` admin
            # messages because no admin session key has been established
            # (`ensureSessionKey` is called inside beginSettingsTransaction,
            # never inside writeConfig itself) — so the change shows up in our
            # in-memory localConfig but never reaches the Heltec.
            node.beginSettingsTransaction()
            try:
                node.writeConfig(section)
            finally:
                node.commitSettingsTransaction()

            # Give the admin message time to transmit before we return.
            # The radio reboots for LoRa/region/preset changes; the existing
            # _on_disconnect → _start_reconnect path handles the link drop.
            time.sleep(0.4)
            logger.info("Device config [%s] updated: %s", section, values)
            return True
        except Exception as e:
            logger.error("set_device_config error: %s", e)
            return False

    def get_owner_info(self):
        if not self.interface or not self.connected or self.connecting:
            return self._owner_info_fallback()
        try:
            my_info = self.interface.getMyNodeInfo() or {}
            result = self._owner_info_from_my_info(my_info)
            self._cached_owner_info = dict(result)
            fallback = self._owner_info_fallback()
            if fallback.get("long_name"):
                result["long_name"] = fallback["long_name"]
            if fallback.get("short_name"):
                result["short_name"] = fallback["short_name"]
            if fallback.get("hw_model"):
                result["hw_model"] = fallback["hw_model"]
            return result
        except Exception as e:
            logger.error("get_owner_info error: %s", e)
            return self._owner_info_fallback()

    def set_owner(self, long_name, short_name):
        if not self.interface:
            return False
        try:
            self.interface.localNode.setOwner(long_name, short_name)
            logger.info("Owner updated: %s / %s", long_name, short_name)
            node_id = self._node_id_str()
            nodes = getattr(self.interface, "nodes", {}) or {}
            if node_id in nodes and isinstance(nodes.get(node_id), dict):
                nodes[node_id].setdefault("user", {})["longName"] = long_name
                nodes[node_id]["user"]["shortName"] = short_name
            return True
        except Exception as e:
            logger.error("set_owner error: %s", e)
            return False

    def wipe_nodedb(self):
        """Tell the radio to clear its NodeDB and drop the in-memory mirror.

        Without this, the SQL `nodes` wipe done by the factory-reset flow
        is immediately re-populated by `_sync_node_db()` reading from
        `interface.nodes` (the Python-side mirror) or by the radio sending
        back its persisted nodedb on the next reconnect.

        Returns a dict describing what was cleared.
        """
        result = {"radio_reset": False, "in_memory_cleared": 0, "error": None}
        if not self.interface or not HAS_MESHTASTIC:
            result["error"] = "no interface"
            return result
        local_id = self._node_id_str()
        local_num = self.my_node_id if isinstance(self.my_node_id, int) else None

        # 1) Tell the radio to drop its NodeDB. Wrap in a transaction so the
        #    admin message goes out with a session key (same pattern as
        #    set_device_config).
        try:
            node = self.interface.localNode
            node.beginSettingsTransaction()
            try:
                node.resetNodeDb()
            finally:
                node.commitSettingsTransaction()
            time.sleep(0.4)
            result["radio_reset"] = True
            logger.info("Radio NodeDB reset issued")
        except Exception as e:
            logger.error("wipe_nodedb: resetNodeDb failed: %s", e)
            result["error"] = str(e)

        # 2) Clear the Python-side mirror so _sync_node_db doesn't replay
        #    stale entries. Keep the local-node record so we don't lose our
        #    own identity in the dict.
        try:
            nodes = getattr(self.interface, "nodes", None)
            if isinstance(nodes, dict):
                keep = {}
                if local_id in nodes:
                    keep[local_id] = nodes[local_id]
                cleared = len(nodes) - len(keep)
                nodes.clear()
                nodes.update(keep)
                result["in_memory_cleared"] = max(cleared, 0)
            nodes_by_num = getattr(self.interface, "nodesByNum", None)
            if isinstance(nodes_by_num, dict):
                keep = {}
                if local_num is not None and local_num in nodes_by_num:
                    keep[local_num] = nodes_by_num[local_num]
                nodes_by_num.clear()
                nodes_by_num.update(keep)
        except Exception as e:
            logger.warning("wipe_nodedb: in-memory mirror clear failed: %s", e)

        return result

    def default_short_name(self):
        """Return Meshtastic's auto-default short_name for this radio.

        Firmware derives it from the last 4 hex chars of the node ID
        (e.g. node !aabbccdd → short_name "ccdd").
        """
        nid = self._node_id_str()
        if not nid or nid == "local":
            return "ATLS"
        h = nid.lstrip("!")
        return h[-4:] if len(h) >= 4 else h

    def apply_device_config_snapshot(self, snapshot):
        """Re-write a previously captured device-config snapshot to the radio.

        `snapshot` is the dict returned by `get_device_config()`. The
        `connected` key (if present) is ignored. Each section is written via
        `set_device_config()` — failures are recorded and returned so the
        caller can decide whether to surface them.
        """
        if not snapshot:
            return {"ok": False, "error": "empty snapshot"}
        results = {}
        # Order matters: write radio-link sections (lora, region) last so the
        # reboot they trigger doesn't drop earlier writes.
        order = ["device", "position", "power", "bluetooth", "network", "lora"]
        for section in order:
            values = snapshot.get(section)
            if not isinstance(values, dict) or not values:
                continue
            ok = self.set_device_config(section, values)
            results[section] = ok
        results["ok"] = all(v for k, v in results.items() if k != "ok")
        return results

    # ------------------------------------------------------------------ compatibility helpers
    def get_interface_nodes(self):
        if not self.interface or not self.interface.nodes:
            return {}
        result = {}
        for nid, node in self.interface.nodes.items():
            user = node.get("user", {}) or {}
            result[str(nid)] = {
                "num": node.get("num"),
                "longName": user.get("longName"),
                "shortName": user.get("shortName"),
                "hwModel": user.get("hwModel"),
                "publicKeyPresent": bool(user.get("publicKey")),
                "isUnmessagable": bool(user.get("isUnmessagable", False)),
                "lastHeard": node.get("lastHeard"),
                "snr": node.get("snr"),
            }
        return result

    def get_dm_diagnostics(self, node_id):
        node = db.get_node(node_id)
        if not node:
            return None
        normalized = node["node_id"]
        configured = [int(ch.get("channel_num")) for ch in self.get_channels() if ch.get("channel_num") is not None]
        last_broadcast = db.get_last_broadcast_for_node(normalized) or {}
        return {
            "node_id": normalized,
            "public_key_present": bool(node.get("public_key_present")),
            "node_has_pkc": bool(node.get("has_pkc")),
            "secure_direct_ready": False,
            "preferred_channel": 0,
            "configured_channels": configured,
            "last_broadcast_channel": last_broadcast.get("channel"),
            "last_broadcast_rx_time": last_broadcast.get("rx_time"),
            "recent_send": self._recent_dm_sends.get(normalized),
        }

    def request_nodeinfo(self, node_id=None, channel=0):
        if not HAS_MESHTASTIC or not self.interface:
            return {"ok": False, "error": "Not connected"}
        dest_id = "^all"
        if node_id:
            dest_id = str(node_id)
        try:
            self.interface.sendData(
                b"",
                destinationId=dest_id,
                portNum=portnums_pb2.PortNum.NODEINFO_APP,
                wantAck=False,
                wantResponse=True,
                channelIndex=int(channel or 0),
            )
            return {"ok": True, "destination": dest_id, "channel": int(channel or 0)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def scan_mesh(self):
        """Broadcast NodeInfo + Position requests to the full mesh.

        Sends two packets:
          1. NODEINFO_APP broadcast (want_response=True) — each node that receives
             it replies with its own User/NodeInfo.  The local node's own User info
             is included as the payload so firmware can identify the requester;
             some builds silently drop NODEINFO requests with an empty payload.
          2. POSITION_APP broadcast (want_response=True) — nodes with GPS reply
             with their current position so the map updates immediately.

        Immediately syncs interface.nodes into SQLite so nodes already known to
        the Meshtastic library (from the initial connection) but not yet in the
        local DB are discovered without waiting for a reconnect event.

        Results arrive incrementally via node_update / position_update socket
        events as responses come in.
        """
        if not HAS_MESHTASTIC or not self.interface:
            return {"ok": False, "error": "Not connected"}
        try:
            # Build our own User proto as the payload so firmware knows who is asking.
            # Nodes that receive a NODEINFO broadcast with want_response=True will
            # reply with their own NodeInfo; including our User info is required by
            # some firmware builds and improves reliability across all versions.
            payload = b""
            try:
                my_num = getattr(self.interface.myInfo, "my_node_num", None)
                if my_num is not None:
                    my_id = "!" + format(my_num, "08x")
                    my_data = (self.interface.nodes or {}).get(my_id, {})
                    u = my_data.get("user", {})
                    local_user = mesh_pb2.User()
                    local_user.id = str(u.get("id") or my_id)
                    if u.get("longName"):
                        local_user.long_name = str(u["longName"])
                    if u.get("shortName"):
                        local_user.short_name = str(u["shortName"])
                    payload = local_user.SerializeToString()
            except Exception as e:
                logger.debug("Could not build User payload for scan (using empty): %s", e)

            # 1. NodeInfo broadcast
            self.interface.sendData(
                payload,
                destinationId="^all",
                portNum=portnums_pb2.PortNum.NODEINFO_APP,
                wantAck=False,
                wantResponse=True,
            )

            # 2. Position broadcast — pulls GPS coordinates from all responding nodes
            self.interface.sendData(
                b"",
                destinationId="^all",
                portNum=portnums_pb2.PortNum.POSITION_APP,
                wantAck=False,
                wantResponse=True,
            )

            # Sync interface.nodes immediately so nodes the library already knows
            # about (from initial connection) but aren't in our DB get picked up
            # without waiting for the next organic connection event.
            threading.Thread(target=self._sync_node_db, daemon=True,
                             name="scan-sync").start()

            return {"ok": True}
        except Exception as exc:
            logger.error("scan_mesh failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def get_channels(self):
        fallback = db.get_channels()
        if not self.interface or not getattr(self.interface, "localNode", None):
            return fallback
        try:
            local_channels = getattr(self.interface.localNode, "channels", None) or []
            aliases = {int(ch["channel_num"]): ch["name"] for ch in fallback}
            merged = []
            for ch in local_channels:
                role_name = channel_pb2.Channel.Role.Name(ch.role)
                if role_name == "DISABLED":
                    continue
                name = (getattr(ch.settings, "name", "") or "").strip()
                if not name:
                    name = aliases.get(int(ch.index), "Primary" if role_name == "PRIMARY" else f"Channel {int(ch.index)}")
                merged.append({
                    "channel_num": int(ch.index),
                    "name": name,
                    "role": role_name.lower(),
                    "is_primary": role_name == "PRIMARY",
                    "psk_state": meshtastic.util.pskToString(getattr(ch.settings, "psk", b"")),
                })
            if merged:
                return merged
        except Exception as e:
            logger.warning("Unable to read live channel config: %s", e)
        return fallback

    def set_channel(self, channel_num, name, psk_text=None):
        channel_num = int(channel_num)
        if not (0 <= channel_num <= 7):
            raise ValueError("Channel number must be between 0 and 7")
        node = self.interface.localNode
        ch = node.getChannelByChannelIndex(channel_num)
        if ch is None:
            raise RuntimeError(f"Channel slot {channel_num} is not available on this device")

        parsed_psk = self._parse_psk(psk_text)
        resolved_name = str(name or "").strip()
        if isinstance(parsed_psk, channel_pb2.ChannelSettings) and not resolved_name:
            resolved_name = str(getattr(parsed_psk, "name", "") or "").strip()
        if not resolved_name:
            raise ValueError("Channel name is required")
        role_name = channel_pb2.Channel.Role.Name(ch.role)
        current_psk = bytes(getattr(ch.settings, "psk", b"") or b"")
        if parsed_psk is None and (role_name == "DISABLED" or not current_psk):
            parsed_psk = self._parse_psk("random")

        ch.settings.name = resolved_name
        if isinstance(parsed_psk, channel_pb2.ChannelSettings):
            ch.settings.CopyFrom(parsed_psk)
            ch.settings.name = resolved_name or ch.settings.name
        elif parsed_psk is not None:
            ch.settings.psk = parsed_psk
        ch.settings.uplink_enabled = True
        ch.settings.downlink_enabled = True
        ch.role = channel_pb2.Channel.Role.PRIMARY if channel_num == 0 else channel_pb2.Channel.Role.SECONDARY

        # Wrap in a settings transaction so the firmware commits the new
        # channel definition to flash and reboots its LoRa stack with the new
        # PSK / role.  Without the wrapping commit, bare writeChannel admin
        # messages don't trigger the rekey on firmware ≥ 2.5.
        node.beginSettingsTransaction()
        try:
            node.writeChannel(channel_num)
        finally:
            node.commitSettingsTransaction()
        time.sleep(0.4)
        db.upsert_channel(channel_num, ch.settings.name or f"Channel {channel_num}")
        return True

    def delete_channel(self, channel_num):
        channel_num = int(channel_num)
        if channel_num == 0:
            raise ValueError("Primary channel cannot be removed")
        node = self.interface.localNode
        ch = node.getChannelByChannelIndex(channel_num)
        if ch is None:
            raise RuntimeError(f"Channel slot {channel_num} is not available on this device")
        ch.role = channel_pb2.Channel.Role.DISABLED
        ch.settings.name = ""
        ch.settings.psk = bytes()
        # Same transaction wrapping as set_channel — see comment there for
        # why bare writeChannel doesn't take effect on firmware ≥ 2.5.
        node.beginSettingsTransaction()
        try:
            node.writeChannel(channel_num)
        finally:
            node.commitSettingsTransaction()
        time.sleep(0.4)
        db.delete_channel(channel_num)
        return True

    def get_channel_share_url(self, channel_num, add_only=True):
        channel_num = int(channel_num)
        node = self.interface.localNode
        ch = node.getChannelByChannelIndex(channel_num)
        if ch is None:
            raise RuntimeError(f"Channel slot {channel_num} is not available on this device")
        role_name = channel_pb2.Channel.Role.Name(ch.role)
        if role_name == "DISABLED":
            raise ValueError(f"Channel {channel_num} is disabled")

        channel_set = apponly_pb2.ChannelSet()
        channel_set.settings.append(ch.settings)
        local_config = getattr(node, "localConfig", None)
        if local_config is not None:
            channel_set.lora_config.CopyFrom(local_config.lora)

        encoded = base64.urlsafe_b64encode(channel_set.SerializeToString()).decode("ascii").rstrip("=")
        suffix = f"/?add=true#{encoded}" if add_only else f"/#{encoded}"
        return f"https://meshtastic.org/e{suffix}"

    def _parse_psk(self, psk_text):
        if psk_text is None:
            return None
        psk_text = str(psk_text).strip()
        if not psk_text:
            return None
        parsed_channel = self._parse_channel_url(psk_text)
        if parsed_channel is not None:
            return parsed_channel
        parsed = meshtastic.util.fromPSK(psk_text)
        if isinstance(parsed, (bytes, bytearray)):
            return bytes(parsed)
        try:
            b64 = psk_text.replace("-", "+").replace("_", "/")
            missing = len(b64) % 4
            if missing:
                b64 += "=" * (4 - missing)
            return base64.b64decode(b64.encode("utf-8"), validate=True)
        except Exception as exc:
            raise ValueError(
                "Invalid key format. Use a Meshtastic channel URL, default, none, random, simpleN, 0x..., base64:..., or raw base64."
            ) from exc

    def _parse_channel_url(self, text):
        if "/#" not in text and "/?add=true#" not in text:
            return None
        split_url = text.split("/?add=true#") if "/?add=true#" in text else text.split("/#")
        if len(split_url) == 1:
            return None
        b64 = split_url[-1].strip()
        if not b64:
            raise ValueError("missing encoded channel data")
        missing = len(b64) % 4
        if missing:
            b64 += "=" * (4 - missing)
        decoded = base64.urlsafe_b64decode(b64.encode("utf-8"))
        channel_set = apponly_pb2.ChannelSet()
        channel_set.ParseFromString(decoded)
        if len(channel_set.settings) == 0:
            raise ValueError("URL did not contain any channel settings")
        return channel_set.settings[0]

    # ------------------------------------------------------------------ demo mode
    def _load_demo_data(self):
        import random
        now = int(time.time())

        demo_nodes = [
            {"node_id": "!a1b2c3d4", "long_name": "Base Station Alpha", "short_name": "BSA",
             "hw_model": "HELTEC_V3", "latitude": 38.9586, "longitude": -77.3570,
             "battery_level": 95, "voltage": 4.12, "snr": 10.5, "rssi": -65,
             "channel_util": 12.3, "air_util_tx": 3.2, "role": "ROUTER", "uptime": 86400},
            {"node_id": "!e5f6a7b8", "long_name": "Relay Node Bravo", "short_name": "RNB",
             "hw_model": "HELTEC_V3", "latitude": 38.9650, "longitude": -77.3450,
             "battery_level": 67, "voltage": 3.85, "snr": 7.25, "rssi": -78,
             "channel_util": 8.1, "air_util_tx": 2.1, "role": "ROUTER", "uptime": 43200},
            {"node_id": "!c9d0e1f2", "long_name": "Mobile Charlie", "short_name": "MCH",
             "hw_model": "TBEAM", "latitude": 38.9520, "longitude": -77.3680,
             "battery_level": 34, "voltage": 3.55, "snr": 3.0, "rssi": -92,
             "channel_util": 5.5, "air_util_tx": 1.8, "role": "CLIENT", "uptime": 7200},
            {"node_id": "!a3b4c5d6", "long_name": "Sensor Delta", "short_name": "SND",
             "hw_model": "HELTEC_V3", "latitude": 38.9700, "longitude": -77.3400,
             "battery_level": 12, "voltage": 3.28, "snr": -1.5, "rssi": -105,
             "channel_util": 2.0, "air_util_tx": 0.5, "role": "SENSOR", "uptime": 172800},
            {"node_id": "!e7f8a9b0", "long_name": "Gateway Echo", "short_name": "GWE",
             "hw_model": "STATION_G2", "latitude": 38.9480, "longitude": -77.3520,
             "battery_level": 100, "voltage": 5.05, "snr": 12.0, "rssi": -55,
             "channel_util": 18.7, "air_util_tx": 5.6, "role": "ROUTER", "uptime": 604800},
            {"node_id": "!f1a2b3c4", "long_name": "Field Fox", "short_name": "FFX",
             "hw_model": "RAK4631", "latitude": 38.9430, "longitude": -77.3750,
             "battery_level": 78, "voltage": 3.95, "snr": 5.5, "rssi": -82,
             "channel_util": 6.3, "air_util_tx": 1.9, "role": "CLIENT", "uptime": 14400},
        ]

        for n in demo_nodes:
            n["last_heard"] = now - random.randint(0, 600)
            n["mac_addr"] = n["node_id"][1:]
            db.upsert_node(n)

        messages = [
            ("!a1b2c3d4", "^all", "Mesh network check — all nodes report in"),
            ("!e5f6a7b8", "^all", "Bravo online, signal strong"),
            ("!c9d0e1f2", "^all", "Charlie here, moving to grid sector 7"),
            ("!a3b4c5d6", "^all", "Temp: 22.4°C Humidity: 65% Pressure: 1013hPa"),
            ("!e7f8a9b0", "^all", "Gateway uptime 7 days, all channels clear"),
            ("!f1a2b3c4", "!a1b2c3d4", "Fox to Base — requesting position update"),
            ("!a1b2c3d4", "!f1a2b3c4", "Base copies, sending coordinates now"),
            ("!c9d0e1f2", "^all", "Low battery warning — switching to power save"),
            ("!e5f6a7b8", "^all", "New node detected in sector 4"),
            ("!a1b2c3d4", "^all", "All stations: weather advisory in effect"),
        ]
        for i, (f, t, txt) in enumerate(messages):
            db.insert_message({
                "from_id": f,
                "to_id": t,
                "channel": 0,
                "text": txt,
                "rx_time": now - (len(messages) - i) * 180,
                "rx_snr": random.uniform(2, 12),
                "rx_rssi": random.randint(-100, -55),
                "hop_limit": random.randint(1, 3),
                "hop_start": 3,
                "is_direct": int(t != "^all"),
            })

        for n in demo_nodes:
            base_bat = n["battery_level"]
            for h in range(48):
                ts = now - (47 - h) * 1800
                drift = random.uniform(-2, 1)
                bat = max(5, min(100, base_bat + drift * h * 0.1))
                db.insert_telemetry({
                    "node_id": n["node_id"],
                    "timestamp": ts,
                    "battery_level": int(bat),
                    "voltage": round(3.0 + bat / 100 * 1.2, 2),
                    "channel_util": round(random.uniform(1, 25), 1),
                    "air_util_tx": round(random.uniform(0.5, 8), 1),
                    "uptime": n["uptime"] + h * 1800,
                    "temperature": round(20 + random.uniform(-3, 5), 1),
                    "relative_humidity": round(55 + random.uniform(-10, 15), 1),
                })

        for n in demo_nodes:
            if n.get("latitude"):
                for h in range(12):
                    db.insert_position({
                        "node_id": n["node_id"],
                        "timestamp": now - (11 - h) * 600,
                        "latitude": n["latitude"] + random.uniform(-0.002, 0.002),
                        "longitude": n["longitude"] + random.uniform(-0.002, 0.002),
                        "altitude": random.randint(50, 120),
                        "sats_in_view": random.randint(5, 14),
                    })

        links = [
            ("!a1b2c3d4", "!e5f6a7b8", 10.5, -65),
            ("!a1b2c3d4", "!e7f8a9b0", 12.0, -55),
            ("!e5f6a7b8", "!c9d0e1f2", 7.0, -78),
            ("!e5f6a7b8", "!a3b4c5d6", -1.5, -105),
            ("!e7f8a9b0", "!f1a2b3c4", 5.5, -82),
            ("!a1b2c3d4", "!f1a2b3c4", 4.0, -88),
            ("!c9d0e1f2", "!f1a2b3c4", 2.0, -95),
        ]
        for f, t, s, r in links:
            db.upsert_link(f, t, s, r)

        db.insert_alert({"alert_type": "battery", "severity": "critical",
            "node_id": "!a3b4c5d6", "title": "Critical battery: Sensor Delta",
            "message": "Battery at 12% (3.28V)"})
        db.insert_alert({"alert_type": "message", "severity": "info",
            "node_id": "!c9d0e1f2", "title": "Message from Mobile Charlie",
            "message": "Low battery warning — switching to power save"})
        db.insert_alert({"alert_type": "connection", "severity": "warning",
            "node_id": "!a3b4c5d6", "title": "Weak signal: Sensor Delta",
            "message": "SNR dropped below 0 dB"})

        logger.info("Demo data loaded")
        self.connected = True
        self.my_node_id = "!a1b2c3d4"
