#!/usr/bin/env python3
"""
Navigation Node — Turn-by-turn engine for Atlas offline cyberdeck.

Features:
  - Parses OSRM route steps into human-readable instructions
  - Tracks progress along the route using GPS position snapping
  - Off-route detection with configurable thresholds per profile
  - Auto-reroute: triggers new route calculation after sustained off-route
  - ETA calculation updated continuously
  - Dead-reckoning aware: uses any fix type (GNSS or DR) for position
  - Meshtastic: optionally broadcasts nav state to mesh network
  - Thread-safe Socket.IO events for real-time UI updates
  - Waypoint support: multi-destination routes
"""

import json
import math
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Any

logger = logging.getLogger("navigation")

# Off-route thresholds (meters) per profile
_OFF_ROUTE_M = {"car": 50.0, "bicycle": 40.0, "foot": 30.0, "hiking": 60.0}
_OFF_ROUTE_DEFAULT = 50.0
_REROUTE_DELAY_S   = 12.0    # seconds off-route before triggering reroute
_ARRIVE_RADIUS_M   = 25.0    # within this many meters = arrived
_STEP_ADVANCE_M    = 20.0    # snap to next step when within this distance of maneuver
_NAV_UPDATE_HZ     = 1.0     # navigation state update interval (seconds)


# ── Geometry helpers ─────────────────────────────────────────────────────────

def _hav_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin(math.radians(lat2 - lat1) / 2) ** 2
         + math.cos(p1) * math.cos(p2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))


def _bearing(lat1, lon1, lat2, lon2) -> float:
    """True bearing from point 1 to point 2 (degrees, 0=North)."""
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _nearest_on_segment(px, py, ax, ay, bx, by):
    """
    Nearest point (in lon/lat degrees) on segment AB to point P.
    Returns (nearest_lon, nearest_lat, fraction_along_segment).
    """
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ax, ay, 0.0
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return ax + t * dx, ay + t * dy, t


def _cross_track_m(lat, lon, coords) -> float:
    """
    Minimum cross-track distance (meters) from (lat, lon) to the polyline
    defined by coords (list of [lon, lat]).
    """
    if not coords or len(coords) < 2:
        return float("inf")
    best = float("inf")
    for i in range(len(coords) - 1):
        ax, ay = coords[i][0], coords[i][1]     # lon, lat
        bx, by = coords[i + 1][0], coords[i + 1][1]
        nx, ny, _ = _nearest_on_segment(lon, lat, ax, ay, bx, by)
        d = _hav_m(lat, lon, ny, nx)
        if d < best:
            best = d
    return best


def _total_line_length_m(coords) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        total += _hav_m(coords[i][1], coords[i][0], coords[i + 1][1], coords[i + 1][0])
    return total


# ── Turn instruction builder ─────────────────────────────────────────────────

_MODIFIER_WORD = {
    "uturn": "make a U-turn",
    "sharp right": "turn sharp right",
    "right": "turn right",
    "slight right": "keep right",
    "straight": "continue straight",
    "slight left": "keep left",
    "left": "turn left",
    "sharp left": "turn sharp left",
}

_ROUNDABOUT_ORDINAL = ["", "first", "second", "third", "fourth", "fifth",
                       "sixth", "seventh", "eighth"]


def _build_instruction(maneuver: dict, road_name: str, distance: float) -> str:
    """Format a human-readable turn instruction."""
    mtype    = maneuver.get("type", "")
    modifier = maneuver.get("modifier", "straight")
    exit_n   = maneuver.get("exit", 1)

    road = f" onto {road_name}" if road_name else ""
    dist = _format_distance(distance)

    if mtype == "depart":
        bearing_after = maneuver.get("bearing_after", 0)
        cardinal = _cardinal(bearing_after)
        return f"Head {cardinal}{road}"

    if mtype == "arrive":
        return "You have arrived at your destination"

    if mtype in ("roundabout", "rotary"):
        ordinal = _ROUNDABOUT_ORDINAL[min(exit_n, len(_ROUNDABOUT_ORDINAL) - 1)]
        return f"Enter roundabout, take {ordinal} exit{road}"

    if mtype in ("turn", "end of road", "fork"):
        action = _MODIFIER_WORD.get(modifier, f"turn {modifier}")
        return f"{action.capitalize()}{road} in {dist}"

    if mtype in ("merge", "on ramp", "off ramp"):
        return f"{'Merge' if mtype == 'merge' else 'Take ramp'}{road} in {dist}"

    if mtype == "new name":
        return f"Continue{road}"

    return f"Continue for {dist}"


