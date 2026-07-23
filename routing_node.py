#!/usr/bin/env python3
"""
Routing Node — OSRM process manager for Atlas offline cyberdeck.
Ground-up rewrite of the inline Docker routing in app.py.

Features:
  - Prefers native osrm-routed binary (no Docker overhead, less RAM, faster startup)
  - Falls back to Docker if native binary is not installed
  - LRU pool with configurable memory caps (default: 4 car + 2 other profiles)
  - On-demand loading: loads state only when a route request needs it
  - Geographic prewarm: auto-loads car + hiking routing for current GPS state
  - Cross-state routing: tries both origin and destination states; stitches if needed
  - Straight-line fallback when all OSRM sources fail (survival always gets a route)
  - Thread-safe; all process management is serialized via a lock
"""

import json
import logging
import math
import os
import socket
import subprocess
import threading
import time
from typing import Optional, Tuple, Dict
from gevent import monkey as _gevent_monkey

logger = logging.getLogger("routing-node")

# Routing prewarm/startup runs from real OS threads. gevent's patched subprocess
# layer can fail there with "child watchers are only available on the default
# loop", so use the original stdlib subprocess functions for OSRM lifecycle.
_STD_POPEN = _gevent_monkey.get_original("subprocess", "Popen")
_STD_RUN = _gevent_monkey.get_original("subprocess", "run")

# ── State bounding boxes: (lon_min, lat_min, lon_max, lat_max) ───────────────

STATE_BBOX: Dict[str, Tuple[float, float, float, float]] = {
    "alabama":        (-88.47, 30.22, -84.89, 35.01),
    "alaska":         (-179.15, 51.21, -129.98, 71.35),
    "arizona":        (-114.82, 31.33, -109.05, 37.00),
    "arkansas":       (-94.62, 33.00, -89.65, 36.50),
    "california":     (-124.41, 32.53, -114.13, 42.01),
    "colorado":       (-109.06, 36.99, -102.04, 41.00),
    "connecticut":    (-73.73, 40.98, -71.79, 42.05),
    "delaware":       (-75.79, 38.45, -75.05, 39.84),
    "florida":        (-87.63, 24.52, -80.03, 31.00),
    "georgia":        (-85.61, 30.36, -80.84, 35.00),
    "hawaii":         (-160.25, 18.91, -154.81, 22.24),
    "idaho":          (-117.24, 41.99, -111.04, 49.00),
    "illinois":       (-91.51, 36.97, -87.02, 42.51),
    "indiana":        (-88.10, 37.77, -84.78, 41.76),
    "iowa":           (-96.64, 40.37, -90.14, 43.50),
    "kansas":         (-102.05, 36.99, -94.59, 40.00),
    "kentucky":       (-89.57, 36.50, -81.96, 39.15),
    "louisiana":      (-94.04, 28.93, -88.82, 33.02),
    "maine":          (-71.08, 43.06, -66.95, 47.46),
    "maryland":       (-79.49, 37.89, -75.05, 39.72),
    "massachusetts":  (-73.50, 41.24, -69.93, 42.89),
    "michigan":       (-90.42, 41.70, -82.41, 48.31),
    "minnesota":      (-97.24, 43.50, -89.49, 49.38),
    "mississippi":    (-91.65, 30.17, -88.10, 35.01),
    "missouri":       (-95.77, 35.99, -89.10, 40.61),
    "montana":        (-116.05, 44.36, -104.04, 49.00),
    "nebraska":       (-104.05, 40.00, -95.31, 43.00),
    "nevada":         (-120.00, 35.00, -114.04, 42.00),
    "new-hampshire":  (-72.56, 42.70, -70.62, 45.31),
    "new-jersey":     (-75.56, 38.93, -73.89, 41.36),
    "new-mexico":     (-109.05, 31.33, -103.00, 37.00),
    "new-york":       (-79.76, 40.50, -71.86, 45.02),
    "north-carolina": (-84.32, 33.84, -75.46, 36.59),
    "north-dakota":   (-104.05, 45.94, -96.55, 49.00),
    "ohio":           (-84.82, 38.40, -80.52, 42.33),
    "oklahoma":       (-103.00, 33.62, -94.43, 37.00),
    "oregon":         (-124.57, 41.99, -116.46, 46.26),
    "pennsylvania":   (-80.52, 39.72, -74.69, 42.27),
    "rhode-island":   (-71.91, 41.15, -71.12, 42.01),
    "south-carolina": (-83.35, 32.05, -78.55, 35.22),
    "south-dakota":   (-104.06, 42.48, -96.44, 45.95),
    "tennessee":      (-90.31, 34.98, -81.65, 36.68),
    "texas":          (-106.65, 25.84, -93.51, 36.50),
    "utah":           (-114.05, 36.99, -109.04, 42.00),
    "vermont":        (-73.44, 42.73, -71.50, 45.02),
    "virginia":       (-83.68, 36.54, -75.24, 39.47),
    "washington":     (-124.73, 45.54, -116.92, 49.00),
    "west-virginia":  (-82.64, 37.20, -77.72, 40.64),
    "wisconsin":      (-92.89, 42.49, -86.25, 47.31),
    "wyoming":        (-111.06, 40.99, -104.05, 45.01),
    # Territories and DC — routed via nearest state with data
    "district-of-columbia": (-77.12, 38.79, -76.91, 38.99),
    "puerto-rico":          (-67.27, 17.88, -65.22, 18.52),
    "virgin-islands":       (-65.09, 17.67, -64.56, 18.43),
}

