"""
The Atlas button: a DFRobot Gravity I2C RGB LED button (DFR0991) on the
40-pin header.

It is the physical proof of ownership and the way back in:

  * press it while setting Atlas up, and the light shows a colour that the
    phone must pick (pairing.py, "button colour" proof);
  * hold it for 15 seconds, and the light turns red and Atlas is reset to
    factory settings (app.py, _run_factory_reset).

The light also says what Atlas is waiting for: dim white = not set up yet,
blinking blue = a new device is asking the primary user for access, off =
set up and idle.

Wiring: the button belongs on header pins 27 (D/SDA) and 28 (C/SCL), which
are /dev/i2c-1 at 100 kHz, with + on pin 1 (3.3 V) and - on pin 6. Pins 3/5
(/dev/i2c-7) run at 400 kHz, where the module answers a few transfers and
then stops responding until its power is cut (measured 2026-11-12); a
software reset cannot recover it.

Only the transfers verified on that hardware are used: a 2-byte block read of
the part id at 0x09, a 3-byte block write of R,G,B at 0x01 and a 1-byte block
read of the button state at 0x04.

The controller runs as a native thread inside atlas-control (threading is not
monkey-patched there), so I2C waits never stall the gevent hub. Anything that
touches pairing or the reset is handed back to the app through callbacks.

`python3 status_button.py --probe` reports where the button is (installer and
acceptance checks); `--flash` cycles its colours for a technician.
"""
import argparse
import importlib
import json
import logging
import os
import sys
import threading
import time

logger = logging.getLogger("status_button")

_SMBUS = None
for _name in ("smbus", "smbus2"):
    try:
        _SMBUS = importlib.import_module(_name)
        break
    except Exception:
        pass

PART_ID = 0x43DF
REG_RGB = 0x01
REG_PRESSED = 0x04
REG_PART_ID = 0x09

DEFAULT_BUS = 1
DEFAULT_ADDR = 0x2A
# Header buses, in the order the button is looked for. Bus 1 (pins 27/28) is
# where it works; bus 7 (pins 3/5) is checked only to tell a technician that
# it is plugged into the wrong pins.
HEADER_BUSES = (1, 7)
SLOW_BUSES = {1}

# Colours a phone may be asked to pick: clearly different on the module's LED
# and on a phone screen. Red and amber are not among them: they mean "reset".
# id -> (label, LED R,G,B, swatch colour for the app)
PAIR_COLORS = {
    "green":  ("Green",  (0x00, 0xC0, 0x00), "#22c55e"),
    "blue":   ("Blue",   (0x00, 0x00, 0xFF), "#3b82f6"),
    "purple": ("Purple", (0x90, 0x00, 0xFF), "#a855f7"),
    "yellow": ("Yellow", (0xFF, 0x90, 0x00), "#facc15"),
    "cyan":   ("Cyan",   (0x00, 0xC0, 0xC0), "#22d3ee"),
    "white":  ("White",  (0xC0, 0xC0, 0xC0), "#f1f5f9"),
}
OFF = (0, 0, 0)
RED = (0xFF, 0x00, 0x00)
AMBER = (0xFF, 0x30, 0x00)
DIM_WHITE = (0x18, 0x18, 0x18)
READY_WHITE = (0x80, 0x80, 0x80)
SUCCESS = PAIR_COLORS["green"][1]
APPROVAL = PAIR_COLORS["blue"][1]

POLL_S = 0.05
SHORT_PRESS_MAX_S = 2.0
HOLD_WARN_S = 3.0
RESET_HOLD_S = 15.0
SUCCESS_FLASH_S = 3.0
RESET_RED_S = 2.0          # solid red when the hold reaches 15 s
RESET_STARTED_S = 3.0      # then solid green: the reset has started (and while it runs)
REFUSED_FLASH_S = 2.0
FAILS_BEFORE_UNRESPONSIVE = 5
RETRY_S = 5.0
ABSENT_RETRY_S = 30.0
# The light is written again this often even when unchanged, so it recovers
# from anything else that set it (a technician's --flash).
REFRESH_S = 5.0


def palette():
    """The colour choices as the apps and the web page show them."""
    return [{"id": cid, "label": label, "swatch": swatch} for cid, (label, _rgb, swatch) in PAIR_COLORS.items()]


def configured():
    """(bus, addr) from ATLAS_BUTTON_I2C_BUS / ATLAS_BUTTON_I2C_ADDR."""
    def _int(name, default):
        try:
            return int(os.environ.get(name, ""), 0)
        except ValueError:
            return default
    return _int("ATLAS_BUTTON_I2C_BUS", DEFAULT_BUS), _int("ATLAS_BUTTON_I2C_ADDR", DEFAULT_ADDR)