def _format_distance(meters: float) -> str:
    if meters < 100:
        return f"{int(meters)} m"
    if meters < 1000:
        return f"{round(meters / 10) * 10:.0f} m"
    if meters < 10_000:
        return f"{meters / 1000:.1f} km"
    return f"{meters / 1000:.0f} km"


def _format_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    return f"{h}h {m}min" if m else f"{h}h"


def _cardinal(bearing: float) -> str:
    dirs = ["north", "northeast", "east", "southeast",
            "south", "southwest", "west", "northwest"]
    return dirs[int((bearing + 22.5) / 45) % 8]


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class RouteStep:
    instruction:  str
    distance:     float    # meters to next maneuver
    duration:     float    # seconds
    maneuver_loc: tuple    # (lat, lon)
    maneuver_type: str
    road_name:    str
    geometry:     list     # [[lon, lat], …] for this step's path


@dataclass
class ActiveRoute:
    steps:            List[RouteStep]
    full_geometry:    list            # flattened [[lon, lat], …]
    total_distance:   float
    total_duration:   float
    profile:          str
    destination_name: str
    start_time:       float = field(default_factory=time.time)

    def remaining_distance(self, from_step: int) -> float:
        return sum(s.distance for s in self.steps[from_step:])

    def remaining_duration(self, from_step: int) -> float:
        return sum(s.duration for s in self.steps[from_step:])


# ── Navigation Node ───────────────────────────────────────────────────────────