# DC routes through Maryland (smallest bbox overlap → Maryland data handles it)
_STATE_ALIAS = {
    "district-of-columbia": "maryland",
    "puerto-rico":          "puerto-rico",
    "virgin-islands":       "virgin-islands",
}

# Pool caps (to stay within 8 GB Jetson RAM budget)
_MAX_CAR   = 4   # ~500 MB each
_MAX_OTHER = 3   # hiking ~200 MB each

# How long a concurrent caller will wait for another in-flight start of the
# *same* (state, profile) to finish before giving up. Must stay comfortably
# above the _port_ready(..., timeout=120.0) readiness wait below (plus
# Popen/docker-run launch overhead) so a waiter never times out before the
# owner's own attempt would have.
_START_WAIT_TIMEOUT_S = 130.0

# Snap radius: unlimited for hiking so trailheads far from paved roads are reachable
_SNAP = {
    "car":     "25;25",
    "hiking":  "-1;-1",
}

# Docker OSRM image (multi-arch, supports ARM64 for Jetson)
_OSRM_IMAGE = "ghcr.io/project-osrm/osrm-backend"


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))


def _state_for_coord(lat: float, lon: float) -> Optional[str]:
    """Return the state/territory name for a lat/lon, resolving aliases.
    When bounding boxes overlap (e.g. MD/VA/DC), the smallest bbox wins
    so small states and DC are preferred over their larger neighbours.
    """
    best_state = None
    best_area  = float("inf")
    for state, (lon_min, lat_min, lon_max, lat_max) in STATE_BBOX.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            area = (lon_max - lon_min) * (lat_max - lat_min)
            if area < best_area:
                best_area  = area
                best_state = state
    if best_state is None:
        return None
    return _STATE_ALIAS.get(best_state, best_state)


def _all_states_for_coord(lat: float, lon: float) -> list:
    """Return all states whose bbox contains (lat, lon), sorted smallest-first.
    Used as fallback candidates when the primary state's OSRM has no road data
    near the coordinates (e.g. a point right on a state border).
    """
    matches = []
    for state, (lon_min, lat_min, lon_max, lat_max) in STATE_BBOX.items():
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            area = (lon_max - lon_min) * (lat_max - lat_min)
            resolved = _STATE_ALIAS.get(state, state)
            matches.append((area, resolved))
    matches.sort(key=lambda x: x[0])
    seen = set()
    result = []
    for _, s in matches:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


def _find_native_osrm() -> Optional[str]:
    """Return path to native osrm-routed binary, or None."""
    import shutil
    return shutil.which("osrm-routed")