class RGBButton:
    """One DFR0991 module. Every method raises OSError when the module does
    not answer; the caller decides what that means."""

    def __init__(self, bus, addr=DEFAULT_ADDR, smbus_module=None):
        self.bus_no, self.addr = int(bus), int(addr)
        self._smbus = smbus_module or _SMBUS
        self._bus = None

    def _open(self):
        if self._bus is None:
            if self._smbus is None:
                raise OSError("python3-smbus is not installed")
            self._bus = self._smbus.SMBus(self.bus_no)
        return self._bus

    def close(self):
        bus, self._bus = self._bus, None
        if bus is not None:
            try:
                bus.close()
            except Exception:
                pass

    def _io(self, fn):
        try:
            return fn(self._open())
        except OSError:
            self.close()           # reopen on the next transfer
            raise

    def part_id(self):
        hi, lo = self._io(lambda b: b.read_i2c_block_data(self.addr, REG_PART_ID, 2))[:2]
        return (hi << 8) | lo

    def set_rgb(self, rgb):
        self._io(lambda b: b.write_i2c_block_data(self.addr, REG_RGB, [int(v) & 0xFF for v in rgb]))

    def pressed(self):
        return self._io(lambda b: b.read_i2c_block_data(self.addr, REG_PRESSED, 1))[0] == 1


def detect(buses=None, addr=None, smbus_module=None):
    """Where the button is: {"bus", "addr", "part_id"} or None.

    The configured bus is tried first, then the other header buses."""
    want_bus, want_addr = configured()
    addr = want_addr if addr is None else addr
    order = [want_bus] + [b for b in (buses or HEADER_BUSES) if b != want_bus]
    for bus in order:
        if not os.path.exists(f"/dev/i2c-{bus}") and smbus_module is None:
            continue
        button = RGBButton(bus, addr, smbus_module)
        try:
            if button.part_id() == PART_ID:
                return {"bus": bus, "addr": addr, "part_id": PART_ID}
        except OSError:
            pass
        finally:
            button.close()
    return None