class NavigationNode:
    """
    Turn-by-turn navigation engine.

    Wire it up::

        nav = NavigationNode(socketio=socketio, routing_node=rn, mesh_manager=mesh)
        nav.start()
        nav.start_navigation(30.2, -97.7, 29.7, -95.3, profile="car",
                             destination_name="Austin, TX")
    """

    def __init__(self, socketio=None, routing_node=None, mesh_manager=None):
        self.socketio      = socketio
        self.routing_node  = routing_node
        self.mesh_manager  = mesh_manager

        self._route: Optional[ActiveRoute] = None
        self._step_idx  = 0
        self._off_route = False
        self._off_route_since: Optional[float] = None
        self._rerouting = False
        self._lock      = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.running    = False

        # Last known position (set by GPS node callback or manual update)
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._speed: Optional[float] = None
        self._heading: Optional[float] = None

        # Destination stored for rerouting
        self._dest_lat: Optional[float] = None
        self._dest_lon: Optional[float] = None
        self._dest_name: str = ""
        self._profile: str = "car"
        self._waypoints: list = []

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self):
        self.running = True
        self._thread = threading.Thread(target=self._nav_loop,
                                        daemon=True, name="navigation")
        self._thread.start()
        logger.info("Navigation node started")

    def stop(self):
        self.running = False

    def update_position(self, lat: float, lon: float,
                        speed: float = None, heading: float = None):
        """Called by GPS node on every fix."""
        with self._lock:
            self._lat, self._lon = lat, lon
            if speed is not None:   self._speed   = speed
            if heading is not None: self._heading = heading

    def start_navigation(self, from_lat: float, from_lon: float,
                         to_lat: float, to_lon: float,
                         profile: str = "car",
                         destination_name: str = "",
                         waypoints: list = None) -> dict:
        """
        Start turn-by-turn navigation.
        Returns {"ok": True, "route": {...}} or {"ok": False, "error": "..."}.
        """
        if not self.routing_node:
            return {"ok": False, "error": "No routing engine configured"}

        result = self.routing_node.route(from_lat, from_lon, to_lat, to_lon,
                                         profile=profile, waypoints=waypoints)
        if not result or not result.get("routes"):
            return {"ok": False, "error": "No route found"}

        route_data = result["routes"][0]
        parsed = self._parse_osrm_route(route_data, profile, destination_name or "")
        if not parsed:
            return {"ok": False, "error": "Failed to parse route"}

        with self._lock:
            self._route        = parsed
            self._step_idx     = 0
            self._off_route    = False
            self._off_route_since = None
            self._rerouting    = False
            self._dest_lat     = to_lat
            self._dest_lon     = to_lon
            self._dest_name    = destination_name or ""
            self._profile      = profile
            self._waypoints    = waypoints or []

        self._emit_nav_state()
        logger.info(
            f"Navigation started: {destination_name} "
            f"dist={route_data.get('distance', 0) / 1000:.1f}km "
            f"profile={profile} source={route_data.get('_source', '?')}"
        )
        return {"ok": True, "route": self._route_summary(parsed)}

    def cancel_navigation(self):
        with self._lock:
            self._route     = None
            self._step_idx  = 0
            self._off_route = False
            self._rerouting = False
        self._emit_nav_cancelled()
        logger.info("Navigation cancelled")

    def get_state(self) -> dict:
        with self._lock:
            return self._build_state()

    # ── Navigation loop ──────────────────────────────────────────────────────

    def _nav_loop(self):
        while self.running:
            time.sleep(_NAV_UPDATE_HZ)
            with self._lock:
                if not self._route or self._rerouting:
                    continue
                lat, lon = self._lat, self._lon
                if lat is None or lon is None:
                    continue
                route  = self._route
                step_i = self._step_idx

            if step_i >= len(route.steps):
                continue

            current_step = route.steps[step_i]
            maneuver_lat, maneuver_lon = current_step.maneuver_loc

            # Distance to current maneuver point
            dist_to_maneuver = _hav_m(lat, lon, maneuver_lat, maneuver_lon)

            # Check arrival
            dest_step = route.steps[-1] if route.steps else None
            if dest_step:
                dest_lat, dest_lon = dest_step.maneuver_loc
                if _hav_m(lat, lon, dest_lat, dest_lon) < _ARRIVE_RADIUS_M:
                    self._on_arrived()
                    continue

            # Advance step when close to maneuver point
            if dist_to_maneuver < _STEP_ADVANCE_M and step_i < len(route.steps) - 1:
                with self._lock:
                    self._step_idx = step_i + 1
                    self._off_route = False
                    self._off_route_since = None
                logger.debug(f"Advanced to step {self._step_idx}: {route.steps[self._step_idx].instruction}")

            # Off-route detection: distance from full route geometry
            threshold = _OFF_ROUTE_M.get(self._profile, _OFF_ROUTE_DEFAULT)
            cross_d   = _cross_track_m(lat, lon, route.full_geometry)
            now = time.time()

            with self._lock:
                if cross_d > threshold:
                    if not self._off_route:
                        self._off_route = True
                        self._off_route_since = now
                        logger.info(f"Off route: {cross_d:.0f}m from path")
                    elif now - (self._off_route_since or now) > _REROUTE_DELAY_S:
                        self._rerouting = True
                else:
                    self._off_route = False
                    self._off_route_since = None

            if self._rerouting:
                self._trigger_reroute(lat, lon)

            self._emit_nav_state()

    def _on_arrived(self):
        with self._lock:
            self._route     = None
            self._step_idx  = 0
            self._off_route = False
        if self.socketio:
            try:
                self.socketio.emit("nav_arrived", {"message": "You have arrived"}, namespace="/")
            except Exception:
                pass
        logger.info("Navigation: arrived at destination")

    def _trigger_reroute(self, lat: float, lon: float):
        if not self.routing_node or self._dest_lat is None:
            with self._lock:
                self._rerouting = False
            return

        logger.info("Rerouting…")
        if self.socketio:
            try:
                self.socketio.emit("nav_rerouting", {}, namespace="/")
            except Exception:
                pass

        result = self.routing_node.route(lat, lon, self._dest_lat, self._dest_lon,
                                         profile=self._profile,
                                         waypoints=self._waypoints)
        if result and result.get("routes"):
            parsed = self._parse_osrm_route(result["routes"][0], self._profile, self._dest_name)
            if parsed:
                with self._lock:
                    self._route = parsed
                    self._step_idx = 0
                    self._off_route = False
                    self._off_route_since = None
                    self._rerouting = False
                self._emit_nav_state()
                logger.info("Reroute complete")
                return

        with self._lock:
            self._rerouting = False
        logger.warning("Reroute failed — continuing with current route")

    # ── Route parser ─────────────────────────────────────────────────────────

    def _parse_osrm_route(self, route_data: dict, profile: str, dest_name: str) -> Optional[ActiveRoute]:
        """Convert OSRM route JSON to our ActiveRoute dataclass."""
        try:
            legs     = route_data.get("legs", [])
            all_steps: List[RouteStep] = []
            full_coords = route_data.get("geometry", {}).get("coordinates", [])

            for leg in legs:
                for raw_step in leg.get("steps", []):
                    maneuver = raw_step.get("maneuver", {})
                    loc      = maneuver.get("location", [0, 0])   # [lon, lat]
                    road     = raw_step.get("name", "")
                    dist     = float(raw_step.get("distance", 0))
                    dur      = float(raw_step.get("duration", 0))
                    geo      = raw_step.get("geometry", {}).get("coordinates", [])

                    instruction = _build_instruction(maneuver, road, dist)
                    step = RouteStep(
                        instruction   = instruction,
                        distance      = dist,
                        duration      = dur,
                        maneuver_loc  = (loc[1], loc[0]),   # (lat, lon)
                        maneuver_type = maneuver.get("type", ""),
                        road_name     = road,
                        geometry      = geo,
                    )
                    all_steps.append(step)

            if not all_steps:
                return None

            return ActiveRoute(
                steps            = all_steps,
                full_geometry    = full_coords,
                total_distance   = float(route_data.get("distance", 0)),
                total_duration   = float(route_data.get("duration", 0)),
                profile          = profile,
                destination_name = dest_name,
            )
        except Exception as e:
            logger.error(f"Failed to parse OSRM route: {e}")
            return None

    # ── State builders ────────────────────────────────────────────────────────

    def _build_state(self) -> dict:
        route = self._route
        if not route:
            return {"active": False}

        step_i  = min(self._step_idx, len(route.steps) - 1)
        step    = route.steps[step_i]
        next_step = route.steps[step_i + 1] if step_i + 1 < len(route.steps) else None

        lat, lon = self._lat, self._lon
        dist_to_next = None
        if lat is not None and lon is not None:
            dist_to_next = _hav_m(lat, lon, *step.maneuver_loc)

        remaining_dist = route.remaining_distance(step_i)
        remaining_dur  = route.remaining_duration(step_i)
        eta_epoch      = time.time() + remaining_dur

        return {
            "active":           True,
            "profile":          route.profile,
            "destination":      route.destination_name,
            "step_index":       step_i,
            "total_steps":      len(route.steps),
            "instruction":      step.instruction,
            "road_name":        step.road_name,
            "distance_to_next": round(dist_to_next) if dist_to_next is not None else None,
            "next_instruction": next_step.instruction if next_step else None,
            "distance_remaining": round(remaining_dist),
            "duration_remaining": round(remaining_dur),
            "duration_str":     _format_duration(remaining_dur),
            "distance_str":     _format_distance(remaining_dist),
            "eta":              int(eta_epoch),
            "off_route":        self._off_route,
            "rerouting":        self._rerouting,
        }

    def _route_summary(self, route: ActiveRoute) -> dict:
        return {
            "total_distance": round(route.total_distance),
            "total_duration": round(route.total_duration),
            "distance_str":   _format_distance(route.total_distance),
            "duration_str":   _format_duration(route.total_duration),
            "steps":          [{"instruction": s.instruction,
                                "distance":    round(s.distance),
                                "duration":    round(s.duration),
                                "road_name":   s.road_name,
                                "maneuver":    s.maneuver_type,
                                "location":    list(s.maneuver_loc)}
                               for s in route.steps],
        }

    # ── Socket.IO events ──────────────────────────────────────────────────────

    def _emit_nav_state(self):
        if not self.socketio:
            return
        try:
            with self._lock:
                state = self._build_state()
            self.socketio.emit("nav_update", state, namespace="/")
        except Exception:
            pass

    def _emit_nav_cancelled(self):
        if not self.socketio:
            return
        try:
            self.socketio.emit("nav_cancelled", {}, namespace="/")
        except Exception:
            pass