def _port_ready(port: int, timeout: float = 30.0) -> bool:
    """Wait until TCP port accepts connections, or timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket()
            s.settimeout(0.3)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except OSError:
            time.sleep(0.25)
    return False


class RoutingNode:
    """
    Manages a pool of OSRM server processes (native or Docker) for offline routing.

    Usage::

        rn = RoutingNode(states_dir="/path/to/osrm-data/states")
        rn.prewarm("texas", "car")       # optional pre-loading
        result = rn.route(30.2, -97.7, 29.7, -95.3, profile="car")
        # result is an OSRM route dict or None
    """

    def __init__(self, states_dir: str, active_json: str = None,
                 use_native: Optional[bool] = None):
        self.states_dir  = states_dir
        self.active_json = active_json  # path to osrm_active.json (legacy compat)

        native_bin = _find_native_osrm()
        if use_native is None:
            self.use_native = bool(native_bin)
        else:
            self.use_native = use_native and bool(native_bin)
        self._native_bin = native_bin or "osrm-routed"

        # (state, profile) → (process_or_None, port)
        self._pool: Dict[Tuple[str, str], Tuple[object, int]] = {}
        self._lru: Dict[Tuple[str, str], float] = {}
        self._lock = threading.Lock()

        # (state, profile) → (reserved_port, done_event) while a start for
        # that key is in flight (port chosen / process launching, but not
        # yet confirmed ready and committed into self._pool). Guarded by
        # self._lock. Lets a second concurrent caller for the *same* key
        # (e.g. a foreground route request racing prewarm_for_position's
        # background thread) wait for and reuse the first caller's result
        # instead of launching a duplicate osrm-routed, and lets concurrent
        # callers for *different* keys avoid ever picking an in-flight,
        # not-yet-pooled port as "free".
        self._starting: Dict[Tuple[str, str], Tuple[int, threading.Event]] = {}

        logger.info(f"RoutingNode init: native={self.use_native} states={states_dir}")

    # ── Public API ───────────────────────────────────────────────────────────

    def get_state_for(self, lat: float, lon: float) -> Optional[str]:
        return _state_for_coord(lat, lon)

    def prewarm(self, state: str, profile: str = "car"):
        """Start OSRM for (state, profile) in a background thread."""
        threading.Thread(target=self._ensure_running,
                         args=(state, profile), daemon=True,
                         name=f"osrm-prewarm-{state}-{profile}").start()

    def prewarm_for_position(self, lat: float, lon: float):
        """Load car + hiking for the current state in background threads."""
        state = _state_for_coord(lat, lon)
        if not state:
            return
        for profile in ("car", "hiking"):
            self.prewarm(state, profile)

    def route(self, from_lat: float, from_lon: float,
              to_lat: float, to_lon: float,
              profile: str = "car",
              waypoints: list = None) -> dict:
        """
        Calculate a route offline.  Returns OSRM-format dict with 'routes' key.
        Falls back to straight-line estimate if OSRM is unavailable.
        """
        import urllib.request as _ur

        snap = _SNAP.get(profile, "25;25")
        origin_state = _state_for_coord(from_lat, from_lon)
        dest_state   = _state_for_coord(to_lat, to_lon)

        # Build coordinate string (add intermediate waypoints if provided)
        coords = f"{from_lon},{from_lat}"
        if waypoints:
            for wp in waypoints:
                coords += f";{wp[1]},{wp[0]}"
        coords += f";{to_lon},{to_lat}"

        # Build candidate state list: origin first, then destination.
        # Also include all bbox-overlapping states for each endpoint so that
        # a point right on a state border (e.g. VA/MD near the Potomac) is
        # tried against every plausible state's road network.
        candidates = []
        seen_states = set()
        for coord_lat, coord_lon in [(from_lat, from_lon), (to_lat, to_lon)]:
            for state in _all_states_for_coord(coord_lat, coord_lon):
                if state not in seen_states:
                    seen_states.add(state)
                    candidates.append(state)

        # Also check osrm_active.json for any pre-loaded US-wide container
        if self.active_json and os.path.exists(self.active_json):
            try:
                with open(self.active_json) as f:
                    active = json.load(f).get("states", {})
                for wildcard in ("us", "all"):
                    if wildcard in active and active[wildcard].get(profile):
                        port = int(active[wildcard][profile])
                        result = self._query_osrm(port, coords, profile, snap)
                        if result:
                            result["routes"][0]["_source"] = f"local-{wildcard}"
                            return result
            except Exception:
                pass

        # Try each candidate state — only when both endpoints are in the same state
        # (or destination state is unknown).  For cross-state routes we skip this
        # loop entirely: a single-state OSRM may accept the query by snapping the
        # out-of-state destination to a border road (<2000 m), returning a partial
        # route that stops at the border instead of reaching the actual destination.
        if origin_state == dest_state or dest_state is None:
            for state in candidates:
                port = self._ensure_running(state, profile)
                if not port:
                    continue
                result = self._query_osrm(port, coords, profile, snap, max_snap_m=2000)
                if result:
                    result["routes"][0]["_source"] = f"local-{state}"
                    return result

        # Hiking cross-state shortcut: before bbox-based stitching, try each
        # destination-state OSRM directly with both endpoints.  NPS and backcountry
        # trails often run continuously across state lines, so the destination
        # state's OSRM can usually route all the way from a near-border origin to
        # the trailhead without the geometric crossing-point handoffs that
        # bbox-stitching produces (which land on ridges with no trail network).
        # We verify that the destination waypoint snapped within 2 km.
        # NOTE: do NOT skip based on seen_states — that set includes dest-state coords
        # (built from both endpoints), but the same-state loop above was skipped for
        # cross-state routes, so destination states have not actually been tried yet.
        if profile == "hiking" and origin_state and dest_state and origin_state != dest_state:
            dest_candidates = _all_states_for_coord(to_lat, to_lon) or [dest_state]
            for state in dest_candidates:
                port = self._ensure_running(state, profile)
                if not port:
                    continue
                result = self._query_osrm(port, coords, profile, snap)
                if not result:
                    continue
                # Reject if destination snapped too far (wrong-state snap to a road)
                wps = result.get("waypoints", [])
                dest_snap_m = wps[-1].get("distance", 0) if len(wps) >= 2 else 0
                if dest_snap_m > 2000:
                    logger.debug(
                        f"Hiking cross-state: {state} OSRM dest snap "
                        f"{dest_snap_m:.0f}m > 2000m, skipping")
                    continue
                result["routes"][0]["_source"] = f"local-{state}"
                logger.info(
                    f"Hiking cross-state resolved by {state} OSRM "
                    f"(dest snap {dest_snap_m:.0f}m)")
                return result

        # Multi-hop cross-state routing: plan the full state path and stitch legs
        if origin_state and dest_state:
            stitched = self._route_multi_hop(
                from_lat, from_lon, to_lat, to_lon, profile, snap)
            if stitched:
                return stitched

        # Straight-line fallback — always returns a route so navigation keeps working
        logger.info(f"Route fallback: straight line {origin_state}→{dest_state}")
        return self._straight_line(from_lat, from_lon, to_lat, to_lon, profile)

    def shutdown(self):
        """Stop all managed OSRM processes."""
        with self._lock:
            for key, (proc, port) in list(self._pool.items()):
                self._stop_process(proc, key)
            self._pool.clear()
            self._lru.clear()

    def get_active_map(self) -> dict:
        """Return {state: {profile: port}} for all running instances."""
        with self._lock:
            result = {}
            for (state, profile), (_, port) in self._pool.items():
                result.setdefault(state, {})[profile] = port
            return result

    # ── Process lifecycle ────────────────────────────────────────────────────

    def _wait_for_start(self, event: threading.Event, timeout: float) -> bool:
        """Wait for a concurrent in-flight _ensure_running() start (same key)
        to finish, without blocking the gevent event loop when called from
        the main request-handling thread.

        event is a plain threading.Event (real, not gevent-patched — monkey
        patching runs with thread=False). A blocking .wait() from a greenlet
        running in the main OS thread would freeze the whole event loop,
        including the greenlet that's supposed to set the event — so poll
        cooperatively there instead. Real background threads (prewarm, the
        nav reroute loop) can block normally. Mirrors AIManager._ollama_slot.
        """
        if threading.current_thread() is threading.main_thread():
            import gevent
            deadline = time.monotonic() + timeout
            while not event.is_set():
                if time.monotonic() >= deadline:
                    return False
                gevent.sleep(0.2)
            return True
        return event.wait(timeout=timeout)

    def _ensure_running(self, state: str, profile: str) -> Optional[int]:
        """Start OSRM for (state, profile) if not running. Return port or None."""
        profile_dir = os.path.join(self.states_dir, state, profile)
        osrm_file = os.path.join(profile_dir, "region.osrm")
        marker    = os.path.join(profile_dir, ".processed")
        if not os.path.exists(marker):
            return None

        if not os.path.exists(osrm_file):
            try:
                import glob
                if glob.glob(f"{osrm_file}.*"):
                    open(osrm_file, "a", encoding="ascii").close()
            except Exception as e:
                logger.debug(f"Failed to materialize OSRM base file for {state}/{profile}: {e}")

        if not os.path.exists(osrm_file):
            logger.warning(f"OSRM dataset incomplete for {state}/{profile}: missing {osrm_file}")
            return None

        key = (state, profile)

        # 1. Check our own pool first (fast path, under lock)
        with self._lock:
            if key in self._pool:
                proc, port = self._pool[key]
                alive = (proc is None or
                         (hasattr(proc, "poll") and proc.poll() is None) or
                         (isinstance(proc, str)))  # Docker container name
                if alive and _port_ready(port, timeout=0.3):
                    self._lru[key] = time.time()
                    return port
                # Process died — evict
                del self._pool[key]
                self._lru.pop(key, None)

        # 2. Adopt an externally-managed instance from osrm_active.json
        #    (started by start_routing.sh) — avoids launching a duplicate process.
        if self.active_json and os.path.exists(self.active_json):
            try:
                with open(self.active_json) as f:
                    active_states = json.load(f).get("states", {})
                ext_port = active_states.get(state, {}).get(profile)
                if ext_port:
                    ext_port = int(ext_port)
                    if _port_ready(ext_port, timeout=0.5):
                        with self._lock:
                            self._pool[key] = (None, ext_port)
                            self._lru[key]  = time.time()
                        logger.info(f"OSRM {state}/{profile}: adopted external on port {ext_port}")
                        return ext_port
            except Exception as e:
                logger.debug(f"adopt-external OSRM check: {e}")

        # 3. Launch a new managed instance — or reuse/wait-for a concurrent
        #    in-flight start of this exact key instead of racing/duplicating.
        with self._lock:
            if key in self._pool:                 # may have just landed while
                proc, port = self._pool[key]       # we were doing steps 1-2
                self._lru[key] = time.time()
                return port

            inflight = self._starting.get(key)
            if inflight is None:
                # We are now the owner of this key's start attempt. Reserve a
                # port for it *before* releasing the lock, so no concurrent
                # caller — same key or a different one — can ever pick this
                # port as "free".
                is_car = profile == "car"
                cap    = _MAX_CAR if is_car else _MAX_OTHER
                same_profile = [(k, v) for k, v in self._pool.items() if k[1] == profile]
                if len(same_profile) >= cap:
                    evict_key = min(same_profile, key=lambda kv: self._lru.get(kv[0], 0.0))[0]
                    evict_proc, _ = self._pool[evict_key]
                    self._stop_process(evict_proc, evict_key)
                    del self._pool[evict_key]
                    self._lru.pop(evict_key, None)
                    logger.info(f"OSRM LRU evict: {evict_key[0]}/{evict_key[1]}")

                # Free port — excludes ports already claimed by other
                # in-flight (not-yet-pooled) starts, not just pooled ones.
                used = {p for _, p in self._pool.values()}
                used |= {p for p, _ in self._starting.values()}
                port = 5001
                while port in used:
                    port += 1
                done_event = threading.Event()
                self._starting[key] = (port, done_event)

        if inflight is not None:
            # Someone else is already starting this exact (state, profile) —
            # e.g. a foreground route request racing a prewarm thread for the
            # same key. Wait for it and reuse its result instead of racing on
            # port choice or launching a second osrm-routed for the same state.
            port, done_event = inflight
            if not self._wait_for_start(done_event, timeout=_START_WAIT_TIMEOUT_S):
                logger.error(f"OSRM {state}/{profile}: timed out waiting on "
                             f"concurrent start (port {port})")
                return None
            with self._lock:
                if key in self._pool:
                    proc, port = self._pool[key]
                    self._lru[key] = time.time()
                    return port
            # The owner's attempt finished but failed (nothing committed to
            # self._pool) — share its outcome rather than piling a retry on
            # top of an already-failed key.
            logger.debug(f"OSRM {state}/{profile}: concurrent start attempt "
                         f"by another caller failed")
            return None

        # We own this key's start attempt.
        try:
            # Outside lock: verify the candidate port isn't claimed by
            # something we don't manage (e.g. a manually-started osrm-routed
            # from start_routing.sh).
            while _port_ready(port, timeout=0.1):
                logger.debug(f"Port {port} already in use externally, trying {port + 1}")
                port += 1
                with self._lock:
                    used = {p for _, p in self._pool.values()}
                    used |= {p for k2, (p, _) in self._starting.items() if k2 != key}
                    while port in used:
                        port += 1
                    self._starting[key] = (port, done_event)

            # Start outside lock to avoid blocking other threads during ~15-120s startup
            proc = (self._start_native(osrm_file, port)
                    if self.use_native
                    else self._start_docker(state, profile, port))

            if not proc:
                return None

            if not _port_ready(port, timeout=120.0):
                logger.error(f"OSRM {state}/{profile} port {port} never became ready")
                self._stop_process(proc, (state, profile))
                return None

            with self._lock:
                self._pool[(state, profile)] = (proc, port)
                self._lru[(state, profile)]  = time.time()

            self._write_active_json()
            logger.info(f"OSRM {state}/{profile} ready on port {port} "
                        f"({'native' if self.use_native else 'docker'})")
            return port
        finally:
            with self._lock:
                self._starting.pop(key, None)
            done_event.set()

    def _start_native(self, osrm_file: str, port: int):
        """Launch native osrm-routed subprocess."""
        cmd = [self._native_bin,
               "--algorithm", "mld",
               "--port", str(port),
               "--ip", "127.0.0.1",
               "--max-table-size", "1000",
               osrm_file]
        try:
            proc = _STD_POPEN(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True)
            logger.info(f"Native OSRM started: {osrm_file} → port {port} (pid {proc.pid})")
            return proc
        except FileNotFoundError:
            logger.error(f"osrm-routed not found at {self._native_bin}")
            return None
        except Exception as e:
            logger.error(f"Failed to start native OSRM: {e}")
            return None

    def _start_docker(self, state: str, profile: str, port: int):
        """Launch Docker OSRM container. Returns container name or None."""
        pdir  = os.path.join(self.states_dir, state, profile)
        cname = f"atlas-osrm-{state}-{profile}"
        # Remove any stale container with the same name
        _STD_RUN(["docker", "rm", "-f", cname],
                 capture_output=True, timeout=10)
        cmd = ["docker", "run", "-d",
               "--name", cname, "--restart", "unless-stopped",
               "-p", f"127.0.0.1:{port}:5000",
               "-v", f"{pdir}:/data:ro",
               _OSRM_IMAGE,
               "osrm-routed", "--algorithm", "mld",
               "--max-table-size", "1000", "/data/region.osrm"]
        try:
            _STD_RUN(cmd, check=True, capture_output=True, timeout=30)
            logger.info(f"Docker OSRM started: {cname} → port {port}")
            return cname   # store container name as "process" sentinel
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.decode()[:300] if e.stderr else ""
            if "already in use" in stderr or "Conflict" in stderr:
                # Race condition: another thread started it already
                return cname
            logger.error(f"docker run {cname}: {stderr}")
            return None
        except Exception as e:
            logger.error(f"Failed to start Docker OSRM {cname}: {e}")
            return None

    def _stop_process(self, proc, key):
        """Stop a native subprocess or Docker container."""
        try:
            if isinstance(proc, str):
                # Docker container name
                _STD_RUN(["docker", "stop", proc], capture_output=True, timeout=15)
                _STD_RUN(["docker", "rm",   proc], capture_output=True, timeout=10)
            elif proc and hasattr(proc, "terminate"):
                import signal as _sig
                try:
                    os.killpg(os.getpgid(proc.pid), _sig.SIGTERM)
                except Exception:
                    proc.terminate()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
        except Exception as e:
            logger.debug(f"Stop OSRM {key}: {e}")

    # ── Route query ──────────────────────────────────────────────────────────

    def _query_osrm(self, port: int, coords: str, profile: str, snap: str,
                    max_snap_m: Optional[float] = None) -> Optional[dict]:
        """HTTP GET to local OSRM. Returns parsed JSON dict or None.

        Some OSRM datasets are processed without the base region.osrm index file
        (e.g. only the .osrm.* component files are present). In that case OSRM
        rejects any request that includes the `radiuses` query parameter with HTTP
        400. When that happens we retry once without the snap-radius constraint.

        max_snap_m: if set, reject routes where any waypoint snapped farther than
        this many metres from its requested coordinate (prevents wrong-state routing
        in single-state candidate queries).  Not applied for multi-hop legs because
        bbox exit points may legitimately be far from the state's road network.
        """
        import urllib.request as _ur
        base = (f"http://127.0.0.1:{port}/route/v1/{profile}/{coords}"
                f"?steps=true&geometries=geojson&overview=full&annotations=false")
        retry_without_snap = False
        try:
            req = _ur.Request(f"{base}&radiuses={snap}",
                              headers={"User-Agent": "AtlasControl/1.0"})
            with _ur.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            if data.get("code") == "Ok" and data.get("routes"):
                return data
        except Exception as e:
            # HTTP 400 typically means the radiuses parameter is unsupported by
            # this OSRM dataset (missing base index file). Retry without it.
            if hasattr(e, "code") and e.code == 400:
                retry_without_snap = True
            else:
                logger.debug(f"OSRM query port {port}: {e}")

        if retry_without_snap:
            try:
                req2 = _ur.Request(base, headers={"User-Agent": "AtlasControl/1.0"})
                with _ur.urlopen(req2, timeout=8) as r2:
                    data2 = json.loads(r2.read())
                if data2.get("code") == "Ok" and data2.get("routes"):
                    if max_snap_m is not None:
                        waypoints = data2.get("waypoints", [])
                        ms = max((wp.get("distance", 0) for wp in waypoints), default=0)
                        if ms > max_snap_m:
                            logger.debug(
                                f"OSRM port {port}: snap {ms:.0f}m > {max_snap_m:.0f}m, "
                                f"rejecting wrong-state route")
                            return None
                    return data2
            except Exception as e2:
                logger.debug(f"OSRM query (no snap) port {port}: {e2}")

        return None

    # ── Multi-hop cross-state routing ────────────────────────────────────────

    @staticmethod
    def _plan_state_hops(from_lat: float, from_lon: float,
                         to_lat: float, to_lon: float,
                         start_state: str = None) -> list:
        """Return ordered list of (state, leg_from_lat, leg_from_lon, leg_to_lat,
        leg_to_lon) segments following the great-circle path from origin to
        destination, one entry per state traversed.  For same-state routes this
        returns a single entry.  For cross-country routes it returns one entry
        per state in order (e.g. VA→TN→TX→NM→AZ→CA).

        start_state overrides the automatic state detection for the origin
        (useful when bbox overlaps cause the wrong state to be chosen).
        """
        dlat = to_lat - from_lat
        dlon = to_lon - from_lon
        mag  = math.hypot(dlat, dlon)

        legs      = []
        cur_lat   = from_lat
        cur_lon   = from_lon
        visited: set = set()
        use_override = start_state  # only applied on the first iteration

        for _ in range(25):  # safety cap — US cross-country max ~12 states
            if use_override:
                cur_state   = use_override
                use_override = None
            else:
                cur_state = _state_for_coord(cur_lat, cur_lon)

            if not cur_state or cur_state in visited:
                break

            dest_state = _state_for_coord(to_lat, to_lon)
            if cur_state == dest_state:
                legs.append((cur_state, cur_lat, cur_lon, to_lat, to_lon))
                break

            visited.add(cur_state)

            # Find where the path line exits this state's bbox
            crossing = RoutingNode._border_crossing(
                cur_lat, cur_lon, to_lat, to_lon, cur_state)
            if not crossing:
                # No exit found — destination may be outside all known bboxes,
                # just route to dest directly from here
                legs.append((cur_state, cur_lat, cur_lon, to_lat, to_lon))
                break

            exit_lat, exit_lon = crossing
            legs.append((cur_state, cur_lat, cur_lon, exit_lat, exit_lon))

            # Advance past the crossing in the path direction to land in the
            # next state.  The bbox exit point may be in the ocean or another
            # gap (e.g. NC coast vs SC bbox), so we try increasing steps until
            # we find a state or pass the destination.
            if mag < 1e-9:
                break
            found_next = False
            # Use fine steps near the crossing (0.1°), then coarser (0.5°) for
            # longer ocean/gap stretches (e.g. SC coast → FL, ~6° along path).
            step_schedule = (
                [(n * 0.1) for n in range(1, 16)] +   # 0.1° … 1.5°
                [(1.5 + n * 0.5) for n in range(1, 24)]  # 2.0° … 13.0°
            )
            for step in step_schedule:
                nxt_lat = exit_lat + step * dlat / mag
                nxt_lon = exit_lon + step * dlon / mag
                if (to_lat - nxt_lat) * dlat + (to_lon - nxt_lon) * dlon <= 0:
                    break
                if _state_for_coord(nxt_lat, nxt_lon):
                    cur_lat, cur_lon = nxt_lat, nxt_lon
                    found_next = True
                    break
            if not found_next:
                break

        return legs

    def _route_multi_hop(self, from_lat: float, from_lon: float,
                         to_lat: float, to_lon: float,
                         profile: str, snap: str) -> Optional[dict]:
        """Route across any number of states by stitching per-state OSRM legs.

        Tries each candidate origin state (handles bbox overlaps like VA/MD
        border) to find a plan whose first leg actually has road data.  Builds
        the state sequence with _plan_state_hops, starts each state's OSRM on
        demand, queries each leg, then concatenates the geometries.
        Returns None if no plan succeeds (caller falls back to straight-line).
        """
        # Try each state that contains the origin point — the primary state
        # from _state_for_coord may lack local road data (bbox overlap issue,
        # e.g. VA coordinates returning 'maryland' as primary state).
        origin_candidates = _all_states_for_coord(from_lat, from_lon) or [
            _state_for_coord(from_lat, from_lon)]

        for start_state in origin_candidates:
            legs = self._plan_state_hops(
                from_lat, from_lon, to_lat, to_lon, start_state=start_state)
            if not legs:
                continue
            result = self._execute_hop_legs(legs, profile, snap)
            if result:
                return result

        return None

    def _execute_hop_legs(self, legs: list, profile: str, snap: str) -> Optional[dict]:
        """Route each leg in the state-hop plan and stitch results together."""
        if len(legs) == 1:
            state, lf_lat, lf_lon, lt_lat, lt_lon = legs[0]
            port = self._ensure_running(state, profile)
            if not port:
                return None
            coords = f"{lf_lon},{lf_lat};{lt_lon},{lt_lat}"
            result = self._query_osrm(port, coords, profile, snap)
            if result:
                result["routes"][0]["_source"] = f"local-{state}"
            return result

        logger.info(
            f"Multi-hop route: {' → '.join(l[0] for l in legs)} "
            f"({len(legs)} legs, profile={profile})")

        leg_routes = []
        for i, (state, lf_lat, lf_lon, lt_lat, lt_lon) in enumerate(legs):
            port = self._ensure_running(state, profile)
            if not port:
                logger.debug(f"Multi-hop: no OSRM for {state}/{profile}, aborting")
                return None
            coords = f"{lf_lon},{lf_lat};{lt_lon},{lt_lat}"
            result = self._query_osrm(port, coords, profile, snap)
            if not result:
                logger.debug(f"Multi-hop: OSRM query failed for {state}, aborting")
                return None
            # For the first leg, validate that the route origin snapped to a road
            # within 2000 m of the user's actual position.  If OSRM snapped the
            # origin far away (e.g. MD OSRM pulling a VA coordinate across the
            # Potomac) the plan is wrong — reject it so _route_multi_hop can try
            # the next start_state candidate (e.g. virginia).
            if i == 0:
                wps = result.get("waypoints", [])
                origin_snap_m = wps[0].get("distance", 0) if wps else 0
                if origin_snap_m > 2000:
                    logger.debug(
                        f"Multi-hop first leg: {state} OSRM snapped origin "
                        f"{origin_snap_m:.0f}m from GPS position — rejecting plan")
                    return None
            leg_routes.append((state, result["routes"][0]))

        # Stitch all legs: concatenate geometries, sum distance/duration
        all_coords = leg_routes[0][1]["geometry"]["coordinates"][:]
        all_legs   = list(leg_routes[0][1].get("legs", []))
        total_dist = leg_routes[0][1]["distance"]
        total_dur  = leg_routes[0][1]["duration"]

        for _, r in leg_routes[1:]:
            all_coords.extend(r["geometry"]["coordinates"][1:])  # skip duplicate
            all_legs.extend(r.get("legs", []))
            total_dist += r["distance"]
            total_dur  += r["duration"]

        state_names = "-".join(s for s, _ in leg_routes)
        if len(state_names) > 80:
            state_names = state_names[:77] + "..."
        merged = {
            "geometry": {"type": "LineString", "coordinates": all_coords},
            "distance": total_dist,
            "duration": total_dur,
            "legs":     all_legs,
            "_source":  f"stitched-{state_names}",
        }
        return {"routes": [merged]}

    # ── Cross-state routing ──────────────────────────────────────────────────

    @staticmethod
    def _border_crossing(from_lat, from_lon, to_lat, to_lon, state) -> Optional[tuple]:
        """
        Return the (lat, lon) where the straight line from→to first exits the
        state bounding box.  Used as the handoff point for cross-state stitching.
        """
        bbox = STATE_BBOX.get(state)
        if not bbox:
            return None
        lon_min, lat_min, lon_max, lat_max = bbox
        dlat = to_lat - from_lat
        dlon = to_lon - from_lon
        crossings = []
        for edge_lat in (lat_min, lat_max):
            if dlat == 0:
                continue
            t = (edge_lat - from_lat) / dlat
            if 0 < t < 1:
                edge_lon = from_lon + t * dlon
                if lon_min <= edge_lon <= lon_max:
                    crossings.append((t, from_lat + t * dlat, edge_lon))
        for edge_lon in (lon_min, lon_max):
            if dlon == 0:
                continue
            t = (edge_lon - from_lon) / dlon
            if 0 < t < 1:
                edge_lat2 = from_lat + t * dlat
                if lat_min <= edge_lat2 <= lat_max:
                    crossings.append((t, edge_lat2, from_lon + t * dlon))
        if not crossings:
            return None
        _, cross_lat, cross_lon = min(crossings)   # first exit point
        return cross_lat, cross_lon

    def _cross_state_route(self, from_lat, from_lon, to_lat, to_lon,
                           origin_state, dest_state, profile, snap) -> Optional[dict]:
        """
        Stitch two per-state routes at the point where the path crosses the
        origin state's bounding box boundary (more accurate than centroid midpoint).
        Falls back to centroid split if no crossing is found.
        """
        # Find handoff: where the route leaves the origin state bbox
        crossing = self._border_crossing(from_lat, from_lon, to_lat, to_lon, origin_state)
        if crossing:
            mid_lat, mid_lon = crossing
        else:
            # Fallback: midpoint between the two state centroids
            def _cen(s):
                b = STATE_BBOX[s]
                return (b[1] + b[3]) / 2, (b[0] + b[2]) / 2
            o_lat, o_lon = _cen(origin_state)
            d_lat, d_lon = _cen(dest_state)
            mid_lat = (o_lat + d_lat) / 2
            mid_lon = (o_lon + d_lon) / 2

        mid_state = _state_for_coord(mid_lat, mid_lon)

        # Leg 1: origin → border crossing (using origin state routing)
        port1 = self._ensure_running(origin_state, profile)
        leg1  = None
        if port1:
            coords1 = f"{from_lon},{from_lat};{mid_lon},{mid_lat}"
            leg1 = self._query_osrm(port1, coords1, profile, snap)

        # Leg 2: border crossing → destination (using dest or mid state routing)
        port2 = self._ensure_running(dest_state, profile)
        if not port2 and mid_state and mid_state != origin_state:
            port2 = self._ensure_running(mid_state, profile)
        leg2  = None
        if port2:
            coords2 = f"{mid_lon},{mid_lat};{to_lon},{to_lat}"
            leg2 = self._query_osrm(port2, coords2, profile, snap)

        # If leg 1 failed (origin has no data), try a direct query via dest/mid state
        if not leg1 and port2:
            full_coords = f"{from_lon},{from_lat};{to_lon},{to_lat}"
            direct = self._query_osrm(port2, full_coords, profile, snap)
            if direct:
                direct["routes"][0]["_source"] = f"local-{dest_state}"
                return direct

        if not leg1 or not leg2:
            return None

        # Stitch routes: concatenate geometries and sum distances/durations
        try:
            r1 = leg1["routes"][0]
            r2 = leg2["routes"][0]
            coords_1 = r1["geometry"]["coordinates"]
            coords_2 = r2["geometry"]["coordinates"]
            # Merge: drop the duplicate midpoint coord (last of r1 = first of r2)
            merged_coords = coords_1 + coords_2[1:]
            merged_route = {
                "geometry": {"type": "LineString", "coordinates": merged_coords},
                "distance": r1["distance"] + r2["distance"],
                "duration": r1["duration"] + r2["duration"],
                "legs":     r1["legs"] + r2["legs"],
                "_source":  f"stitched-{origin_state}-{dest_state}",
            }
            return {"routes": [merged_route]}
        except Exception as e:
            logger.debug(f"Cross-state stitch failed: {e}")
            return None

    # ── Straight-line fallback ───────────────────────────────────────────────

    def _straight_line(self, from_lat, from_lon, to_lat, to_lon, profile) -> dict:
        speed = {"car": 13.89, "bicycle": 4.17, "foot": 1.39, "hiking": 0.83}.get(profile, 5.0)
        dist  = _haversine_m(from_lat, from_lon, to_lat, to_lon)
        dur   = dist / speed
        return {"routes": [{
            "geometry": {"type": "LineString",
                         "coordinates": [[from_lon, from_lat], [to_lon, to_lat]]},
            "distance": round(dist),
            "duration": round(dur),
            "legs": [{"steps": [
                {"maneuver": {"type": "depart", "modifier": "straight",
                              "location": [from_lon, from_lat]},
                 "name": "direct path", "distance": round(dist), "duration": round(dur)},
                {"maneuver": {"type": "arrive", "location": [to_lon, to_lat]},
                 "name": "", "distance": 0, "duration": 0},
            ], "distance": round(dist), "duration": round(dur)}],
            "_source": "straight_line",
        }]}

    # ── osrm_active.json (backward compat with start_routing.sh) ─────────────

    def _write_active_json(self):
        if not self.active_json:
            return
        try:
            states  = self.get_active_map()   # get_active_map handles its own locking
            payload = {"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "states": states}
            with open(self.active_json, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.debug(f"Write osrm_active.json: {e}")