class ButtonController:
    """Polls the button, turns presses and holds into callbacks and keeps the
    light showing the most important thing Atlas is waiting for.

    state_fn() -> dict, read every tick (keep it cheap):
        unclaimed         bool  nobody has set Atlas up yet
        pair_color        str   colour id a phone must pick, or None
        press_window      bool  pressed, waiting for a phone to start
        approval_pending  bool  a device is asking the primary user
    on_press()           short press (released within SHORT_PRESS_MAX_S)
    on_reset() -> bool   15 s hold; False when the reset was refused
    """

    def __init__(self, state_fn, on_press, on_reset, smbus_module=None, clock=time.monotonic,
                 sleep=time.sleep, detect_fn=None):
        self._state_fn = state_fn
        self._on_press = on_press
        self._on_reset = on_reset
        self._smbus = smbus_module
        self._clock = clock
        self._sleep = sleep
        self._detect = detect_fn or (lambda: detect(smbus_module=smbus_module))
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._thread = None
        self.button = None
        self.status = {"state": "starting", "bus": None, "addr": None}
        self._fails = 0
        self._next_probe = 0.0
        self._down_since = None
        self._hold_fired = False
        self._resetting = False
        self._red_until = 0.0
        self._started_until = 0.0
        self._success_until = 0.0
        self._refused_until = 0.0
        self._shown = None
        self._shown_at = 0.0

    # ── public ──────────────────────────────────────────────────────────
    def snapshot(self):
        with self._lock:
            return dict(self.status)

    def flash_success(self):
        self._success_until = self._clock() + SUCCESS_FLASH_S

    def start(self):
        self._thread = threading.Thread(target=self.run, name="atlas-button", daemon=True)
        self._thread.start()

    def stop(self):
        self._halt.set()

    def run(self):
        while not self._halt.is_set():
            try:
                self.tick()
            except Exception as exc:          # never let the thread die
                logger.warning("Atlas button: %s", exc)
            self._sleep(POLL_S)

    # ── one poll ────────────────────────────────────────────────────────
    def tick(self):
        now = self._clock()
        if self.button is None:
            if now >= self._next_probe:
                self._attach(now)
            return
        try:
            pressed = self.button.pressed()
        except OSError:
            self._failed(now)
            return
        self._fails = 0
        self._handle_press(pressed, now)
        self._show(self._light(now), now)

    def _attach(self, now):
        found = self._detect()
        if not found:
            self._set_status("absent", None, None)
            self._next_probe = now + ABSENT_RETRY_S
            return
        self.button = RGBButton(found["bus"], found["addr"], self._smbus)
        self._fails = 0
        self._shown = None                     # rewrite the light: it lost power or never had one
        if found["bus"] not in SLOW_BUSES:
            logger.warning("Atlas button found on /dev/i2c-%s: move it to header pins 27/28 "
                           "(that bus runs at 400 kHz, where the button stops responding)", found["bus"])
            self._set_status("wrong_pins", found["bus"], found["addr"])
        else:
            logger.info("Atlas button on /dev/i2c-%s at 0x%02x", found["bus"], found["addr"])
            self._set_status("ok", found["bus"], found["addr"])

    def _failed(self, now):
        self._fails += 1
        if self._fails < FAILS_BEFORE_UNRESPONSIVE:
            return
        status = self.snapshot()
        if status["state"] != "not_responding":
            logger.warning("Atlas button stopped responding on /dev/i2c-%s; unplug it and plug it back in",
                           status["bus"])
        self._set_status("not_responding", status["bus"], status["addr"])
        if self.button is not None:
            self.button.close()
        self.button = None
        self._down_since = None
        self._next_probe = now + RETRY_S

    def _set_status(self, state, bus, addr):
        with self._lock:
            self.status = {"state": state, "bus": bus, "addr": addr}

    # ── presses ─────────────────────────────────────────────────────────
    def _handle_press(self, pressed, now):
        if pressed:
            if self._down_since is None:
                self._down_since = now
                self._hold_fired = False
            elif not self._hold_fired and now - self._down_since >= RESET_HOLD_S:
                self._hold_fired = True
                self._start_reset()
            return
        if self._down_since is None:
            return
        held, self._down_since = now - self._down_since, None
        if self._hold_fired:
            return
        if held < SHORT_PRESS_MAX_S:
            try:
                self._on_press()
            except Exception as exc:
                logger.warning("Atlas button press: %s", exc)

    def _start_reset(self):
        now = self._clock()
        self._resetting = True
        self._red_until = now + RESET_RED_S
        self._started_until = self._red_until + RESET_STARTED_S
        self._show(RED, now)
        logger.warning("Atlas button held for %d seconds: factory reset", RESET_HOLD_S)

        def _run():
            try:
                ok = self._on_reset()
            except Exception as exc:
                logger.error("Button factory reset failed: %s", exc)
                ok = False
            if ok is False:
                self._started_until = 0.0
                self._refused_until = max(self._clock(), self._red_until) + REFUSED_FLASH_S
            self._resetting = False
        threading.Thread(target=_run, name="atlas-button-reset", daemon=True).start()

    # ── the light ───────────────────────────────────────────────────────
    def _light(self, now):
        blink = int(now * 2) % 2 == 0          # 1 Hz
        if now < self._red_until:
            return RED
        if now < self._refused_until:
            return RED if int(now * 4) % 2 == 0 else OFF
        if self._resetting or now < self._started_until:
            return SUCCESS
        # Still held after the reset fired: never back to "hold in progress".
        if self._down_since is not None and not self._hold_fired and now - self._down_since >= HOLD_WARN_S:
            return AMBER if blink else OFF
        try:
            state = self._state_fn() or {}
        except Exception:
            state = {}
        color = state.get("pair_color")
        if color in PAIR_COLORS:
            return PAIR_COLORS[color][1]
        if now < self._success_until:
            return SUCCESS if int(now * 4) % 2 == 0 else OFF
        if state.get("press_window"):
            return READY_WHITE if blink else DIM_WHITE
        if state.get("approval_pending"):
            return APPROVAL if blink else OFF
        if state.get("unclaimed"):
            return DIM_WHITE
        return OFF

    def _show(self, rgb, now):
        if self.button is None or (rgb == self._shown and now - self._shown_at < REFRESH_S):
            return
        try:
            self.button.set_rgb(rgb)
            self._shown = rgb
            self._shown_at = now
        except OSError:
            self._shown = None


# ── command line (installer, acceptance, technicians) ───────────────────
def _probe():
    found = detect()
    if not found:
        print(json.dumps({"found": False}))
        return 2
    print(json.dumps(dict(found, found=True, part_id=f"0x{found['part_id']:04x}",
                          addr=f"0x{found['addr']:02x}", right_pins=found["bus"] in SLOW_BUSES)))
    return 0 if found["bus"] in SLOW_BUSES else 3


def _flash():
    found = detect()
    if not found:
        print("No Atlas button found on header buses", ", ".join(map(str, HEADER_BUSES)))
        return 2
    button = RGBButton(found["bus"], found["addr"])
    try:
        for cid, (label, rgb, _swatch) in PAIR_COLORS.items():
            print(label)
            button.set_rgb(rgb)
            time.sleep(1.2)
        print("Red (reset)")
        button.set_rgb(RED)
        time.sleep(1.2)
    finally:
        try:
            button.set_rgb(OFF)
        finally:
            button.close()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--probe", action="store_true",
                       help="print where the button is; exit 0 = OK, 2 = missing, 3 = wrong pins")
    group.add_argument("--flash", action="store_true", help="show each colour on the button for a technician")
    args = parser.parse_args(argv)
    return _probe() if args.probe else _flash()


if __name__ == "__main__":
    sys.exit(main())
