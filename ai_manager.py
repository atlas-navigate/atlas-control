"""
AI Manager — RAG + multi-chat + Ollama integration for Atlas Control.
Uses only Python stdlib. Import database as db inside methods to avoid circular imports.
"""
import contextlib
import json
import math
import re
import time
import threading
import logging
import urllib.request

import csv
import os as _os
import math as _math

# ---------------------------------------------------------------------------
# Offline reverse geocoder — two-tier:
#   1. US coordinates  → nearest ZIP code centroid (GeoNames US.txt, 41 k entries)
#      ZIP centroids are far denser than city centers and map directly to the
#      postal city name, giving near-perfect US accuracy.
#   2. Non-US coords   → gravity-weighted city lookup (cities5000.txt, 68 k cities)
# Both files live in data/ and are read once, then cached in memory.
# ---------------------------------------------------------------------------

_DATA_DIR  = _os.path.join(_os.path.dirname(__file__), "data")
_US_ZIP    = _os.path.join(_DATA_DIR, "US.txt")          # GeoNames US postal codes
_GEO_TSV   = _os.path.join(_DATA_DIR, "cities5000.txt")  # world cities w/ population

# US state abbreviation → full name
_US_STATE = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
    "CO":"Colorado","CT":"Connecticut","DE":"Delaware","FL":"Florida","GA":"Georgia",
    "HI":"Hawaii","ID":"Idaho","IL":"Illinois","IN":"Indiana","IA":"Iowa",
    "KS":"Kansas","KY":"Kentucky","LA":"Louisiana","ME":"Maine","MD":"Maryland",
    "MA":"Massachusetts","MI":"Michigan","MN":"Minnesota","MS":"Mississippi",
    "MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada","NH":"New Hampshire",
    "NJ":"New Jersey","NM":"New Mexico","NY":"New York","NC":"North Carolina",
    "ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon","PA":"Pennsylvania",
    "RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota","TN":"Tennessee",
    "TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia","WA":"Washington",
    "WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming","DC":"Washington DC",
    "PR":"Puerto Rico","GU":"Guam","VI":"Virgin Islands",
}

# Approximate US bounding box (covers contiguous US + AK + HI)
def _is_us_coords(lat, lon):
    return (17.0 <= lat <= 72.0) and (-180.0 <= lon <= -65.0)

# Military/installation ZIP city-name tokens that should be skipped in favour of
# the nearest civilian ZIP.  We only skip when a civilian option exists nearby.
_MILITARY_TOKENS = {
    "jbsa", "afb", "afs", "aaf", "nas", "naf", "nsb", "mcb", "mcas",
    "usag", "fgg", "wpafb", "langley", "quantico",
}

def _is_military_city(name: str) -> bool:
    tokens = name.lower().split()
    return any(t in _MILITARY_TOKENS for t in tokens)

# ── Loader: US ZIP centroids ────────────────────────────────────────────────
# GeoNames US.txt columns (tab-delimited):
#   0=CC  1=postal  2=place_name  3=state_name  4=state_code
#   5=county  6=county_code  7=  8=  9=lat  10=lon  11=accuracy

def _load_us_zips():
    if not _os.path.exists(_US_ZIP):
        return []
    rows = []
    try:
        with open(_US_ZIP, encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 11:
                    continue
                try:
                    rows.append((float(p[9]), float(p[10]), p[2], p[4]))
                    # (lat, lon, city_name, state_code)
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass
    return rows

# ── Loader: world cities (cities5000) ──────────────────────────────────────
# Columns: 0=id 1=name 4=lat 5=lon 8=CC 10=admin1_code 14=population

def _load_world_cities():
    if not _os.path.exists(_GEO_TSV):
        return []
    rows = []
    try:
        with open(_GEO_TSV, encoding="utf-8") as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) < 15:
                    continue
                try:
                    rows.append((float(p[4]), float(p[5]), p[1], p[10], p[8],
                                 int(p[14]) if p[14] else 0))
                    # (lat, lon, name, admin1_code, country_code, population)
                except (ValueError, IndexError):
                    pass
    except Exception:
        pass
    return rows

_US_ZIP_DATA    = None
_WORLD_GEO_DATA = None
_GEO_LOCK = threading.Lock()

def _ensure_loaded():
    global _US_ZIP_DATA, _WORLD_GEO_DATA
    with _GEO_LOCK:
        if _US_ZIP_DATA is None:
            _US_ZIP_DATA    = _load_us_zips()
        if _WORLD_GEO_DATA is None:
            _WORLD_GEO_DATA = _load_world_cities()

# ── Public API ──────────────────────────────────────────────────────────────

def _reverse_geocode(lat: float, lon: float) -> str | None:
    """Return 'City, State' for US coordinates or 'City, Country' elsewhere.

    US path: nearest ZIP centroid — 41 k entries gives exact postal city names
    (Herndon, Fairfax, Chesapeake, Fort Walton Beach, etc.).

    International path: gravity-weighted city from cities5000 so a nearby
    town of 24 k beats a distant capital of 700 k.
    """
    _ensure_loaded()

    cos_lat = _math.cos(_math.radians(lat))

    if _is_us_coords(lat, lon) and _US_ZIP_DATA:
        # Find nearest civilian ZIP first; also track nearest overall as fallback.
        best_civ_d2  = float("inf")
        best_civ     = None
        best_any_d2  = float("inf")
        best_any     = None
        for (rlat, rlon, city, state_code) in _US_ZIP_DATA:
            dlat = (lat - rlat) * 111.0
            dlon = (lon - rlon) * 111.0 * cos_lat
            d2   = dlat * dlat + dlon * dlon
            if d2 < best_any_d2:
                best_any_d2 = d2
                best_any    = (city, state_code)
            if not _is_military_city(city) and d2 < best_civ_d2:
                best_civ_d2 = d2
                best_civ    = (city, state_code)
        # Only trust the US ZIP result when the nearest ZIP centroid is within 50 km.
        # Beyond that (e.g. coordinates in Canada) fall through to world cities.
        _50km2 = 50.0 * 50.0
        if best_any_d2 > _50km2:
            pass  # fall through to world-cities lookup below
        else:
            # Prefer the nearest civilian ZIP; allow up to 20 km farther than the
            # raw nearest to avoid returning a military base name.
            best = best_civ if (best_civ and best_civ_d2 <= best_any_d2 + (20.0 * 20.0)) else best_any
            if best:
                city, sc = best
                state = _US_STATE.get(sc, sc)
                return f"{city}, {state}"

    # International (or US ZIP data missing) — gravity-weighted city
    if not _WORLD_GEO_DATA:
        return None

    best_score = -1.0
    best_near_d = float("inf")
    best_result = None
    best_near   = None

    for (rlat, rlon, name, admin1, cc, pop) in _WORLD_GEO_DATA:
        dlat_km = (lat - rlat) * 111.0
        dlon_km = (lon - rlon) * 111.0 * cos_lat
        dist_km = _math.sqrt(dlat_km * dlat_km + dlon_km * dlon_km)
        d2      = dlat_km * dlat_km + dlon_km * dlon_km

        score = (pop + 1) / max(dist_km, 1.0) ** 2
        if score > best_score:
            best_score  = score
            best_result = (name, admin1, cc)

        if d2 < best_near_d:
            best_near_d = d2
            best_near   = (name, admin1, cc)

    result = best_result or best_near
    if result is None:
        return None

    name, admin1, cc = result
    if cc == "US":
        state = _US_STATE.get(admin1, admin1)
        return f"{name}, {state}"
    return f"{name}, {cc}"

logger = logging.getLogger("ai_manager")

# ---------------------------------------------------------------------------
# Default settings
# ---------------------------------------------------------------------------
# AI settings defaults live in ONE place — database.AI_DEFAULTS — so the
# fresh-install defaults and these in-code fallbacks can never drift apart.
# Per-box values saved in the ai_settings table override these at runtime.
import database
from database import AI_DEFAULTS as DEFAULT_SETTINGS

# Safe math namespace shared across all calculator methods
import math as _math_mod

# ---------------------------------------------------------------------------
# Location-agent: keyword detection and coordinate extraction
# ---------------------------------------------------------------------------
# Query fragments that indicate a location-aware question — GPS context is
# injected when any of these appear in the user message.
_LOCATION_KW_FRAGMENTS = [
    "near me", "near here", "nearby", "nearest", "closest",
    "around here", "around me", "around us", "local area",
    "where am i", "where are we", "my location", "my position",
    "my coordinates", "my gps", "current location", "current position",
    "sunrise", "sunset", "solar noon", "golden hour",
    "moon phase", "moonrise", "moonset",
    "weather here", "weather at", "climate here",
    "elevation here", "altitude here", "terrain here",
    "navigate to", "navigation to", "direction to", "bearing to",
    "how far", "distance to", "distance from here",
    "plants here", "foraging here", "edible near", "water near",
    "what's around", "what is around",
]

# Regex to pull decimal-degree coordinate pairs from the user message.
# Handles: "37.5, -122.3" / "37.5°N 122.3°W" / "N37.5 W122.3" / "lat 37.5 lon -122.3"
_COORD_RE = re.compile(
    r"""
    (?:
        # NS prefix: N37.5 W122.3  or  N37.5, W122.3
        [Nn]\s*(?P<a_lat>\d{1,3}(?:\.\d+)?)\s*[°]?\s*[,;]?\s*[EeWw]?\s*(?P<a_lon>\d{1,3}(?:\.\d+)?)
      | # Signed decimal: 37.5, -122.3  or  -33.8, 151.2
        (?P<b_lat>[+-]?\d{1,3}(?:\.\d+)?)\s*[°]?\s*,\s*(?P<b_lon>[+-]?\d{1,3}(?:\.\d+)?)
      | # Keyword-prefixed: lat 37.5 lon -122.3  or  lat=37.5 lon=-122.3
        [Ll]at\s*[=:]?\s*(?P<c_lat>[+-]?\d{1,3}(?:\.\d+)?)\s*[,;]?\s*[Ll]on\s*[=:]?\s*(?P<c_lon>[+-]?\d{1,3}(?:\.\d+)?)
    )
    """,
    re.VERBOSE,
)

# Named-location phrases: "at Denver", "near Phoenix AZ", "to the Rocky Mountains"
# Captures 1–4 consecutive title-case words (proper nouns) after a location preposition.
_NAMED_LOC_RE = re.compile(
    r'\b(?:at|near|in|from|for|to)\s+(?:the\s+)?((?:[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}))',
)

def _extract_user_location(msg: str):
    """
    Attempt to extract an explicit location from the user's message.

    Returns one of:
      {"type": "coords", "lat": float, "lon": float, "raw": str}
      {"type": "named",  "name": str}
      None — no explicit location found
    """
    m = _COORD_RE.search(msg)
    if m:
        g = m.groupdict()
        if g.get("a_lat") and g.get("a_lon"):
            lat, lon = float(g["a_lat"]), -abs(float(g["a_lon"]))  # N+W convention
        elif g.get("b_lat") and g.get("b_lon"):
            lat, lon = float(g["b_lat"]), float(g["b_lon"])
        else:
            lat, lon = float(g["c_lat"]), float(g["c_lon"])
        # Sanity-check ranges
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"type": "coords", "lat": lat, "lon": lon, "raw": m.group(0).strip()}
    m = _NAMED_LOC_RE.search(msg)
    if m:
        name = m.group(1).strip().rstrip(",.")
        if len(name) >= 3:
            return {"type": "named", "name": name}
    return None

def _is_location_query(msg: str) -> bool:
    """Return True if the message appears to be location-sensitive."""
    ml = msg.lower()
    return any(kw in ml for kw in _LOCATION_KW_FRAGMENTS)

def _build_location_prefix(ctx_meta: dict) -> str:
    """
    Return a one-line location tag to prepend to the user message sent to the
    model, so the model sees the coordinates in the user turn and cannot ignore
    them.  Returns empty string when no GPS fix is available.
    """
    gps_fix = ctx_meta.get("gps_fix")
    user_loc = ctx_meta.get("user_location")
    parts = []
    if user_loc:
        if user_loc["type"] == "coords":
            parts.append(f"User location: {_fmt_coord(user_loc['lat'], user_loc['lon'])}")
        else:
            parts.append(f"User location: {user_loc['name']}")
    if gps_fix and gps_fix.get("latitude") is not None:
        lat, lon = gps_fix["latitude"], gps_fix["longitude"]
        tag = f"Device GPS: {_fmt_coord(lat, lon)}"
        city = _reverse_geocode(lat, lon)
        if city:
            tag += f" ({city})"
        alt = gps_fix.get("altitude")
        if alt is not None:
            tag += f" | alt {alt:.0f} m"
        sats = gps_fix.get("sats_in_view") or gps_fix.get("sats")
        if sats is not None:
            tag += f" | {sats} sats"
        parts.append(tag)
    if not parts:
        return ""
    return "[" + " | ".join(parts) + "]\n"

def _fmt_coord(lat: float, lon: float) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.5f}°{ns} {abs(lon):.5f}°{ew}"

# ---------------------------------------------------------------------------
# G1 ballistic drag model — piecewise-linear Cd table keyed by Mach number
# Values sourced from the Ingalls / Mayewski G1 drag function (standard reference)
# ---------------------------------------------------------------------------
_G1_MACH_CD = [
    # (Mach, Cd)
    (0.00, 0.092), (0.50, 0.092), (0.70, 0.092),
    (0.80, 0.100), (0.85, 0.116), (0.90, 0.135),
    (0.95, 0.195), (1.00, 0.390), (1.05, 0.475),
    (1.10, 0.455), (1.20, 0.425), (1.30, 0.400),
    (1.40, 0.378), (1.50, 0.358), (1.60, 0.342),
    (1.80, 0.322), (2.00, 0.308), (2.50, 0.286),
    (3.00, 0.268), (3.50, 0.255), (4.00, 0.245),
    (5.00, 0.230),
]

def _g1_cd(v_mps):
    """Interpolate G1 drag coefficient from velocity in m/s."""
    vs = 340.3  # speed of sound, sea level 15°C
    mach = v_mps / vs
    tbl = _G1_MACH_CD
    if mach <= tbl[0][0]:
        return tbl[0][1]
    if mach >= tbl[-1][0]:
        return tbl[-1][1]
    for i in range(len(tbl) - 1):
        m0, cd0 = tbl[i]
        m1, cd1 = tbl[i + 1]
        if m0 <= mach <= m1:
            t = (mach - m0) / (m1 - m0)
            return cd0 + t * (cd1 - cd0)
    return tbl[-1][1]

def _ballistic_sim(range_m, zero_m=100.0, v0_mps=975.0, bc_g1=0.269, dt=0.005):
    """
    Point-mass trajectory with G1 aerodynamic drag model.

    Returns (drop_cm, tof_s) at range_m relative to line of sight when zeroed at zero_m.
    drop_cm is negative when bullet is below line of sight.

    Args:
        range_m  : Target range in meters
        zero_m   : Zero distance in meters  (default 100)
        v0_mps   : Muzzle velocity in m/s   (default 975 m/s ≈ 3200 fps)
        bc_g1    : G1 ballistic coefficient  (default 0.269, 55gr .224 bullet)
        dt       : Integration time step (seconds; smaller = more accurate)
    """
    _g    = 9.80665
    rho   = 1.225                  # kg/m³ standard sea-level air density
    bc_si = bc_g1 * 703.07         # convert G1 lb/in² → kg/m²

    def _sim(elev_rad, target_x):
        """Simulate to target_x, return (y_m, tof_s)."""
        x, y, t    = 0.0, 0.0, 0.0
        vx         = v0_mps * _math_mod.cos(elev_rad)
        vy         = v0_mps * _math_mod.sin(elev_rad)
        px, py, pt = x, y, t
        while True:
            v = _math_mod.sqrt(vx * vx + vy * vy)
            if v < 1.0:
                return y, t        # bullet stalled
            cd = _g1_cd(v)
            k  = cd * rho / (2.0 * bc_si)
            ax = -k * v * vx
            ay = -_g - k * v * vy
            px, py, pt = x, y, t
            vx += ax * dt
            vy += ay * dt
            x  += vx * dt
            y  += vy * dt
            t  += dt
            if x >= target_x:
                if x != px:
                    frac = (target_x - px) / (x - px)
                    return py + frac * (y - py), pt + frac * (t - pt)
                return y, t

    # Bisection: find elevation angle that gives y=0 at zero_m
    lo = _math_mod.radians(-1.0)
    hi = _math_mod.radians(8.0)
    for _ in range(64):
        mid = (lo + hi) * 0.5
        y_z, _ = _sim(mid, zero_m)
        if y_z > 0.0:
            hi = mid
        else:
            lo = mid
    zero_elev = (lo + hi) * 0.5

    drop_m, tof_s = _sim(zero_elev, range_m)
    return round(drop_m * 100.0, 1), round(tof_s, 3)


def _ballistic_drop(range_m, zero_m=100.0, v0_mps=975.0, bc_g1=0.269, dt=0.005):
    """Drop in cm at range_m zeroed at zero_m (backward-compatible wrapper for CALC namespace)."""
    drop_cm, _ = _ballistic_sim(range_m, zero_m, v0_mps, bc_g1, dt)
    return drop_cm


def _miller_sg(weight_gr, diam_in, length_in, twist_in, v0_mps):
    """
    Miller gyroscopic stability factor (Don Miller, 2005).
    Formula: Sg = 30·m / (n² · d³ · L_cal · (1 + L_cal²)) × (v_fps/2800)^(1/3)
    where n = twist_in/diam_in (calibers per turn), L_cal = length_in/diam_in.
    Returns Sg (> 1.5 is stable; > 2.0 is well-stabilized for rifle bullets).
    """
    l_cal = length_in / diam_in           # bullet length in calibers
    n     = twist_in  / diam_in           # twist rate in calibers per turn
    sg    = (30.0 * weight_gr) / (n**2 * diam_in**3 * l_cal * (1.0 + l_cal**2))
    sg   *= (v0_mps * 3.28084 / 2800.0) ** (1.0 / 3.0)   # velocity correction
    return max(sg, 0.1)


def _mps_to_fps(v): return round(v * 3.28084, 1)
def _fps_to_mps(v): return round(v / 3.28084, 4)
def _cm_to_inches(v): return round(v / 2.54, 2)
def _m_to_yards(v):  return round(v * 1.09361, 2)
def _yards_to_m(v):  return round(v / 1.09361, 4)
def _km_to_miles(v): return round(v * 0.621371, 4)
def _miles_to_km(v): return round(v / 0.621371, 4)
def _lbs_to_kg(v):   return round(v * 0.453592, 4)
def _kg_to_lbs(v):   return round(v / 0.453592, 4)
def _c_to_f(v):      return round(v * 9/5 + 32, 2)
def _f_to_c(v):      return round((v - 32) * 5/9, 2)

_CALC_SAFE_NAMES = {
    "abs": abs, "round": round, "min": min, "max": max,
    "int": int, "float": float, "pow": pow,
    "sqrt": _math_mod.sqrt, "ceil": _math_mod.ceil, "floor": _math_mod.floor,
    "log": _math_mod.log, "log2": _math_mod.log2, "log10": _math_mod.log10,
    "exp": _math_mod.exp,
    "sin": _math_mod.sin, "cos": _math_mod.cos, "tan": _math_mod.tan,
    "asin": _math_mod.asin, "acos": _math_mod.acos, "atan": _math_mod.atan,
    "atan2": _math_mod.atan2,
    "sinh": _math_mod.sinh, "cosh": _math_mod.cosh, "tanh": _math_mod.tanh,
    "degrees": _math_mod.degrees, "radians": _math_mod.radians,
    "hypot": _math_mod.hypot, "factorial": _math_mod.factorial,
    "pi": _math_mod.pi, "e": _math_mod.e, "tau": _math_mod.tau, "inf": _math_mod.inf,
    # Physics constants
    "g": 9.80665,   # standard gravity m/s²
    "G": 6.674e-11, # gravitational constant
    "c": 299792458, # speed of light m/s
    # Ballistics
    "ballistic_drop": _ballistic_drop,   # (range_m, zero_m, v0_mps, bc_g1) → drop in cm
    # Unit conversions
    "mps_to_fps": _mps_to_fps, "fps_to_mps": _fps_to_mps,
    "cm_to_inches": _cm_to_inches,
    "m_to_yards": _m_to_yards, "yards_to_m": _yards_to_m,
    "km_to_miles": _km_to_miles, "miles_to_km": _miles_to_km,
    "lbs_to_kg": _lbs_to_kg, "kg_to_lbs": _kg_to_lbs,
    "c_to_f": _c_to_f, "f_to_c": _f_to_c,
}

# ---------------------------------------------------------------------------
# Common ammunition reference data.
# Physical bullet dimensions come from Sierra/Hornady/NATO published specs.
# Twist references are standard SAAMI/NATO barrel specs for each load.
# ---------------------------------------------------------------------------
_COMMON_ROUNDS = {
    # label: (v0_mps, bc_g1, description, weight_gr, diam_in, length_in, ref_twist_in)
    "5.56_55":   (975,  0.269, "5.56mm/.223 Rem 55gr FMJ (M193)",       55,  0.224, 0.910,  7.0),
    "5.56_62":   (930,  0.307, "5.56mm/.223 Rem 62gr FMJ (M855/SS109)", 62,  0.224, 0.990,  7.0),
    "5.56_77":   (884,  0.372, "5.56mm/.223 Rem 77gr OTM (Mk262)",      77,  0.224, 1.060,  8.0),
    "308_147":   (838,  0.412, ".308 Win/7.62x51 147gr FMJ (M80)",     147,  0.308, 1.140, 12.0),
    "308_168":   (820,  0.447, ".308 Win/7.62x51 168gr BTHP (M118LR)", 168,  0.308, 1.226, 10.0),
    "308_175":   (808,  0.505, ".308 Win/7.62x51 175gr BTHP",          175,  0.308, 1.240, 10.0),
    "300wm_190": (932,  0.560, ".300 Win Mag 190gr BTHP",              190,  0.308, 1.350, 10.0),
    "300wm_220": (884,  0.640, ".300 Win Mag 220gr BTHP",              220,  0.308, 1.450, 10.0),
    "65cm_140":  (869,  0.626, "6.5mm Creedmoor 140gr BTHP",           140,  0.264, 1.196,  8.0),
    "65cm_130":  (884,  0.583, "6.5mm Creedmoor 130gr BTHP",           130,  0.264, 1.150,  8.0),
    "338lm_250": (905,  0.587, ".338 Lapua Magnum 250gr BTHP",         250,  0.338, 1.590, 10.0),
    "338lm_300": (850,  0.730, ".338 Lapua Magnum 300gr BTHP",         300,  0.338, 1.750, 10.0),
    "50bmg_750": (895,  1.050, ".50 BMG 750gr APIT",                   750,  0.510, 4.180, 15.0),
    "9mm_115":   (370,  0.145, "9mm Luger 115gr FMJ",                  115,  0.355, 0.680, 16.0),
    "45acp_230": (259,  0.195, ".45 ACP 230gr FMJ",                    230,  0.452, 0.800, 16.0),
}

# Keyword fragments for identifying specific rounds in a query
_ROUND_HINTS = [
    # (keywords_that_must_all_match, round_key)
    (["5.56", "55"],          "5.56_55"),
    (["5.56", "62"],          "5.56_62"),
    (["5.56", "77"],          "5.56_77"),
    ([".223", "55"],          "5.56_55"),
    ([".223", "62"],          "5.56_62"),
    ([".308", "147"],         "308_147"),
    ([".308", "168"],         "308_168"),
    ([".308", "175"],         "308_175"),
    (["308", "168"],          "308_168"),
    (["7.62", "147"],         "308_147"),
    (["7.62", "168"],         "308_168"),
    (["300", "win", "190"],   "300wm_190"),
    (["300", "win", "220"],   "300wm_220"),
    (["6.5", "creedmoor"],    "65cm_140"),
    (["6.5", "140"],          "65cm_140"),
    (["338", "lapua", "250"], "338lm_250"),
    (["338", "lapua", "300"], "338lm_300"),
    (["50", "bmg"],           "50bmg_750"),
    ([".50", "bmg"],          "50bmg_750"),
    (["9mm", "115"],          "9mm_115"),
    (["45", "acp"],           "45acp_230"),
    # Fallback by caliber only
    (["5.56"],                "5.56_55"),
    ([".223"],                "5.56_55"),
    ([".308"],                "308_168"),
    (["7.62x51"],             "308_147"),
    (["6.5", "creed"],        "65cm_140"),
    (["338", "lapua"],        "338lm_250"),
]

# Keywords that specifically indicate a ballistics trajectory question
_BALLISTIC_SPECIFIC_KEYWORDS = {
    "zeroed", "zero at", "zero'd", "zero distance",
    "bullet drop", "elevation drop", "drop off", "holdover", "come-up",
    "moa", "mrad", "mil", "scope dial",
    "trajectory", "ballistic", "flight path",
    "5.56", ".223", ".308", "7.62", "6.5 creedmoor", ".338", ".50 bmg",
    "grain", "gr bullet", "gr round",
    "fps", "feet per second",
}

# Keywords that trigger injection of the [CALC:] tag fallback instruction
_MATH_KEYWORDS = {
    "calculat", "comput", "solv", "equation", "formula", "convert", "conversion",
    "how many", "how much", "how far", "how fast", "how long",
    "plus", "minus", "multipl", "divid", "add", "subtract", "total",
    "sum", "average", "percent", "%", "ratio", "proportion",
    "velocity", "acceleration", "force", "mass", "weight", "gravity",
    "energy", "momentum", "pressure", "density",
    "kinetic", "potential", "ballistic", "trajectory", "projectile",
    "muzzle", "bullet", "range", "drop", "drift",
    "sqrt", "square root", "log", "logarithm",
    "sin", "cos", "tan", "degree", "radian",
    "meter", "kilomet", "kilometer", "mile", "feet", "foot", "yard",
    "inch", "pound", "kilogram", "gram", "ounce", "liter", "gallon",
    "celsius", "fahrenheit", "kelvin", "mph", "kph",
    "distance", "speed", "time", "height", "altitude", "depth",
    "area", "volume", "circumference", "radius", "diameter",
}

# Keywords that trigger the full two-pass agent loop (extract → compute → answer)
_PHYSICS_KEYWORDS = {
    "ballistic", "trajectory", "projectile", "bullet drop", "bullet",
    "muzzle velocity", "muzzle energy", "flight time", "time of flight",
    "range", "maximum range", "elevation angle", "angle of elevation",
    "kinetic energy", "momentum", "velocity", "acceleration", "gravity",
    "force", "physics", "fps", "grain", "caliber", "rifle", "parabolic",
    "convert", "conversion", "celsius", "fahrenheit", "kelvin",
    "mph", "kph", "km/h", "m/s", "feet per second",
    "meter", "kilomet", "kilometer", "mile", "feet", "foot", "yard",
    "kilogram", "pound", "pound", "ounce", "gram",
    "distance", "speed", "height", "altitude", "depth",
}

_CALC_INSTRUCTION = (
    "\n\n=== CALCULATOR TOOL ===\n"
    "When you need a precise numerical result, write it as [CALC: expression] "
    "using Python math syntax. The system will replace it with the evaluated answer.\n"
    "Available: +, -, *, /, **, %, sqrt, sin, cos, tan, asin, acos, atan, atan2, "
    "log, log10, exp, degrees, radians, pi, e, g (9.80665), abs, round, hypot.\n"
    "Examples:\n"
    "  Range of projectile: [CALC: (100**2 * sin(2*radians(45))) / g]\n"
    "  km to miles: [CALC: 10 * 0.621371]\n"
    "  Kinetic energy: [CALC: 0.5 * 0.01 * 900**2]"
)

# Keywords that indicate the user is asking about live dashboard data —
# no RAG embed needed, the answer is already in the injected context.
_LIVE_DATA_KEYWORDS = {
    "temp", "temperature", "cpu", "gpu", "ram", "memory", "disk", "battery",
    "voltage", "power", "watt", "uptime", "node", "nodes", "online", "offline",
    "mesh", "signal", "snr", "rssi", "map", "position", "location", "gps",
    "lat", "lon", "altitude", "speed", "heading", "telemetry", "sensor",
    "humidity", "pressure", "channel", "message", "alert", "topology", "link",
    "hop", "hops", "relay", "router", "hardware", "model", "device", "stat",
    "stats", "status",
}

# ---------------------------------------------------------------------------
# Pre-seeded survival knowledge base
# ---------------------------------------------------------------------------
SURVIVAL_DOCS = [
    # ── WATER & FIRE ──────────────────────────────────────────────────────────
    {
        "title": "Water Purification in the Field",
        "tags": "water,survival,purification,boiling,bleach,iodine,filtration,SODIS,hygiene",
        "content": (
            "Priority: dehydration kills in 3 days. Treat ALL unknown water.\n\n"
            "METHODS:\n"
            "Boiling: rolling boil 1 min (3 min >6,500 ft). Kills bacteria, viruses, protozoa.\n"
            "Bleach (6–8.25% sodium hypochlorite, unscented):\n"
            "  Clear water: 8 drops/gal. Cloudy: 16 drops/gal. Wait 30 min.\n"
            "Iodine tablets: 1 tab/liter. Wait 30 min (60 min if cold or turbid).\n"
            "Filtration (Sawyer/LifeStraw): removes bacteria/protozoa — NOT viruses. Combine with chemical treatment.\n"
            "SODIS: clear PET bottle in direct sun 6 hrs (2 days overcast). Kills bacteria/viruses.\n\n"
            "COLLECTION:\n"
            "• Morning dew: wipe vegetation with cloth, wring into container\n"
            "• Rain catchment: tarps, buckets, gutters into barrels\n"
            "• Dry riverbed: dig 1–2 ft from bank to find subsurface water\n"
            "• Running > standing water. Avoid near dead animals.\n\n"
            "Improvised filter (pre-treatment only — always boil/treat after):\n"
            "Layer in container: gravel → sand → crushed wood charcoal.\n\n"
            "NEVER drink seawater (2× dehydration rate) or urine."
        ),
    },
    {
        "title": "Fire Starting Techniques",
        "tags": "fire,survival,warmth,friction,ferrocerium,tinder,bow drill,signaling,fire lay",
        "content": (
            "BUILD ORDER: dry tinder bundle → thumb-sized kindling → finger-sized sticks → wrist-sized logs.\n\n"
            "IGNITION METHODS:\n"
            "Ferrocerium rod: most reliable — scrape with 90° spine of knife, direct sparks onto tinder.\n"
            "Bow drill (friction): dry hardwood spindle + softwood fireboard. Notch = 1/8 into socket hole. "
            "Friction creates glowing coal; transfer to tinder bundle, blow gently until flame.\n"
            "Flint + high-carbon steel: strike at 30°, catch sparks in char cloth.\n\n"
            "TINDER (must be bone dry):\n"
            "• Dried grass, cattail fluff, shredded cedar bark, birch bark\n"
            "• Fatwood shavings (resin-soaked pine heartwood — burns wet)\n"
            "• Char cloth (cotton burned in sealed tin) — best spark catcher\n\n"
            "FIRE STRUCTURES:\n"
            "Teepee: fast heat, good for signaling. Log cabin: long steady burn, cooking.\n"
            "Star fire: logs pushed inward as they burn — minimal wood, long duration.\n\n"
            "WET CONDITIONS: dead wood under thick bark stays dry. Split wet wood to access dry interior.\n"
            "Birch bark burns even wet (contains oils).\n\n"
            "SIGNALING: 3 fires in triangle = international distress. "
            "Green vegetation = white smoke (day). Rubber/plastic = black smoke."
        ),
    },

    # ── SHELTER ──────────────────────────────────────────────────────────────
    {
        "title": "Emergency Shelter Construction",
        "tags": "shelter,survival,hypothermia,debris hut,lean-to,quinzhee,insulation,wilderness",
        "content": (
            "RULE: Ground conduction is the fastest heat-loss pathway in cold environments — "
            "a body loses heat to cold ground faster than to cold air. Insulate below before above.\n\n"
            "SITE: lee side of hill or dense trees. No drainage paths. No dead branches overhead. Near debris.\n\n"
            "DEBRIS HUT (best solo cold-weather):\n"
            "1. Ridgepole: 9 ft long, supported at 4 ft by forked branch\n"
            "2. Ribs: branches at 45° from ridgepole to ground\n"
            "3. Lattice: smaller branches across ribs\n"
            "4. Debris: pile leaves/needles minimum arm-length thick (2–3 ft)\n"
            "5. Interior floor: 2 ft of dry leaf litter\n"
            "Cavity: just wide enough for shoulders — smaller = warmer.\n\n"
            "LEAN-TO (quick, 3-season):\n"
            "Two uprights + crossbar at 4–5 ft. Lean branches at 45°. Layer bark top-to-bottom like shingles.\n"
            "Add back wall + side wings. Position facing a reflector fire.\n\n"
            "QUINZHEE (winter, snow):\n"
            "Pile snow 8 ft high. Let sinter 2 hrs. Insert sticks as thickness gauges (8 in). Hollow out. Vent hole required.\n\n"
            "PRIORITY ORDER: 1. Ground insulation  2. Windbreak  3. Rain protection  4. Heat retention"
        ),
    },

    # ── FOOD & FORAGING ──────────────────────────────────────────────────────
    {
        "title": "Wild Food Foraging Basics",
        "tags": "foraging,food,wild edibles,plants,insects,edibility test,wilderness,survival",
        "content": (
            "RULE: starvation takes weeks; poisoning kills in hours. When in doubt, don't eat it.\n\n"
            "UNIVERSAL EDIBILITY TEST (unknown plants — 24 hrs per part):\n"
            "1. Separate into parts (leaves, stems, roots)\n"
            "2. Smell: strong bitter or almond odor → reject\n"
            "3. Skin contact: rub on inner wrist 15 min → irritation = reject\n"
            "4. Lip test: touch to lip 3 min → burning/numbness = reject\n"
            "5. Tongue test: hold on tongue 15 min → same\n"
            "6. Chew small amount 15 min (do not swallow)\n"
            "7. Wait 8 hrs fasting. No reaction → eat 1 tablespoon, wait 8 hrs more.\n"
            "8. No reaction → safe to eat in quantity.\n\n"
            "RELIABLE NORTH AMERICAN EDIBLES:\n"
            "• Dandelion: entire plant — young leaves (salad), roots (roast/coffee), flowers\n"
            "• Cattail: pollen + green spike (spring), starchy rhizomes (year-round)\n"
            "• Pine (all species): inner bark edible raw/cooked; needles for vitamin C tea\n"
            "• Clover: leaves and flowers edible raw\n"
            "• Blackberry/raspberry: aggregate berry, readily identified\n\n"
            "INSECTS (high protein, low identification risk):\n"
            "Crickets, grasshoppers, grubs, earthworms — cook all (kills parasites).\n"
            "Avoid: brightly colored insects, hairy caterpillars, strong foul odor.\n\n"
            "AVOID: white milky sap; almond smell (cyanide); umbrella-shaped white flowers (hemlock family)."
        ),
    },

    # ── MEDICAL ──────────────────────────────────────────────────────────────
    {
        "title": "Wilderness First Aid: Primary Survey and Basic Care",
        "tags": "first aid,medical,ABCDE,bleeding,shock,fracture,burns,wilderness,primary survey",
        "content": (
            "PRIMARY SURVEY — ABCDE (treat life threats in order):\n"
            "A — Airway: jaw thrust or head-tilt chin-lift. Recovery position if unconscious+breathing.\n"
            "B — Breathing: look/listen/feel 10 sec.\n"
            "  If absent: begin CPR — 30 chest compressions then 2 rescue breaths (trained responders).\n"
            "  Untrained or solo: compression-only CPR (push hard and fast, 100–120/min, center of chest).\n"
            "C — Circulation: check pulse; control major bleeding (see hemorrhage doc).\n"
            "D — Disability: AVPU (Alert/Voice/Pain/Unresponsive). Pupils equal and reactive?\n"
            "E — Exposure: head-to-toe survey for hidden wounds. Prevent hypothermia.\n\n"
            "BLEEDING CONTROL (priority order):\n"
            "1. Direct pressure: 10+ min uninterrupted, pressure dressing.\n"
            "2. Tourniquet (extremity): 2–3 in above wound, tighten until bleeding stops, note time.\n"
            "3. Wound packing (junction wounds): pack hemostatic or clean cloth deep, hold 3 min.\n\n"
            "SHOCK: pale/cool/clammy, rapid weak pulse, confusion, fast breathing.\n"
            "Treat: stop bleeding → lay flat → legs elevated (NOT head/chest injury) → keep warm.\n\n"
            "FRACTURE: immobilize joint above and below break. SAM splint or sticks + padding.\n"
            "Check CSM (circulation, sensation, movement) distal to fracture before and after splinting.\n\n"
            "BURNS:\n"
            "• Cool with cool (not cold) running water 20 min\n"
            "• Do not pop blisters. Cover with clean non-stick dressing.\n"
            "• Rule of 9s: head=9%, each arm=9%, each leg=18%, torso front=18%, back=18%\n"
            ">20% body surface area = life-threatening; treat for shock."
        ),
    },

    # ── NAVIGATION ──────────────────────────────────────────────────────────
    {
        "title": "Land Navigation Without Electronics",
        "tags": "navigation,compass,map,declination,UTM,sun navigation,north star,pace count,orienteering",
        "content": (
            "MAP READING:\n"
            "Contours: closely spaced = steep. V pointing upstream = valley. V pointing downhill = ridge.\n"
            "Declination: difference between Grid North and Magnetic North. US range: −20° to +20°.\n"
            "UTM: 1 grid square = 1 km. Read right (easting) then up (northing). 6-digit = 100 m precision.\n\n"
            "COMPASS USE:\n"
            "Bearing: point direction-of-travel arrow at target, rotate bezel to align N with needle. Read bearing.\n"
            "Following: rotate body until needle aligns with N in bezel; walk toward DT arrow.\n"
            "Triangulation: take bearings to 2 known landmarks, plot lines on map — intersection = your position.\n\n"
            "SUN NAVIGATION (Northern Hemisphere):\n"
            "Solar noon: sun due south, shadow points true north.\n"
            "Shadow stick: mark tip, wait 15 min, mark again. Line between marks = east–west.\n"
            "Watch method: point 12 o'clock at sun; bisect angle between 12 and hour hand = south.\n\n"
            "STARS:\n"
            "North Star (Polaris): follow the two outer stars of the Big Dipper cup — points to Polaris. Within 1° of true north.\n"
            "Southern Cross: perpendicular from cross shaft to pointer star midpoint = south.\n\n"
            "PACE COUNT: know your paces per 100 m (average: ~65 double-paces). "
            "Use tally counter or pebble-to-pocket method to track distance."
        ),
    },

    # ── COMMUNICATIONS ───────────────────────────────────────────────────────
    {
        "title": "Meshtastic Off-Grid Communications",
        "tags": "meshtastic,lora,radio,mesh,channel,PSK,encryption,915mhz,range,off-grid,GPS",
        "content": (
            "Meshtastic = LoRa mesh radio for encrypted off-grid text + GPS. No license (ISM band, 915 MHz USA).\n\n"
            "HARDWARE:\n"
            "Freq: 915 MHz USA / 868 MHz EU / 433 MHz Asia.\n"
            "Range: 2–5 km urban; 10–30 km line-of-sight; 50+ km elevated repeater node.\n"
            "Power: 0.1–1 W TX. Battery life: days to weeks on 18650 cells.\n\n"
            "CHANNEL SECURITY:\n"
            "Default channel 0: shared key — readable by ALL Meshtastic users in range. Not private.\n"
            "Private channel: custom name + PSK (pre-shared key). Share key out-of-band only.\n"
            "Up to 8 simultaneous channels per node.\n\n"
            "BEST PRACTICES:\n"
            "• Minimize TX frequency (saves battery; reduces radio direction-finding exposure)\n"
            "• Hop limit default=3 (each packet relayed up to 3 hops). Increase for large networks.\n"
            "• Encrypted messages still expose: timestamp, node ID, traffic pattern\n"
            "• Use codenames on default/shared channels — not real names or call signs\n"
            "• Manual position instead of GPS share if location OPSEC matters\n"
            "• Repeater node: always-on node on high ground dramatically extends network\n\n"
            "PAIRS WITH: phone via BLE (app), PC via USB serial (web client).\n"
            "INTEGRATES WITH: ham radio APRS (position bridging), JS8Call (message bridging)."
        ),
    },

    # ── POWER ────────────────────────────────────────────────────────────────
    {
        "title": "Off-Grid Power and Battery Management",
        "tags": "power,solar,battery,12v,watt-hours,amp-hours,off-grid,charge controller,LiFePO4,sizing",
        "content": (
            "BATTERY MATH:\n"
            "Capacity: Wh = Ah × V  (100Ah × 12V = 1,200 Wh)\n"
            "Usable capacity: lead-acid = 50% DoD; LiFePO4 = 80–90% DoD.\n"
            "Runtime: hours = Wh_usable ÷ load_watts\n"
            "Example: 100Ah LiFePO4 × 12V × 85% = 1,020 Wh usable. At 50W load → 20.4 hrs.\n\n"
            "SOLAR SIZING:\n"
            "Daily need (Wh) = Σ (device_watts × hrs_per_day)\n"
            "Panel watts = daily_Wh ÷ peak_sun_hours (use 4 hrs typical USA)\n"
            "Example: 200 Wh/day ÷ 4 = 50W minimum. Add 25% margin → 65W panel.\n\n"
            "BATTERY TYPES:\n"
            "Lead-acid (AGM): cheap ($1/Wh), heavy, 300–500 cycles, 50% DoD.\n"
            "LiFePO4 lithium: $0.80–1.50/Wh, 1/3 weight, 2,000+ cycles, 80% DoD — best value long-term.\n"
            "18650 cells: 3.6V/cell, 2–3.5 Ah each. Series × parallel configs for voltage + capacity.\n\n"
            "CHARGE CONTROLLERS:\n"
            "PWM: ~75% efficient, cheap. MPPT: 93–97% efficient, required for series-wired panels.\n"
            "Size: amps = panel_W ÷ battery_V × 1.25  (200W ÷ 12V × 1.25 = 21A → use 30A controller)\n\n"
            "EFFICIENCY LOSSES:\n"
            "Inverter (DC→AC): 85–92% efficient. Use 12V DC devices directly to avoid 8–15% loss.\n"
            "Wire losses: use 10 AWG for runs under 10 ft at 20A; 8 AWG for longer runs."
        ),
    },

    # ── FOOD PRESERVATION ────────────────────────────────────────────────────
    {
        "title": "Food Preservation Without Refrigeration",
        "tags": "food,preservation,dehydration,canning,fermentation,salt,smoking,root cellar,storage",
        "content": (
            "DEHYDRATION:\n"
            "Temp: 130–160°F (meat); 125–135°F (vegetables/fruit).\n"
            "Sun drying: 2–3 days direct sun; cheesecloth cover (insects). Requires low humidity.\n"
            "Shelf life: 6–12 months properly stored. Jerky: 1–2 months at room temp.\n\n"
            "SALT CURING:\n"
            "Dry cure: 1 oz salt per pound of meat. Optional sugar + pink curing salt (sodium nitrite).\n"
            "Brine (wet cure): 1 cup (8 oz) non-iodized salt per 1 gallon water = ~6% brine by weight.\n"
            "  For a heavier 20% brine: ~3.5 cups salt per quart of water.\n"
            "  Submerge meat fully. Pork: 7 days per inch of thickness at 36–40°F.\n"
            "Effect: draws moisture out, creates hostile environment for bacteria.\n\n"
            "FERMENTATION:\n"
            "Sauerkraut: shred cabbage, add 2% salt by weight, pack tightly, submerge under brine, 1–4 weeks at 65–75°F.\n"
            "Pickles, kimchi, sourdough: same principle — salt + anaerobic environment.\n"
            "Shelf life: 6+ months once fermented. Adds probiotics.\n\n"
            "CANNING:\n"
            "Low-acid foods (meat, vegetables): MUST pressure can at 10–15 PSI (240°F) — kills Clostridium botulinum.\n"
            "High-acid (fruits, pickles, tomatoes + acid): water bath canning at 212°F is sufficient.\n"
            "WARNING: never water-bath low-acid foods — botulism risk is real and fatal.\n\n"
            "ROOT CELLARING: 32–40°F, 85–95% humidity. Potatoes/carrots/beets/cabbage last 4–8 months.\n\n"
            "SPOILAGE SIGNS: off smell, mold, slimy texture, swollen/hissing lids → discard immediately."
        ),
    },

    # ── SECURITY ────────────────────────────────────────────────────────────
    {
        "title": "Situational Awareness and Security",
        "tags": "security,awareness,OPSEC,Cooper color code,threat assessment,COMSEC,perimeter,group roles",
        "content": (
            "COOPER COLOR CODE:\n"
            "White — unaware (avoid in uncertain environments).\n"
            "Yellow — relaxed alertness, no specific threat (maintain as default).\n"
            "Orange — specific potential threat identified; have a plan ready.\n"
            "Red — threat confirmed; executing plan.\n\n"
            "BASELINE AWARENESS:\n"
            "• Learn what is normal for your environment\n"
            "• Deviations: out-of-place people/vehicles, sounds that stop abruptly\n"
            "• Trust your instincts; investigate anomalies before dismissing them\n\n"
            "PHYSICAL SECURITY LAYERS:\n"
            "1. Outer: early warning — tripwires with noise makers, remote cameras\n"
            "2. Inner: barriers — fencing, thorny hedges, motion lighting\n"
            "3. Strong point: hardened structure with comms, supplies, 360° coverage\n"
            "Establish rally point and fallback route — all members must know both.\n\n"
            "OPSEC (Operational Security):\n"
            "• Limit who knows your supplies, numbers, and plans\n"
            "• Ask: what does my daily pattern reveal about my capabilities?\n"
            "• Reduce light signature at night (blackout windows)\n\n"
            "COMSEC:\n"
            "Meshtastic default channel: readable by ALL nearby users. Private PSK for sensitive traffic.\n"
            "Minimize transmissions. No real names or locations on shared channels.\n\n"
            "GROUP STRUCTURE: pre-assign roles before crisis — Leader, Medic, Comms, Logistics, Security."
        ),
    },

    # ── BALLISTICS ───────────────────────────────────────────────────────────
    {
        "title": "Ballistics: MOA and MIL Angular Units",
        "tags": "ballistics,MOA,minute of angle,MIL,milliradian,angular units,subtension,scope,conversion",
        "content": (
            "MOA (Minute of Angle) = 1/60 degree = 0.000291 rad.\n"
            "Formula: size_in = 1.047 × (range_yd / 100)\n"
            "Shortcut: ~1 inch per 100 yards (within 5%).\n\n"
            "MOA SUBTENSION:\n"
            " 100 yd =  1.047 in |  600 yd =  6.282 in\n"
            " 200 yd =  2.094 in |  700 yd =  7.329 in\n"
            " 300 yd =  3.141 in |  800 yd =  8.376 in\n"
            " 400 yd =  4.188 in |  900 yd =  9.423 in\n"
            " 500 yd =  5.235 in | 1000 yd = 10.470 in\n\n"
            "Scope clicks (MOA): 1/4-MOA scope = 4 clicks/MOA. 1/8-MOA = 8 clicks/MOA.\n\n"
            "MIL (Milliradian) = 1/1000 radian = 0.001 rad.\n"
            "Formula metric:   size_cm = range_m / 10\n"
            "Formula imperial: size_in = range_yd × 0.036\n\n"
            "MIL SUBTENSION:\n"
            " 100 m =  10 cm |  100 yd =  3.60 in\n"
            " 300 m =  30 cm |  300 yd = 10.80 in\n"
            " 500 m =  50 cm |  500 yd = 18.00 in\n"
            " 700 m =  70 cm |  700 yd = 25.20 in\n"
            "1000 m = 100 cm | 1000 yd = 36.00 in\n\n"
            "Scope clicks (MIL): 0.1-MIL scope = 10 clicks/MIL.\n\n"
            "EXACT CONVERSION:\n"
            "1 MIL = 3.438 MOA  [derivation: 1 rad = 3437.75 MOA; ÷1000 = 3.438]\n"
            "1 MOA = 0.2909 MIL [= 1/3.438]\n\n"
            "QUICK TABLE:\n"
            " 0.5 MOA = 0.145 MIL |  0.1 MIL = 0.344 MOA\n"
            " 1.0 MOA = 0.291 MIL |  0.5 MIL = 1.719 MOA\n"
            " 2.0 MOA = 0.582 MIL |  1.0 MIL = 3.438 MOA\n"
            " 5.0 MOA = 1.454 MIL |  2.0 MIL = 6.876 MOA\n"
            "10.0 MOA = 2.909 MIL |  5.0 MIL = 17.19 MOA"
        ),
    },
    {
        "title": "Ballistics: Bullet Drop, TOF, and DOPE Card",
        "tags": "ballistics,bullet drop,TOF,time of flight,DOPE card,trajectory,308 Win,223 Rem,gravity,zero",
        "content": (
            "SCOPE: values in this doc are for .308 Win 168gr BTHP and .223 Rem 55gr at sea level only. "
            "Altitude, temperature, and different loads change all figures.\n\n"
            "PHYSICS: g = 386 in/s². Total fall from bore line: fall_in = 0.5 × 386 × TOF²\n"
            "Drop below LOS ≠ total fall (zero angle offsets part of it).\n"
            "With 100-yd zero: bullet crosses LOS at ~25 yd (rising) and 100 yd (falling).\n\n"
            "TOF — .308 Win 168gr BTHP, MV=792 m/s, G1 BC=0.47, sea level:\n"
            "  91 m: TOF=0.117 s, vel=774 m/s\n"
            " 183 m: TOF=0.243 s, vel=722 m/s\n"
            " 274 m: TOF=0.381 s, vel=674 m/s\n"
            " 366 m: TOF=0.521 s, vel=628 m/s\n"
            " 457 m: TOF=0.643 s, vel=582 m/s\n"
            " 640 m: TOF=0.967 s, vel=500 m/s\n"
            " 914 m: TOF=1.547 s, vel=396 m/s\n\n"
            "DOPE CARD — .308 Win 168gr 792 m/s, 100 m zero, sea level:\n"
            "Range  | Drop(cm) | MOA UP | 10mph wind | TOF\n"
            " 91 m  |    0.0   |  0.0   |  0.3 MOA   | 0.12 s\n"
            "183 m  |   -8.6   |  1.6   |  0.7 MOA   | 0.25 s\n"
            "274 m  |  -27.7   |  3.5   |  1.0 MOA   | 0.38 s\n"
            "366 m  |  -59.7   |  5.6   |  1.5 MOA   | 0.52 s\n"
            "457 m  | -106.2   |  8.0   |  2.2 MOA   | 0.64 s\n"
            "549 m  | -170.2   | 10.7   |  3.0 MOA   | 0.78 s\n"
            "640 m  | -255.3   | 13.8   |  3.8 MOA   | 0.97 s\n"
            "732 m  | -363.2   | 17.1   |  4.7 MOA   | 1.17 s\n"
            "914 m  | -675.6   | 25.4   |  6.5 MOA   | 1.55 s\n\n"
            ".223 Rem 55gr 988 m/s (unstable/transonic ~550–640 m):\n"
            "274 m: -19.8 cm / 2.5 MOA. 457 m: -63.5 cm / 4.8 MOA.\n\n"
            "ENERGY: KE (joules) = 0.5 × mass_kg × vel_mps²\n"
            ".308 168gr @ 792 m/s = 3,417 J. @ 396 m/s (914 m) = 854 J."
        ),
    },
    {
        "title": "Ballistics: Wind Drift Calculations",
        "tags": "ballistics,wind drift,wind correction,lag time,crosswind,wind formula,308 Win,MOA wind,MIL wind",
        "content": (
            "SCOPE: wind values below are for .308 Win 168gr BTHP, MV=2600fps, full 90° crosswind. "
            "Scale for other angles: 45°=×0.707, 30°=×0.500, 0°/180°=×0.000. "
            "Scale linearly for other wind speeds.\n\n"
            "WIND DRIFT FORMULA (lag-time method — verified correct):\n"
            "  lag_s   = TOF_s − (range_ft ÷ MV_fps)\n"
            "  Drift_in = wind_mph × 17.6 × lag_s        [17.6 in/s per mph]\n"
            "  Drift_MOA = Drift_in ÷ (range_yd/100 × 1.047)\n"
            "  Drift_MIL = Drift_in ÷ (range_yd × 0.036)\n\n"
            "WORKED EXAMPLE — .308 Win 168gr 2600fps, 500 yd, 10 mph full crosswind:\n"
            "  lag = 0.643 − (1500÷2600) = 0.643 − 0.577 = 0.066 s\n"
            "  Drift_in  = 10 × 17.6 × 0.066 = 11.6 in\n"
            "  Drift_MOA = 11.6 ÷ 5.235 = 2.22 MOA  ✓\n"
            "  Drift_MIL = 11.6 ÷ 18.0  = 0.64 MIL  ✓\n"
            "CRITICAL: lag (0.066 s) ≠ TOF (0.643 s). Lag is ~10× smaller than TOF.\n\n"
            "WIND TABLE — .308 Win 168gr 2600fps, 10 mph full crosswind:\n"
            "Range  | lag_s  | Drift_in | MOA   | MIL\n"
            "100 yd | 0.0017 |   0.3 in | 0.3   | 0.08\n"
            "300 yd | 0.046  |   8.1 in | 2.6   | 0.75\n"
            "500 yd | 0.066  |  11.6 in | 2.2   | 0.64\n"
            "700 yd | 0.100  |  17.6 in | 2.4   | 0.70\n"
            "1000yd | 0.185  |  32.6 in | 3.1   | 0.91\n\n"
            "WIND ANGLE FACTORS:\n"
            "90° (full value): × 1.000 | 45°: × 0.707 | 30°: × 0.500 | 0°/180°: × 0.000\n\n"
            "WIND SPEED FIELD ESTIMATE:\n"
            "Calm: smoke straight up. 3–5 mph: leaves rustle. 8–12 mph: branches move, dust raised.\n"
            "13–18 mph: small trees sway. Mirage boil = calm; mirage leaning = wind."
        ),
    },
    {
        "title": "Ballistics: Scope Click Adjustments and Ranging",
        "tags": "ballistics,scope,clicks,MOA click,MIL click,adjustment,ranging,elevation,windage,zero",
        "content": (
            "MOA SCOPE CLICK FORMULA:\n"
            "  MOA_needed = correction_in ÷ (range_yd / 100 × 1.047)\n"
            "  clicks = MOA_needed × 4   (1/4-MOA scope)\n"
            "  clicks = MOA_needed × 8   (1/8-MOA scope)\n\n"
            "MOA EXAMPLES (1/4-MOA scope):\n"
            "  6 in low @ 100 yd: 6÷1.047=5.73 MOA → ×4 = 23 clicks UP\n"
            "  6 in low @ 300 yd: 6÷3.141=1.91 MOA → ×4 =  8 clicks UP\n"
            " 12 in low @ 500 yd: 12÷5.235=2.29 MOA → ×4 =  9 clicks UP\n"
            " 20 in low @ 700 yd: 20÷7.329=2.73 MOA → ×4 = 11 clicks UP\n"
            " 36 in low @1000 yd: 36÷10.47=3.44 MOA → ×4 = 14 clicks UP\n\n"
            "MIL SCOPE CLICK FORMULA:\n"
            "  MIL_needed = correction_in ÷ (range_yd × 0.036)\n"
            "  OR metric:  MIL_needed = correction_cm ÷ (range_m ÷ 10)\n"
            "  clicks = MIL_needed × 10   (0.1-MIL scope)\n\n"
            "MIL EXAMPLES (0.1-MIL scope):\n"
            "  6 in low @ 100 yd: 6÷3.60 = 1.667 MIL → ×10 = 17 clicks UP\n"
            "  6 in low @ 300 yd: 6÷10.8 = 0.556 MIL → ×10 =  6 clicks UP\n"
            " 12 in low @ 500 yd: 12÷18.0= 0.667 MIL → ×10 =  7 clicks UP\n\n"
            "CROSS-CHECK: MOA × 0.2909 = MIL  (1.91×0.2909 = 0.556 ✓)\n\n"
            "RANGING WITH RETICLE:\n"
            "  MIL: Range_yd = target_height_in ÷ (MIL_reading × 0.036)\n"
            "  MOA: Range_yd = target_height_in × 95.5 ÷ MOA_reading\n"
            "Examples:\n"
            "  18 in target @ 0.5 MIL: 18÷(0.5×0.036)=18÷0.018=1000 yd\n"
            "  72 in target @ 2.0 MIL: 72÷(2.0×0.036)=72÷0.072=1000 yd\n"
            "  72 in target @ 4.0 MOA: 72×95.5÷4.0=1719 yd"
        ),
    },

    # ── FIREARMS ─────────────────────────────────────────────────────────────
    {
        "title": "Firearms: Action Types and Caliber Selection",
        "tags": "firearms,caliber,action,semi-auto,bolt action,lever action,shotgun,rifle,pistol,22LR,308,223,9mm",
        "content": (
            "FOUR SAFETY RULES (always active, no exceptions):\n"
            "1. Treat as loaded. 2. Never point at what you won't destroy.\n"
            "3. Finger off trigger until sights on target and decision to fire is made. 4. Know target and beyond.\n\n"
            "ACTION TYPES:\n"
            "Semi-auto (AR-15, AK-47, Glock): 1 pull = 1 round. High capacity, fast. Most parts/complexity.\n"
            "Bolt action (Rem 700): most accurate, fewest moving parts, slowest rate of fire.\n"
            "Lever action (Henry, Marlin 336): fast for trained users. No detachable magazine. Common: .30-30, .357, .44 Mag.\n"
            "Pump action (Mossberg 500): extremely reliable, versatile loads. Standard shotgun.\n"
            "Break action: single/double barrel. Simplest design, longest service life, fewest parts.\n\n"
            "CALIBER GUIDE:\n"
            ".22 LR: small game, training. 500 rds portable/cheap. Effective ~100 yd.\n"
            "9mm: standard pistol/carbine. 115–147 gr. High capacity, most available.\n"
            ".357 Mag: revolver + lever rifle — versatile, no magazines needed. Effective to 150 yd (rifle).\n"
            ".223 Rem / 5.56 NATO: AR-15. Effective 400–600 yd. 5.56 chamber accepts .223; not vice versa.\n"
            ".308 Win / 7.62 NATO: precision to 1,000+ yd. 500 rds ≈ 30 lbs.\n"
            "12 Gauge: 00 Buck (9 pellets, defense); slug (deer, 100 yd); birdshot (fowl).\n\n"
            "ENERGY FORMULA: E_ftlbs = (mass_gr × vel_fps²) ÷ 450,400\n"
            ".308 168gr @ 2600fps = 2,521 ft-lbs. .223 55gr @ 3240fps = 1,282 ft-lbs."
        ),
    },
    {
        "title": "Firearms: Malfunctions and Field Maintenance",
        "tags": "firearms,malfunction,jam,TRA,SPORTS,double feed,stovepipe,cleaning,lubrication,storage",
        "content": (
            "MALFUNCTION TYPES AND IMMEDIATE ACTION:\n"
            "Type 1 — Failure to Fire (trigger pull, no bang):\n"
            "  Cause: bad primer, empty mag, out-of-battery. Action: TAP-RACK-ASSESS.\n"
            "  TAP: slap magazine up firmly. RACK: cycle charging handle/slide fully. ASSESS: ready to fire?\n"
            "Type 2 — Stovepipe (empty case in ejection port):\n"
            "  Cause: weak extraction/ejection. Action: TAP-RACK-ASSESS (racking clears it).\n"
            "Type 3 — Double Feed (two rounds in chamber — most serious, ~5–8 sec to clear):\n"
            "  Lock bolt back → strip magazine → rack 2–3 times → fresh mag → rack → fire.\n"
            "  Use cover during clearing.\n"
            "AR-15 SPORTS: Slap (mag), Pull (CH), Observe (chamber), Release (bolt), Tap (fwd assist), Shoot.\n\n"
            "FIELD CLEANING (after every use and before storage):\n"
            "1. CLEAR: remove mag, lock bolt back, visually inspect chamber\n"
            "2. Bore: solvent patch → wait 5 min → dry patches until clean → one lightly oiled patch\n"
            "3. Bolt/slide: remove carbon from bolt face, extractor, feed ramp\n"
            "4. Lubricate: light film on all metal-to-metal surfaces (bolt lugs, rails, barrel hood)\n"
            "Dusty/sandy: minimal lube — excess oil traps grit causing Type 1/2 malfunctions.\n"
            "Arctic (<20°F): very light lube only — thick oils congeal and slow cycling.\n\n"
            "LONG-TERM STORAGE:\n"
            "• Cosmoline or grease coat on metal surfaces\n"
            "• Ammo sealed with silica gel → viable 10–20+ years\n"
            "• Below 50% relative humidity. Annual inspection.\n\n"
            "CRITICAL SPARE PARTS: extractor, extractor spring, firing pin, recoil spring."
        ),
    },

    # ── COLLAPSE SURVIVAL ────────────────────────────────────────────────────
    {
        "title": "Grid-Down: Immediate and Short-Term Survival (0–4 Weeks)",
        "tags": "grid-down,collapse,SHTF,72 hours,short-term,water,food,sanitation,power outage,priorities",
        "content": (
            "IMMEDIATE PRIORITIES (0–72 hours, strict order):\n"
            "1. Water: fill all containers and bathtubs now. WaterBOB = 100 gal in bathtub. Min 1 gal/person/day.\n"
            "2. Food: eat perishables first. Freezer stays frozen 48 hrs; fridge 4–6 hrs without power.\n"
            "3. Information: battery NOAA radio, ham radio, Meshtastic for situation status.\n"
            "4. Security: blackout windows at night. Account for all group members. Establish comms plan.\n\n"
            "SHORT-TERM (1–4 WEEKS):\n"
            "Water: rainwater catchment (roof gutters → 55-gal drums). Treat before drinking.\n"
            "Food rationing: 2,000 kcal/day rest; 3,500 kcal with hard labor. "
            "Inventory supplies; start at 75% normal intake.\n"
            "Sanitation: latrine 200+ ft from water sources. 6–8 in cat-holes minimum. "
            "Hand-washing with soap = #1 disease prevention.\n"
            "Comms: scheduled check-ins on Meshtastic or ham radio with key contacts.\n\n"
            "SUPPLY TIMELINE (no resupply):\n"
            "Days 1–3: fresh food + freezer contents\n"
            "Week 1–2: stored food + establish water system\n"
            "Week 2–4: rationing + sanitation + begin gardening (60–90 days to harvest)\n"
            "Month 1+: long-term planning required (see Long-Term Sustainability doc)\n\n"
            "BUG-OUT TRIGGER: if threat > benefit of staying, leave EARLY. Roads clog in 24–48 hrs."
        ),
    },
    {
        "title": "Grid-Down: Long-Term Sustainability Planning",
        "tags": "collapse,grid-down,long-term,food production,water infrastructure,energy,bug-out,skills,SHTF",
        "content": (
            "FOOD PRODUCTION (start immediately — 60–120 days to harvest):\n"
            "Family of 4 @ 2,500 kcal/day = 3.65M kcal/year ≈ 0.9 acres potatoes or 1.5 acres mixed.\n"
            "Priority crops: potatoes, beans, corn, squash, kale (fastest calories per area).\n"
            "Use open-pollinated/heirloom seeds only — hybrid F1 seeds don't reproduce true-to-type.\n\n"
            "WATER INFRASTRUCTURE (best to worst):\n"
            "1. Gravity-fed from elevated tank (zero energy)\n"
            "2. Hand pump from well\n"
            "3. Rain catchment: 1 in rain on 1,000 sq ft roof = ~600 gal\n"
            "4. Surface water with treatment pipeline\n\n"
            "ENERGY:\n"
            "400W solar + 200Ah LiFePO4 = comms, lighting, small loads covered.\n"
            "Wood heat: 3–5 cords/year for small home in cold climate. 1 cord ≈ 20 million BTU.\n\n"
            "URBAN vs RURAL TRADEOFFS:\n"
            "Urban: more initial resources, higher threat density, no food land, faster depletion.\n"
            "Rural: lower threats, land for food, well water possible, smaller community.\n\n"
            "BUG-OUT: leave early before roads clog (24–48 hr window). Pre-position supplies at destination.\n\n"
            "CRITICAL SKILLS TO DEVELOP NOW:\n"
            "Gardening + seed saving, water treatment, ham radio, food preservation, "
            "basic medicine, mechanical repair, security fundamentals."
        ),
    },
    {
        "title": "Barter Economics and Trade Goods in Collapse",
        "tags": "barter,trade,collapse,ammo,silver,junk silver,stockpile,trade security,precious metals,SHTF",
        "content": (
            "HIGH-VALUE BARTER GOODS (store extras beyond personal needs):\n"
            "Ammunition: most universally traded. Common calibers (.22LR, 9mm, .223, 12ga) trade best.\n"
            "Medications: antibiotics (amoxicillin, doxycycline, ciprofloxacin), OTC pain/antihistamines, wound care.\n"
            "Food: salt, sugar, coffee, alcohol (consumption + antiseptic). Tobacco trades even among non-users.\n"
            "Fuel: gasoline (Sta-Bil treated), diesel (PRI-D treated), propane (indefinite shelf life).\n"
            "Seeds: heirloom vegetable seeds — one packet = multiple growing seasons.\n"
            "Hygiene: soap, bleach, toothpaste, feminine hygiene.\n"
            "Tools: hand tools, nails/hardware, rope, tarps, knives, fire starters.\n\n"
            "PRECIOUS METALS:\n"
            "Silver (pre-1965 US coins = 90% silver): best for daily barter — small denominations, recognizable.\n"
            "1 silver dime historically = ~1 gallon of gas. Best single barter metal.\n"
            "Gold: too high-value per unit for daily transactions. Use for wealth transfer/large deals.\n"
            "Test: neither sticks to a magnet. Silver rings clear tone when dropped on hard surface.\n\n"
            "SKILLS AS BARTER: medical, mechanical, veterinary, electrical — infinite supply, can't be stolen.\n\n"
            "TRADE SECURITY:\n"
            "• Never trade at home — use neutral location\n"
            "• Never reveal stockpile size or cache location\n"
            "• Trade with a lookout present; vary meeting times\n"
            "• Start small; build trust before large transactions\n\n"
            "TIMELINE: Days 1–7: cash. Weeks 2–4: barter starts. Month 1–3: commodity money dominates."
        ),
    },
    {
        "title": "Community Defense and Mutual Aid Group (MAG) Organization",
        "tags": "community,defense,MAG,mutual aid,watch schedule,patrol,perimeter,SALUTE,governance,collapse",
        "content": (
            "MAG FORMATION:\n"
            "Ideal: 8–20 adults. Core roles: Leader, Deputy, Medic, Comms, Logistics, Security.\n"
            "Vetting criteria: skills, reliability, shared values, OPSEC commitment.\n"
            "Command: one person decides in emergencies — committees only for peacetime planning.\n\n"
            "LAYERED DEFENSE:\n"
            "Outer: observation posts (OPs) at key approaches. Tripwires with noise makers.\n"
            "Inner: physical barriers (fencing, thorny hedges), motion lighting.\n"
            "Strong point: one hardened structure with comms, supplies, 360° coverage.\n"
            "All members know: rally point, fallback routes, challenge-and-password.\n\n"
            "WATCH SCHEDULE:\n"
            "4-hour rotations (beyond 4 hrs = fatigue degrades watch quality).\n"
            "12-person group: 3/shift — 1 OP, 1 interior, 1 QRF (quick reaction force).\n"
            "Night (2–4 AM): highest risk window — assign most alert personnel.\n"
            "Challenge: approaching party states password. Guard NEVER responds to own challenge.\n\n"
            "PATROL: 2-man minimum, never solo. Vary routes and timing daily.\n"
            "SALUTE report: Size, Activity, Location, Uniform/Unit, Time, Equipment.\n\n"
            "THREAT LEVELS: 1-Unknown → challenge+monitor. 2-Hostile intent → withdraw to inner. 3-Attack → all defensive positions.\n\n"
            "SUPPLY CACHES: sealed waterproof containers (ammo cans, PVC pipe). "
            "Contents per cache: 3-day food/water, ammo, IFAK, radio, silver, documents. "
            "Location known only to leadership."
        ),
    },

    # ── AMATEUR RADIO ────────────────────────────────────────────────────────
    {
        "title": "Amateur Radio Grid-Down: Frequencies and Equipment",
        "tags": "ham radio,amateur radio,VHF,UHF,HF,NVIS,frequency,antenna,range,Technician,General,CB,emergency",
        "content": (
            "PACE PLAN: Primary(cell) → Alternate(VHF/UHF) → Contingency(HF) → Emergency(courier/signal).\n\n"
            "BAND SUMMARY:\n"
            "VHF 2m (144–148 MHz): HT to HT = 2–5 miles. With repeater: 20–100 miles.\n"
            "UHF 70cm (420–450 MHz): similar to VHF, better building penetration.\n"
            "HF 40m (7 MHz): 500–1,500 mile regional coverage, day and night.\n"
            "HF 80m (3.5 MHz): excellent regional night coverage.\n"
            "HF 20m (14 MHz): 1,000+ miles daytime.\n\n"
            "NVIS (Near Vertical Incidence Skywave):\n"
            "40m or 80m with low horizontal wire antenna (10–15 ft off ground).\n"
            "Covers 50–500 mile 'dead zone' that ground wave and line-of-sight can't reach.\n"
            "No repeater needed. Best for state-level or regional coordination.\n\n"
            "LICENSING:\n"
            "Technician (35 questions): VHF/UHF privileges. General: adds HF.\n"
            "Emergency exception (47 CFR 97.403): anyone may transmit on any freq if life is at risk.\n\n"
            "ANTENNA FORMULAS:\n"
            "Half-wave dipole: L_ft = 468 ÷ freq_MHz\n"
            "40m (7.2 MHz): 468÷7.2 = 65 ft total (32.5 ft each side)\n"
            "2m (146 MHz):  468÷146 = 3.2 ft total\n"
            "Wire dipole in a tree + coax to radio costs under $20.\n\n"
            "CB RADIO: Ch 9 (27.065 MHz) = emergency channel. No license. 5–15 mile range. In most vehicles."
        ),
    },
    {
        "title": "Amateur Radio Grid-Down: Digital Modes and Network Integration",
        "tags": "ham radio,JS8Call,APRS,Winlink,digital modes,meshtastic,NOAA,scanner,grid-down,off-grid comms",
        "content": (
            "DIGITAL MODES:\n"
            "JS8Call (HF): keyboard messaging at −24 dB (very weak signal). "
            "Store-and-forward: messages relay hop-by-hop across HF network. "
            "Works with Winlink for email over radio.\n"
            "Winlink: email over HF or VHF through RF gateway stations. "
            "Requires Winlink Express software + sound card interface (or hardware TNC).\n"
            "APRS (144.390 MHz USA): GPS position beaconing + short messages. "
            "Visible on aprs.fi online; works standalone with TNC offline.\n\n"
            "COMMS ARCHITECTURE (best practice):\n"
            "Local (0–50 km):   Meshtastic 915 MHz LoRa — encrypted, GPS, low power, no license\n"
            "Regional (50–500 km): VHF/UHF via repeater or NVIS HF — voice + digital\n"
            "Long-range (500+ km): HF (40m/20m) — JS8Call, Winlink, or voice\n\n"
            "PRE-PLAN WITH GROUP:\n"
            "• Primary HF check-in: frequency, mode, time (e.g., 7.200 MHz USB, 0800 + 2000 daily)\n"
            "• Meshtastic channel + PSK: share out-of-band before event\n"
            "• Winlink: pre-register email addresses; practice before needed\n\n"
            "PASSIVE MONITORING (no license needed):\n"
            "NOAA Weather Radio: 162.400–162.550 MHz — official emergency broadcasts.\n"
            "Scanner: program law enforcement, fire, EMS, FEMA frequencies. "
            "Passive intel requires no transmission and reveals no position."
        ),
    },

    # ── MEDICAL TRAUMA ───────────────────────────────────────────────────────
    {
        "title": "Field Trauma: Hemorrhage Control (Stop the Bleed)",
        "tags": "trauma,TCCC,tourniquet,CAT,wound packing,hemostatic,bleeding,Stop the Bleed,IFAK,QuikClot",
        "content": (
            "MASSIVE HEMORRHAGE IS PRIORITY 1 IN TACTICAL ENVIRONMENT — before airway.\n\n"
            "THREE METHODS IN ORDER:\n\n"
            "1. TOURNIQUET (extremity = arm or leg only):\n"
            "Commercial: CAT or SOFTT-W. Apply 2–3 in above wound (NOT over a joint).\n"
            "Tighten until bleeding completely stops — if it doesn't hurt, it may not be tight enough.\n"
            "Write application time on TQ or patient's skin. Safe up to 2–4 hrs.\n"
            "Improvised: minimum 2-inch-wide material (belt, cravat) + windlass stick.\n\n"
            "2. WOUND PACKING + PRESSURE (junctions: groin, armpit, neck — no TQ possible):\n"
            "Hemostatic gauze: QuikClot/Combat Gauze (kaolin) or Celox (chitosan).\n"
            "Pack DEEP into wound cavity — don't just cover the surface.\n"
            "Hold firm pressure 3–5 min (full body weight). DO NOT remove packed gauze.\n\n"
            "3. DIRECT PRESSURE (wounds not accessible by methods 1 or 2):\n"
            "Israeli bandage (Emergency Trauma Dressing). Press firmly 10+ min uninterrupted.\n\n"
            "IFAK MINIMUM CONTENTS:\n"
            "2× CAT tourniquet | 2× hemostatic gauze | 2× Israeli bandage\n"
            "2× vented chest seal | 1× 14ga 3.25-in needle | 1× NPA 28Fr + lube\n"
            "Trauma shears | 4× nitrile gloves | permanent marker (for TQ time)"
        ),
    },
    {
        "title": "Field Trauma: Airway, Chest Wounds, Shock, and Medical Supplies",
        "tags": "trauma,airway,chest seal,pneumothorax,needle decompression,shock,medications,antibiotics,TCCC",
        "content": (
            "AIRWAY:\n"
            "Unconscious + breathing: recovery position (lateral) — prevents aspiration.\n"
            "Jaw thrust (trauma): avoids neck hyperextension when spinal injury possible.\n"
            "NPA: insert lubricated nasopharyngeal airway to maintain open airway.\n\n"
            "PENETRATING CHEST WOUND:\n"
            "Seal immediately with vented chest seal (Halo/SAM) — out, not in.\n"
            "Improvised: petroleum jelly gauze taped on 3 sides (4th = flutter valve).\n\n"
            "TENSION PNEUMOTHORAX (air trapped in chest cavity — rapidly fatal):\n"
            "Signs: absent breath sounds one side, deviated trachea, cyanosis, deteriorating shock.\n"
            "If sealed wound gets worse: 'burp' the seal (lift one corner briefly).\n"
            "Needle decompression: 14ga × 3.25-in catheter at 2nd ICS, midclavicular line. "
            "Rush of air = confirmed. Leave catheter in, remove needle.\n\n"
            "HEMORRHAGIC SHOCK:\n"
            "Signs: pale/cool/clammy, weak rapid pulse, altered mental status.\n"
            "Treatment: stop bleeding → flat with legs elevated (NOT head/chest injury) → keep warm.\n"
            "Hypothermia + acidosis + coagulopathy = 'lethal triad' — prevent all three.\n\n"
            "MEDICATION STOCKPILE:\n"
            "Antibiotics: amoxicillin (general), doxycycline (tick/respiratory), "
            "ciprofloxacin (GI/UTI), metronidazole (anaerobic/dental).\n"
            "Pain: ibuprofen + acetaminophen together = multimodal analgesia.\n"
            "Epinephrine (EpiPen): anaphylaxis. Naloxone (Narcan): opioid reversal — OTC.\n"
            "Infection/sepsis: expanding redness + fever + rapid pulse = start antibiotics NOW."
        ),
    },

    # ── FOOD PRODUCTION ──────────────────────────────────────────────────────
    {
        "title": "Survival Gardening: Crops, Soil, and Caloric Planning",
        "tags": "gardening,agriculture,food production,calories,crops,heirloom seeds,soil,composting,irrigation",
        "content": (
            "CALORIC MATH:\n"
            "Adult: 2,000 kcal/day rest; 3,500+ kcal/day with hard labor.\n"
            "Family of 4 @ 2,500 kcal/day = 3.65 million kcal/year.\n"
            "Subsistence potato yield (no irrigation, well-managed): ~10,000–15,000 lbs/acre = ~3.5–5M kcal.\n"
            "One well-managed acre feeds a family of 4 for approximately 1 year.\n\n"
            "PRIORITY CROPS (calories + days to harvest):\n"
            "Potatoes: 70–120 days. Best calories/sq ft. Store 6+ months cool/dark/humid.\n"
            "Beans (dry): 50–70 days. Protein + nitrogen-fixing. Store 10+ years dry.\n"
            "Corn (dent): 70–100 days. High calorie, grind to flour. Plant in blocks for pollination.\n"
            "Squash/pumpkin: 90 days. Store 6–12 months. Large yield per plant.\n"
            "Kale/chard: 30–50 days. Cold-hardy to light frost. Fast repeat harvest.\n\n"
            "HEIRLOOM/OPEN-POLLINATED SEEDS: reproduce true-to-type for seed saving. "
            "Hybrid F1 seeds don't breed reliably — avoid for long-term use.\n"
            "Seed storage: sealed container + silica gel + cool/dark → viable 3–5 years.\n\n"
            "THREE SISTERS COMPANION PLANTING: Corn + Beans + Squash together. "
            "Corn = trellis; beans = nitrogen; squash = ground cover (moisture, weed suppression).\n\n"
            "SOIL: squeeze wet handful. Crumbles = sandy (add compost). Solid ball = clay (add sand+compost). "
            "Breaks slightly = loam (ideal). Compost: 2–3 months hot (weekly turning) or 6–12 months cold.\n\n"
            "RAIN IRRIGATION: 1 in rain on 1,000 sq ft = 600 gal. Gravity drip from elevated tank."
        ),
    },
    {
        "title": "Survival Livestock and Animal Husbandry",
        "tags": "livestock,chickens,rabbits,goats,eggs,meat,milk,animal husbandry,survival,small animals",
        "content": (
            "CHICKENS (easiest livestock):\n"
            "6 hens → ~5 eggs/day. Space: 2–4 sq ft/bird coop; 8–10 sq ft outdoor run.\n"
            "Dual-purpose breeds (Rhode Island Red, Barred Rock): eggs + meat.\n"
            "Feed: kitchen scraps + forage + grain supplement. Egg production drops in winter without 14 hrs light.\n\n"
            "RABBITS (most efficient meat per input):\n"
            "Breeding ratio: 1 buck per 4–6 does (not 1:1 — wasteful).\n"
            "1 productive doe → 80–150 lbs dressed meat/year (4–8 litters, 5–7 kits each at 4–5 lbs dressed).\n"
            "Gestation: 31 days. Wean at 6–8 weeks.\n"
            "Space: 30×36 in hutch/adult. Quiet, low odor — good for low-profile situations.\n"
            "Feed: unlimited timothy hay + garden scraps + pellets.\n\n"
            "GOATS (milk + meat + land clearance):\n"
            "Dairy doe: 1–2 gal/day (Nigerian Dwarf: 1 qt). Must freshen (breed) yearly.\n"
            "Always keep 2+ — social animals; single goats decline.\n"
            "Fencing: 4 ft woven wire + electric nose-level strand. They WILL escape otherwise.\n"
            "Feed: browse/hay + alfalfa (dairy) + loose minerals. Gestation: 150 days; twins common.\n\n"
            "PEST/DISEASE MANAGEMENT:\n"
            "Crop rotation: never same plant family in same bed two years running.\n"
            "Neem oil: organic pesticide + fungicide. Diatomaceous earth: kills crawling insects.\n"
            "Marigolds: companion plant that repels aphids and nematodes."
        ),
    },

    # ── VEHICLES & FUEL ──────────────────────────────────────────────────────
    {
        "title": "Fuel Storage and Vehicle Fuel Math",
        "tags": "fuel,gasoline,diesel,propane,storage,Sta-Bil,shelf life,generator,quantity,conservation",
        "content": (
            "FUEL SHELF LIFE:\n"
            "Gasoline untreated: 3–6 months. + Sta-Bil or PRI-G: 12–24 months.\n"
            "E10 (ethanol blend): absorbs water, phase-separates — use ethanol-free for long storage.\n"
            "Diesel: 1–2 years untreated. + PRI-D: up to 10 years. Add Biobor JF biocide (prevents algae).\n"
            "Diesel cold: gels below ~32°F — use winter blend or anti-gel additive.\n"
            "Propane: indefinite in sealed tanks. Most shelf-stable fuel.\n\n"
            "STORAGE: HDPE containers (red=gas, yellow=diesel). Ventilated area, away from ignition.\n\n"
            "QUANTITY MATH:\n"
            "Vehicle @ 20 mpg: 50 gal stored = 1,000 miles range.\n"
            "Vehicle @ 30 mpg: 50 gal = 1,500 miles.\n"
            "Diesel generator 5 kW at full load: ~0.6–0.7 gal/hr → 100 gal ≈ 140–165 hrs runtime.\n"
            "Same generator at 50% load (2.5 kW): ~0.4–0.5 gal/hr → 100 gal ≈ 200–250 hrs runtime.\n"
            "Propane heater 30,000 BTU: ~1.5 lb/hr → 20-lb tank ≈ 13 hrs continuous heat.\n\n"
            "GENERATOR SIZING:\n"
            "Running watts = sum of simultaneous loads.\n"
            "Starting watts = running watts × 1.5–3× for motor loads.\n"
            "3,500W handles: refrigerator + lights + phone charging.\n\n"
            "CONSERVATION: maintain tire pressure (+3% fuel use when under-inflated); "
            "remove excess weight (100 lb ≈ 1% worse mpg); minimize idling."
        ),
    },
    {
        "title": "Vehicle Maintenance and Off-Road Recovery",
        "tags": "vehicles,off-road,recovery,maintenance,winch,snatch strap,traction boards,field repair,tires",
        "content": (
            "CRITICAL SPARES TO CARRY:\n"
            "Serpentine belt, radiator hoses (upper+lower), 2 qts engine oil, coolant,\n"
            "tire plug kit + 12V compressor, full-size spare, jumper cables or jump pack.\n\n"
            "FIELD-EXPEDIENT REPAIRS:\n"
            "Radiator leak: K-Seal/Bar's Leak (temp). Improvised: raw egg white seals small cracks for 1 trip.\n"
            "Fuel line crack: self-amalgamating or high-temp silicone tape.\n"
            "Alternator failure: minimize electrical loads; typical battery = 20–60 min driving.\n"
            "Flat tire: plug kit handles 90% of punctures in 10 min at roadside.\n"
            "Head gasket (blown): milky oil + white exhaust smoke + coolant loss.\n\n"
            "OFF-ROAD RECOVERY:\n"
            "Air down: sand/mud = 15–18 psi; rock crawling = 8–12 psi. Re-inflate on firm ground.\n"
            "Kinetic snatch strap: attach to recovery points ONLY (never trailer hitch ball). "
            "Never stand in line with a loaded strap — catastrophic snatch block or hook failure.\n"
            "Hi-Lift jack: carry 12×12 in plywood base for soft ground. Doubles as improvised winch.\n"
            "Traction boards (MaxTrax): under spinning tire. Improvised: floor mats, branches, gravel.\n"
            "Winch: 1.5× vehicle GVW minimum. Tree saver strap (not chain). "
            "Snatch block doubles pulling force and allows direction change.\n\n"
            "MAINTENANCE SCHEDULE:\n"
            "Oil: 3,000–5,000 mi. Air filter: 12,000–15,000 mi (more in dust).\n"
            "Coolant flush: 2 years. Belts/hoses: inspect annually. Battery: test annually, replace at 5+ yrs."
        ),
    },

    # ── NATIVE TREES ─────────────────────────────────────────────────────────
    {
        "title": "Native Trees: Best Species by US Region",
        "tags": "trees,native plants,planting,wildlife,oak,hardwood,landscaping,ecology,US regions,forestry",
        "content": (
            "BEST NATIVE TREE BY US REGION\n\n"
            "Oaks (Quercus spp.) are the highest ecological value trees across most of the US — "
            "supporting more wildlife species than any other native tree genus.\n\n"

            "EASTERN US (Mid-Atlantic, Southeast, New England — VA, NC, TN, MD, NY, PA, etc.):\n"
            "Top choice: White Oak (Quercus alba)\n"
            "• Acorns drop annually (red oaks are biennial); low tannin = immediately palatable to wildlife.\n"
            "• Hosts 500+ Lepidoptera species — keystone food web support for birds.\n"
            "• 200–600+ year lifespan; rot-resistant timber; 60–100 ft canopy spread.\n"
            "• Tolerates poor, dry, rocky soils. Deep tap root — plant small, do not transplant large trees.\n"
            "• Acorn production begins at 20–50 years; full mast at 50+ years.\n"
            "Other top eastern natives: Chestnut Oak (rocky/dry slopes), Tulip Poplar (fast-growing hardwood), "
            "American Persimmon (wildlife fruit), Serviceberry (early bloom, bird berries), "
            "Black Gum/Tupelo (wet areas, fall color), Eastern Red Cedar (windbreak, wildlife cover).\n\n"

            "MIDWEST / GREAT PLAINS (OH, IN, IL, IA, MO, KS, NE, MN, WI, MI, etc.):\n"
            "Top choice: Bur Oak (Quercus macrocarpa)\n"
            "• Most drought- and fire-resistant oak; historically survived prairie fires due to thick corky bark.\n"
            "• Largest acorns of any North American oak — critical mast crop for deer, turkey, squirrel.\n"
            "• Extreme longevity (400+ years); tolerates clay soils and seasonal flooding.\n"
            "• The ecological equivalent of White Oak for the midwest and transition zones.\n"
            "Other top midwest natives: Shagbark Hickory (nut crop, wildlife), Hackberry (drought-tolerant, "
            "berry food), Eastern Cottonwood (fast riparian growth), Honey Locust (thorny wildlife cover).\n\n"

            "SOUTHEAST / GULF COAST (FL, GA, AL, MS, LA, TX Gulf, SC, etc.):\n"
            "Top choice: Longleaf Pine (Pinus palustris) for upland; Live Oak (Quercus virginiana) for general use.\n"
            "• Live Oak: massive spreading canopy, evergreen, extremely wind/salt tolerant, acorn wildlife value.\n"
            "• Longleaf Pine: keystone of southeastern savanna ecosystem; supports red-cockaded woodpecker "
            "and dozens of ground-layer species. Fire-adapted; extremely rot-resistant timber.\n"
            "Other top southeast natives: Bald Cypress (wet/flood areas), Swamp Chestnut Oak, "
            "American Holly (winter berries), Saw Palmetto (understory), Sweetbay Magnolia.\n\n"

            "PACIFIC NORTHWEST (WA, OR, northern CA coast):\n"
            "Top choice: Douglas Fir (Pseudotsuga menziesii)\n"
            "• Tallest and most productive timber tree in North America; dominant canopy species.\n"
            "• Seeds feed crossbills, chickadees, squirrels; dense structure shelters many species year-round.\n"
            "• Extremely adaptable from sea level to 5,000 ft; tolerates wet winters and dry summers.\n"
            "Other top PNW natives: Western Red Cedar (rot-resistant timber, wildlife cover), "
            "Big-Leaf Maple (fast broadleaf, wildlife), Red Alder (nitrogen-fixing, riparian), "
            "Oregon White Oak (only native oak; critical for acorn-dependent wildlife in PNW).\n\n"

            "CALIFORNIA / SOUTHWEST COAST (CA, including Central Valley and foothills):\n"
            "Top choice: Valley Oak (Quercus lobata)\n"
            "• Largest North American oak species; massive wildlife support.\n"
            "• Adapted to dry California summers once established; deep tap root reaches water table.\n"
            "• Acorns were the primary food staple of California indigenous peoples.\n"
            "Other top California natives: Blue Oak (dry foothill slopes), Coast Live Oak (coastal fog belt), "
            "California Buckeye (early bloom for native bees), Western Redbud (drought-tolerant, spring color).\n\n"

            "MOUNTAIN WEST / ROCKY MOUNTAINS (CO, UT, ID, MT, WY, NM highlands, AZ highlands):\n"
            "Top choice: Quaking Aspen (Populus tremuloides) for mid-elevation; "
            "Ponderosa Pine (Pinus ponderosa) for drier slopes.\n"
            "• Quaking Aspen: clonal colony growth; hosts 500+ insect species; fast establishment after disturbance.\n"
            "• Ponderosa Pine: most widely distributed pine in North America; open parkland habitat; "
            "seeds feed Clark's nutcracker, squirrels; fire-resistant mature bark.\n"
            "Other top mountain natives: Gambel Oak (SW, critical mast), Rocky Mountain Juniper (wildlife cover), "
            "Blue Spruce (CO/UT, wildlife structure), Narrowleaf Cottonwood (riparian corridors).\n\n"

            "ARID SOUTHWEST / DESERT (AZ desert, NM desert, TX west of Pecos, southern NV, southern UT):\n"
            "Top choice: Velvet Mesquite (Prosopis velutina) / Honey Mesquite (Prosopis glandulosa)\n"
            "• Extreme drought tolerance; deep tap root reaches water 100+ ft down.\n"
            "• Seed pods (beans) are high-calorie food for wildlife and humans (grind to flour).\n"
            "• Nitrogen-fixing; stabilizes soil; provides shade and cover in treeless desert.\n"
            "Other top desert natives: Desert Willow (riparian, hummingbirds), "
            "Arizona Sycamore (canyon streams), Emory Oak (Quercus emoryi — small SW oak, heavy acorn crop), "
            "Palo Verde (drought-deciduous, nitrogen-fixing).\n\n"

            "UNIVERSAL PLANTING PRINCIPLES:\n"
            "• Buy or collect locally sourced seed/saplings — local genotypes are adapted to local climate.\n"
            "• Plant in fall (most species) or early spring. Water first two summers during establishment.\n"
            "• Protect from deer browse for first 3–5 years (tube guards or wire cage).\n"
            "• Right tree, right place: match species to your soil type, drainage, and sun exposure.\n\n"

            "INVASIVE TREES TO AVOID PLANTING (all US regions):\n"
            "Tree of Heaven (Ailanthus altissima), Callery/Bradford Pear, Norway Maple, "
            "Princess Tree (Paulownia), Russian Olive, Mimosa/Silk Tree — "
            "all highly invasive, displace natives, provide low wildlife value."
        ),
    },

    # ── WILDLIFE ──────────────────────────────────────────────────────────────
    {
        "title": "US Wildlife: Large Predators — Bear, Mountain Lion, Wolf, Coyote",
        "tags": "wildlife,predator,bear,black bear,grizzly,mountain lion,cougar,wolf,coyote,attack,threat,encounter,animal,danger",
        "content": (
            "BLACK BEAR (Ursus americanus):\n"
            "Range: 40+ states; densest in Appalachians, Great Smoky Mtns, Pacific coast, Rockies, New England.\n"
            "Behavior: Omnivore; crepuscular; excellent climber; home range 15–80 sq mi (males larger).\n"
            "Seasonal: hyperphagia Aug–Oct (feeding 20 hrs/day); den Nov–Mar.\n"
            "Threat: LOW unless food-conditioned, defending cubs, or cornered. Most charges are bluffs.\n"
            "Sign: claw marks on trees 4–7 ft up, log-rolling, digging, scat containing berries/insects.\n\n"
            "GRIZZLY / BROWN BEAR (Ursus arctos):\n"
            "Range: AK statewide; Glacier NP (MT); Yellowstone region (WY/ID/MT); small N Idaho/WA population.\n"
            "ID: prominent shoulder hump, dished facial profile, short round ears, front claws 2–4 in.\n"
            "Threat: MODERATE-HIGH. Surprise encounters near cached kills or with cubs most dangerous.\n"
            "Home range: 100–1,500 sq mi. ALWAYS carry bear spray (7.9 oz min, ≥1% capsaicin) in grizzly country.\n\n"
            "MOUNTAIN LION / COUGAR (Puma concolor):\n"
            "Range: All western states; confirmed sightings spreading east through Great Plains; FL panther in S Florida.\n"
            "Behavior: Solitary; ambush predator; stalks prey from behind; crepuscular; territory 50–300 sq mi.\n"
            "Threat: LOW overall — most at-risk: children, runners, cyclists, small dogs (trigger chase instinct).\n"
            "Sign: round 3–3.5 in pug print (no claw marks), cached deer covered with debris, scrapes in dirt.\n\n"
            "GRAY WOLF (Canis lupus):\n"
            "Range: ID, MT, WY, MN, WI, MI, WA, OR; recovering — packs expanding into adjacent states.\n"
            "Behavior: Pack hunter (4–10); direct-register trot; very large territory (300–1,000 sq mi/pack).\n"
            "Threat: VERY LOW. Attacks on humans extremely rare; habituation to food/humans raises risk.\n"
            "Track: 4–5 in; large oval; straight-line travel pattern; distinguish from coyote by size.\n\n"
            "COYOTE (Canis latrans):\n"
            "Range: All 48 contiguous states; highly adaptable; urban populations widespread and growing.\n"
            "Behavior: Opportunistic omnivore; primarily crepuscular; solitary in urban, packs in rural areas.\n"
            "Threat: LOW. Rarely attacks adults; risk to small children and pets in suburban/periurban settings.\n"
            "Aggression peak: May–Jul (pup season, near dens). Eastern coyote larger than western; may interbreed with wolf.\n"
            "Haze — never run: make loud noise, wave arms, throw rocks; fleeing triggers predatory chase."
        ),
    },
    {
        "title": "US Wildlife: Venomous Snakes — Rattlesnakes, Copperhead, Cottonmouth, Coral Snake",
        "tags": "wildlife,snake,venomous,rattlesnake,copperhead,cottonmouth,water moccasin,coral snake,pit viper,snakebite,venom,danger,reptile",
        "content": (
            "~8,000 venomous snakebites/yr in US; ~5 fatalities. Pit vipers cause ~99% of bites.\n\n"
            "RATTLESNAKES (Crotalus & Sistrurus — 30+ US species):\n"
            "ID: broad triangular head, heat-sensing pit between eye and nostril, segmented rattle (juveniles may lack).\n"
            "Eastern Diamondback (Crotalus adamanteus) — SE US (FL/GA/SC/AL): largest NA venomous snake (up to 8 ft); potent hemotoxic venom; wet pine flatwoods, palmetto scrub.\n"
            "Western Diamondback (C. atrox) — TX/SW US: most bites in US; hemotoxic.\n"
            "Timber Rattlesnake (C. horridus) — E half of US (PA to TX): variable venom (hemotoxic + neurotoxic SE populations); rocky wooded hillsides, forest edges.\n"
            "Mojave Rattlesnake (C. scutulatus) — SW desert: MOST TOXIC US rattler; potent neurotoxin (Mojave A venom).\n"
            "Prairie Rattlesnake (C. viridis) — Great Plains to Rockies: grassland prairie dog towns.\n"
            "Sidewinder (C. cerastes) — Mojave/Sonoran desert: horns above eyes; distinctive sidewinding locomotion.\n"
            "Massasauga (Sistrurus catenatus) — Midwest/Great Lakes: small; wetlands; uncommon but present in NE Ohio, SW NY.\n\n"
            "COPPERHEAD (Agkistrodon contortrix):\n"
            "Range: Eastern US from southern MA/CT to TX; most frequent venomous snakebite species in US.\n"
            "Venom: hemotoxic, rarely fatal in healthy adults; painful necrotic wounds possible.\n"
            "Habitat: rocky hillsides, brush piles, leaf litter, old boards, suburban areas, near streams.\n"
            "ID: hourglass crossband pattern; copper-toned head; camouflage nearly perfect in leaf litter.\n"
            "Active: Apr–Oct; nocturnal in hot summer months; often near suburban homes in Mid-Atlantic/SE US.\n\n"
            "COTTONMOUTH / WATER MOCCASIN (Agkistrodon piscivorus):\n"
            "Range: SE US — VA coastal plain south through FL, west through TX; always associated with water.\n"
            "Venom: potent hemotoxic; significant tissue destruction. Defensive (stands ground + gapes white mouth).\n"
            "ID: thick-bodied; swims with head elevated above water surface; dark banding.\n\n"
            "CORAL SNAKE (Micrurus fulvius — Eastern; M. tener — Texas):\n"
            "Range: SE US — FL, GA, SC, NC (eastern); TX, LA, AR (Texas coral snake).\n"
            "Venom: NEUROTOXIC — most dangerous US venom; paralysis onset can delay 12–24 hrs (deceiving victim).\n"
            "ID: 'Red touches yellow, kill a fellow; red touches black, friend of Jack' (US species only).\n"
            "Behavior: secretive; most bites from handling or stepping on; small mouth limits envenomation.\n\n"
            "SNAKEBITE PROTOCOL:\n"
            "Keep calm; immobilize limb at heart level; remove constricting items; get to ER immediately.\n"
            "Poison Control: 1-800-222-1222.\n"
            "DO NOT: cut/suck wound, apply tourniquet, apply ice, use electric shock.\n"
            "Photograph snake if safe — do not handle even after decapitation (reflex bite for ~1 hr).\n"
            "Antivenom (CroFab for pit vipers; Pfizer coral antivenom — limited supply) is only treatment."
        ),
    },
    {
        "title": "US Wildlife: Venomous Arthropods — Spiders, Scorpions, Ticks, Stinging Insects",
        "tags": "wildlife,spider,black widow,brown recluse,scorpion,bark scorpion,tick,lyme disease,wasp,bee,africanized,sting,arthropod,insect,danger,venom",
        "content": (
            "SPIDERS:\n"
            "Black Widow (Latrodectus spp.):\n"
            "Range: All US; most common in South, Southwest, and West.\n"
            "ID: shiny black female (~15 mm body), red hourglass on abdomen. Females bite; males harmless.\n"
            "Venom: alpha-latrotoxin (neurotoxin); intense muscle cramping (abdomen, chest), diaphoresis, nausea, hypertension. Rarely fatal in healthy adults.\n"
            "Habitat: undisturbed areas — woodpiles, garages, outhouses, sheds, hollow stumps, low dense vegetation.\n"
            "Treatment: ice, pain management, seek ER; antivenom (Merck) for severe cases.\n\n"
            "Brown Recluse (Loxosceles reclusa):\n"
            "Range: South-central US (KS/MO/OK/TX/AL/TN core). NOT present on Pacific Coast or NE (frequently misidentified).\n"
            "ID: violin-shaped marking on cephalothorax; 6 eyes in 3 pairs; uniform tan-brown; ~10 mm body.\n"
            "Venom: dermonecrotic; expanding necrotic lesion possible over weeks; systemic reaction rare.\n"
            "Habitat: indoor — boxes, clothing piles, cluttered closets, basements. Shake shoes/clothing stored outside.\n\n"
            "SCORPIONS:\n"
            "Arizona Bark Scorpion (Centruroides sculpturatus): ONLY life-threatening US species.\n"
            "Range: AZ (statewide), NM, S Nevada, SE California, SW Utah; enters homes regularly.\n"
            "ID: straw/yellow color; thin pincers; slender tail; glows green-blue under UV light.\n"
            "Venom: neurotoxin — intense pain, numbness, blurred vision, muscle twitching; life-threatening for elderly/children/immunocompromised.\n"
            "Treatment: seek ER; Anascorp antivenom available at AZ hospitals. All other US scorpions: painful, not dangerous.\n\n"
            "TICKS (major disease vectors):\n"
            "Blacklegged / Deer Tick (Ixodes scapularis — NE, Midwest): PRIMARY Lyme disease vector; also babesiosis, anaplasmosis. Nymph stage (poppy-seed size) responsible for most transmission. High-risk zone: entire Northeast and upper Midwest.\n"
            "Western Blacklegged Tick (I. pacificus — Pacific Coast): Lyme vector West Coast.\n"
            "American Dog Tick (Dermacentor variabilis — East/Midwest): Rocky Mountain Spotted Fever (RMSF), tularemia.\n"
            "Lone Star Tick (Amblyomma americanum — SE/Midwest/expanding NE): STARI, ehrlichiosis, tularemia, alpha-gal syndrome (red meat allergy after bite).\n"
            "Rocky Mountain Wood Tick (D. andersoni — Rockies): RMSF, Colorado tick fever, tick paralysis.\n"
            "Removal: fine-tipped tweezers, grip at skin, straight steady pull, no twisting. Clean wound. Monitor 30 days for bull's-eye rash (Lyme) or fever (RMSF).\n\n"
            "STINGING INSECTS:\n"
            "Yellowjackets (Vespula spp.): most aggressive US stinger; ground-nesting; scavenge food (peak Aug–Oct).\n"
            "Bald-faced Hornet (Dolichovespula maculata): aerial paper nests; guard zone 3+ ft; painful sting.\n"
            "Africanized Honey Bees (SW US — AZ, NM, TX, SE CA, NV): same venom as European honeybee but pursue victims >1/4 mile; mobilize 1,000s from nest within seconds. Run straight to enclosed shelter; cover face; submerge in water only as last resort.\n"
            "Tarantula Hawk Wasp (Pepsis spp. — SW US): Schmidt Sting Pain Scale 4 (near maximum); solitary, not aggressive unless handled.\n"
            "Fire Ant (Solenopsis invicta — SE US, spreading): mass stinging attack; pustules; anaphylaxis risk in allergic individuals."
        ),
    },
    {
        "title": "US Wildlife: Dangerous Ungulates & Feral Hog",
        "tags": "wildlife,moose,elk,bison,deer,wild boar,feral hog,ungulate,rut,attack,threat,animal,danger",
        "content": (
            "MOOSE (Alces alces):\n"
            "Range: AK, ME/NH/VT/N NY, MN/WI (northern), Rockies (ID/MT/WY/CO/UT), Pacific NW.\n"
            "Threat: HIGH — more attacks on humans than bears in NA annually. Unpredictable, no warning charge possible.\n"
            "Danger seasons: Cows with calves May–Jul; Rut Sept–Oct (bulls charge anything, including vehicles).\n"
            "Warning signs: hackles raised, ears laid back, licking lips, head lowered, swaying gait.\n"
            "Speed: 35 mph; can swim; not deterred by water.\n"
            "Response: RUN and place large solid object (tree, vehicle, building) between you. Never stand ground.\n"
            "If knocked down: curl into ball, protect head/neck; stay still until moose disengages.\n\n"
            "ELK (Cervus canadensis):\n"
            "Range: Rocky Mtns, Pacific NW, Great Plains (reintroduced), Great Smoky Mtns (reintroduced).\n"
            "Threat: MODERATE. Bulls in rut (Aug–Nov) extremely dangerous — charge vehicles, people, structures. Cows aggressive near calves (May–Jul).\n"
            "Rut behavior: bugling, thrashing vegetation, wallowing in urine, mock charges escalating to contact.\n"
            "Note: #1 cause of visitor injury in Yellowstone NP. Maintain 25-yd minimum distance.\n\n"
            "BISON (Bison bison):\n"
            "Range: Yellowstone NP, Wind Cave NP, Badlands NP, Theodore Roosevelt NP; conservation herds in plains.\n"
            "Threat: MODERATE-HIGH. Deceptively docile appearance; sprint 35 mph, pivot instantly; gores and tosses.\n"
            "#2 cause of Yellowstone visitor injury. Minimum 25-yard distance required (NPS regulation).\n\n"
            "WHITE-TAILED DEER (Odocoileus virginianus):\n"
            "Range: All contiguous eastern US; expanding west; highest density in suburban NE and Mid-Atlantic.\n"
            "Direct threat: LOW. Indirect hazard: #1 vehicle strike wildlife (>1 million collisions/yr, ~200 deaths).\n"
            "Rut (Oct–Dec): bucks active day and night — peak vehicle collision period.\n"
            "Disease: Primary host for blacklegged tick (Lyme disease); Chronic Wasting Disease (CWD) spreading nationally.\n"
            "Buck aggression: Rare but documented — rutting bucks can be aggressive to humans; treat them as wild animals.\n\n"
            "FERAL HOG / WILD BOAR (Sus scrofa):\n"
            "Range: SE US core (TX, FL, GA, AL, SC) expanding to 35+ states; ~6–9 million animals nationally.\n"
            "Behavior: Highly destructive omnivore; primarily nocturnal; excellent swimmers; root and wallow extensively.\n"
            "Threat: MODERATE. Sows with piglets charge aggressively; wounded boar extremely dangerous; sharp tusks inflict severe lacerations.\n"
            "Disease hazard: brucellosis, pseudorabies, leptospirosis — wear nitrile/latex gloves when field dressing.\n"
            "Response: Climb a tree (cannot climb); if charged, step behind large tree or elevated obstacle.\n"
            "Invasive status: No closed season in most states; landowners encouraged to control populations."
        ),
    },
    {
        "title": "US Wildlife: Rabies Vectors & Disease-Carrying Mammals",
        "tags": "wildlife,rabies,bat,raccoon,skunk,fox,disease,hantavirus,plague,opossum,zoonotic,vector,mammal,infection",
        "content": (
            "RABIES — critical facts:\n"
            "Near 100% fatal once symptomatic. Post-Exposure Prophylaxis (PEP) is essentially 100% effective if given promptly.\n"
            "PEP: Wash wound vigorously with soap/water for 15 min. ER immediately for rabies immunoglobulin + vaccine series.\n"
            "ANY bite from bat, raccoon, skunk, or fox = treat as rabies exposure unless animal tested negative.\n\n"
            "PRIMARY RABIES VECTORS BY SPECIES:\n"
            "Bat (multiple species — Eptesicus, Myotis, Tadarida, etc.):\n"
            "Responsible for most US rabies deaths. Bat bites often unfelt (tiny puncture wounds).\n"
            "Rule: Any bat found in room with sleeping person = potential exposure; catch bat (thick gloves) for testing.\n"
            "Do NOT handle bats with bare hands. Little brown bat, big brown bat, Mexican free-tailed bat most common.\n\n"
            "Raccoon (Procyon lotor):\n"
            "Raccoon strain dominant in Eastern US. Daytime activity + staggering + vocalizing = likely rabid.\n"
            "Also vector for raccoon roundworm (Baylisascaris procyonis — can cause fatal larva migrans in humans).\n"
            "Healthy raccoons are bold but avoid direct contact; rabid ones are uncharacteristically aggressive or fearless.\n\n"
            "Skunk (Mephitis mephitis):\n"
            "Primary vector in south-central and north-central US. Nocturnal; daytime activity = sick animal.\n"
            "Spray range 10–15 ft; temporary blindness possible. Tomato juice/hydrogen peroxide + baking soda + dish soap for decontamination.\n\n"
            "Fox (Red: Vulpes vulpes; Gray: Urocyon cinereoargenteus):\n"
            "Unusually friendly, bold, or disoriented fox = potential rabies exposure. Rarely approach humans when healthy.\n\n"
            "Opossum (Didelphis virginiana):\n"
            "Body temperature (94–97°F) too low to sustain rabies virus — LOWEST rabies risk of any US mammal.\n"
            "Excellent tick predators — kill ~90% of ticks that attempt to feed on them.\n\n"
            "OTHER DISEASE VECTORS:\n"
            "Deer Mouse (Peromyscus maniculatus) — Hantavirus Pulmonary Syndrome:\n"
            "Highest risk: SW US, Rockies, Great Basin. Virus shed in droppings/urine/saliva.\n"
            "Avoid disturbing rodent nests/droppings; wear N95 + nitrile gloves; wet droppings with bleach before sweeping.\n"
            "Mortality ~38%; no antiviral treatment — supportive care only.\n\n"
            "Prairie Dog / Ground Squirrel (SW US) — Bubonic Plague (Yersinia pestis):\n"
            "Flea vector; die-offs of entire prairie dog colonies = active plague warning.\n"
            "Cases cluster in NM, CO, AZ, CA; wear DEET and avoid contact with dead rodents in these areas.\n\n"
            "Armadillo (Dasypus novemcinctus — SE US expanding north):\n"
            "Reservoir for Mycobacterium leprae (leprosy). Do not handle or consume; wear gloves.\n\n"
            "Beaver / Muskrat: tularemia via water or handling; always treat backcountry water sources."
        ),
    },
    {
        "title": "US Wildlife: Alligators, Crocodiles & Other Reptile Hazards",
        "tags": "wildlife,alligator,crocodile,reptile,snapping turtle,gila monster,attack,bite,SE US,Florida,danger,animal",
        "content": (
            "AMERICAN ALLIGATOR (Alligator mississippiensis):\n"
            "Range: SE US coastal plain — FL (most), GA, SC, NC, AL, MS, LA, TX, AR; fresh and brackish water.\n"
            "Size: males avg 11–15 ft, up to 1,000 lbs; females 6–9 ft. Wild population ~5 million.\n"
            "Thermal behavior: most active 82–92°F; lethargic below 55°F; sun-bask to regulate temperature.\n"
            "Feeding: ambush predator from water surface; death roll to dismember; drawn to splashing, dogs, fishing commotion.\n"
            "Threat: MODERATE. Mostly territorial/exploratory bites; ~5–6 fatalities per decade in FL.\n"
            "High-risk behaviors: swimming in alligator water at dawn/dusk/night; walking dogs at water's edge; feeding alligators.\n"
            "NEVER feed alligators — illegal in FL; food-conditioned alligators lose fear and must be euthanized.\n"
            "Female nest defense (May–Jul): VERY aggressive near mound nest; hissing + lunging = serious warning.\n"
            "Response to attack: fight back immediately — gouge eyes, strike snout/nostrils; do NOT freeze or play dead.\n"
            "If death-rolled: roll with the alligator to minimize tissue tearing; fight toward surface.\n"
            "On land: can sprint ~11 mph for short distance; humans easily outrun in straight line.\n\n"
            "AMERICAN CROCODILE (Crocodylus acutus):\n"
            "Range: Extreme S Florida only (Biscayne Bay, Florida Bay, Florida Keys); ~2,000 animals.\n"
            "ID: narrower V-shaped snout vs alligator's U-shape; lighter gray-green color; 4th tooth visible when mouth closed.\n"
            "Threat: LOW in US — much shyer than Old World crocodilians; attacks extremely rare.\n\n"
            "SNAPPING TURTLE:\n"
            "Common Snapper (Chelydra serpentina): throughout eastern US, Midwest, into Pacific NW.\n"
            "Alligator Snapping Turtle (Macrochelys temminckii): SE US rivers; up to 200 lbs; most powerful bite of any NA freshwater turtle.\n"
            "Threat: Do NOT attempt to pick up. Bite severs fingers; neck extends to bite BEHIND midpoint of shell.\n"
            "Nesting females on land: aggressive; give wide berth.\n\n"
            "GILA MONSTER (Heloderma suspectum):\n"
            "Range: SW US — AZ (most), S NV, SW UT, SE CA, W NM. Sonoran and Mojave desert.\n"
            "Only venomous lizard native to the US (excludes Mexican Beaded Lizard at extreme TX/Mexico border).\n"
            "Venom: neurotoxic; delivered by chewing (grooved teeth, not fangs); flows by capillary action.\n"
            "Bite: extremely tenacious grip — must be pried off; causes intense pain, low blood pressure, nausea.\n"
            "Threat: VERY LOW — slow-moving, secretive, non-aggressive; nearly all bites result from handling.\n"
            "Protected species in AZ and several other states. Do NOT handle.\n\n"
            "NON-VENOMOUS SNAKE MISIDENTIFICATION:\n"
            "Harmless water snakes (Nerodia spp.) frequently mistaken for cottonmouth; key difference: cottonmouth swims with head HIGH above water, has heat-sensing pit, white mouth lining.\n"
            "Milk snake (Lampropeltis triangulum) mimics coral snake — verify red/yellow/black band sequence."
        ),
    },
    {
        "title": "US Wildlife: Marine & Freshwater Hazards — Sharks, Jellyfish, Rays",
        "tags": "wildlife,shark,jellyfish,stingray,marine,ocean,coastal,freshwater,bull shark,great white,tiger shark,water hazard,aquatic",
        "content": (
            "SHARKS (US waters):\n"
            "Bull Shark (Carcharhinus leucas):\n"
            "MOST dangerous to US coastal visitors. Tolerates freshwater (confirmed in Mississippi R., FL rivers).\n"
            "Habitat: shallow (<6 ft) murky water, coastal inlets, river mouths, bays, surf zone.\n"
            "Range: Atlantic coast FL to NC; Gulf Coast; Pacific coast (rare); freshwater SE rivers.\n\n"
            "Great White Shark (Carcharodon carcharias):\n"
            "Pacific coast: N CA/OR near seal populations (Farallon Islands, Año Nuevo, Tomales Bay).\n"
            "Atlantic: MA to FL summer range; NC peak season (summer/fall).\n"
            "Behavior: investigative bite; rarely feeds on humans; surfers and divers near pinnipeds at highest risk.\n\n"
            "Tiger Shark (Galeocerdo cuvier):\n"
            "Hawaii — #1 cause of shark bites in HI; indiscriminate feeder; murky coastal waters, river mouths.\n"
            "SE Atlantic coast, Gulf of Mexico; active at dusk and night.\n\n"
            "Blacktip Shark (Carcharhinus limbatus):\n"
            "Responsible for majority of Florida shark bites; shallow water spinner and schooling behavior.\n"
            "Mistaken feeding bites (targets baitfish at feet/ankles); rarely causes serious injury.\n\n"
            "Nurse Shark (Ginglymostoma cirratum — Florida, Caribbean Keys):\n"
            "Docile but bites when provoked or stepped on; grip-and-hold biter; does not release easily.\n\n"
            "Shark risk reduction: Avoid dawn/dusk/night; no shiny jewelry or irregular splashing;\n"
            "stay out of water while bleeding; avoid near fishing/bait; stay away from harbor seals (CA).\n\n"
            "JELLYFISH:\n"
            "Portuguese Man-of-War (Physalia physalis) — Atlantic coast and Gulf:\n"
            "NOT a true jellyfish (siphonophore); tentacles 30–165 ft; stings after death/beaching.\n"
            "Intense burning pain; systemic reaction (muscle cramps, difficulty breathing) possible; rarely fatal.\n"
            "Treatment: remove tentacles with stick or card (NOT bare hands); rinse with seawater (not fresh for man-of-war); heat therapy 45°C.\n\n"
            "Sea Nettle (Chrysaora — Chesapeake Bay, Mid-Atlantic, Pacific): moderately painful; treat with vinegar.\n"
            "Moon Jellyfish (Aurelia — most coasts): mild sting; low clinical significance.\n\n"
            "STINGRAY:\n"
            "Range: Atlantic, Gulf, Pacific coasts; buried in sand in shallow warm water.\n"
            "Prevention: shuffle feet when wading to avoid stepping on buried rays; tail barb causes deep laceration.\n"
            "Treatment: HOT water immersion (110–115°F) denatures venom; remove superficial barb; ER for deep wounds/embedded barb.\n\n"
            "FRESHWATER HAZARDS:\n"
            "Naegleria fowleri (brain-eating amoeba): warm freshwater (>80°F) in SE US; fatal meningoencephalitis.\n"
            "Risk: submerging head in warm ponds/lakes/rivers in summer, particularly in FL/TX/SE states. Use nose clip.\n"
            "Snapping turtles: SE freshwater systems (see reptile doc).\n"
            "Cottonmouth: SE US rivers and swamps (see snake doc).\n"
            "All backcountry water: Giardia, Cryptosporidium, Leptospira in all US waterways — always treat/filter before drinking."
        ),
    },
    {
        "title": "US Wildlife: Animal Tracking, Sign & Activity Patterns",
        "tags": "wildlife,tracking,animal sign,tracks,scat,trail,animal movement,activity,crepuscular,nocturnal,seasonal,behavior,field craft",
        "content": (
            "TRACK IDENTIFICATION:\n"
            "Black Bear: human-like 5-toed; front 4 in wide, hind 7 in; pigeon-toed; claw marks often absent (semi-retractile).\n"
            "Grizzly Bear: similar to black bear; claw marks 2–4 in AHEAD of toe pads (diagnostic).\n"
            "Mountain Lion: round 3–3.5 in; 4 toes, NO claw marks (fully retractile); direct-register walk (hind in front print).\n"
            "Gray Wolf: 4–5 in oval; 4 toes + claw marks; direct-register trot; straight-line travel in pack.\n"
            "Coyote: 2–2.5 in; 4 toes + claw marks; similar to wolf but proportionally narrower; trotting gait.\n"
            "White-tailed Deer: split heart-shaped hoof, 2–3 in; dewclaws visible in mud/snow.\n"
            "Moose: split hoof 4–5 in, longer/pointer than deer; large dewclaws prominent.\n"
            "Raccoon: 5-fingered hand-like; front/rear very different; waddling paired-print gait.\n"
            "Wild Turkey: 3 forward toes + 1 rear; 3–5 in spread; drag marks from wing tips in snow/soft ground.\n"
            "Wild Boar: round split hoof; dewclaws set wide and low (contact ground routinely, unlike deer).\n\n"
            "SCAT IDENTIFICATION:\n"
            "Bear: large, tubular, 1.5–2 in diameter; berries/seeds (summer/fall), grass (spring), insect casings, bone fragments, corn.\n"
            "Mountain Lion: segmented, 1 in diameter; often buried near cached kill; fur and bone fragments.\n"
            "Coyote: tapered ends, twisted; hair, berries, seeds, bone fragments; deposited conspicuously on trails/rocks.\n"
            "Deer: oval pellets, 1/2 in, grouped in pile; dark green when fresh.\n"
            "Wild Boar: cylindrical, large, often with acorns, nuts, roots.\n\n"
            "OTHER SIGN:\n"
            "Bear rubs: trees with bark rubbed away + embedded hair 4–7 ft up; claw marks above rub.\n"
            "Bear digging: overturned logs and soil for grubs, ants, and rodents.\n"
            "Elk rubs: bark shredded on 2–6 in diameter saplings (antler velvet removal Aug–Oct); wallows = depressions in mud with strong urine scent.\n"
            "Beaver: conical gnawed stumps at water's edge; dams; mud-and-stick lodges; bank burrows.\n"
            "Wild boar rooting: extensive soil disturbance; wallows (mud pits); fence rubs.\n\n"
            "ACTIVITY PATTERNS:\n"
            "Crepuscular (most active at dawn and dusk): white-tailed deer, elk, moose, black bear, coyote, cottontail rabbit, most game birds.\n"
            "Nocturnal: raccoon, opossum, most bat species, flying squirrel, many rodents, copperhead (summer heat).\n"
            "Diurnal: tree squirrels, chipmunks, most songbirds, raptors, most lizards.\n\n"
            "SEASONAL MOVEMENT CUES:\n"
            "Spring (Mar–May): Bears emerge from dens; lean and foraging aggressively; snake emergence.\n"
            "Summer (Jun–Aug): Fawning (avoid approaching bedded fawns); peak snake activity; tick peak (nymphal blacklegged tick).\n"
            "Fall (Sept–Nov): Hyperphagia — bears feed 20 hrs/day; ungulate rut begins (Sept elk, Oct–Dec deer/moose); vehicle collision peak.\n"
            "Winter (Dec–Feb): Bears in dens (do not disturb); reduced activity; tracks easy to find in snow.\n\n"
            "WATER SOURCES: Most wildlife converges at water at dawn/dusk — highest activity node in any terrain.\n"
            "Travel routes follow drainages, saddles between ridgelines, and edges between habitat types."
        ),
    },
    {
        "title": "US Wildlife: Encounter Protocols & Bite/Sting First Aid",
        "tags": "wildlife,encounter,first aid,bear attack,mountain lion attack,snake bite,spider bite,tick removal,bee sting,alligator,moose attack,response,safety,protocol",
        "content": (
            "BEAR ENCOUNTERS:\n"
            "All bears — general: speak calmly; avoid direct eye contact (don't stare); back away slowly; do NOT run.\n"
            "Bear spray: most effective deterrent; 7.9 oz min; 1–2% capsaicin; deploy at 30–60 ft range; aim at face.\n\n"
            "Black Bear — predatory attack (stalking, follows you, night, sustained): FIGHT BACK. Target eyes, nose, muzzle.\n"
            "Black Bear — defensive (surprise/cubs): Stand ground, appear large, yell; fight back if contact made.\n\n"
            "Grizzly Bear — surprise/defensive: PLAY DEAD. Face down; interlock hands over back of neck; legs spread wide (harder to flip); stay flat until bear leaves and several minutes after.\n"
            "Grizzly Bear — predatory (night, prolonged, follows you into tent): FIGHT BACK. Target eyes and nose.\n\n"
            "MOUNTAIN LION:\n"
            "Do NOT run. Stop; face the animal; make yourself LARGE (raise arms, open jacket, lift children).\n"
            "Maintain eye contact; speak firmly and loudly; back away slowly.\n"
            "If attacked: FIGHT BACK aggressively — eyes, nose, throat. Do NOT play dead.\n\n"
            "MOOSE:\n"
            "RUN — place large solid object (large tree, car, building) between you. Moose won't pursue around obstacles.\n"
            "If knocked down: curl into ball, protect head and neck with arms; remain still until moose leaves.\n\n"
            "COYOTE:\n"
            "Haze loudly — clap, shout, throw rocks; do NOT run. Hazing reinforces fear of humans.\n"
            "Pick up small children and pets immediately.\n\n"
            "ALLIGATOR ATTACK:\n"
            "Fight back immediately — gouge eyes, strike snout/nostrils. They respond to pain.\n"
            "If death-rolled: roll WITH the alligator; do not resist rotation.\n"
            "Once free: run in straight line (not zigzag — alligators are slow on land beyond 50 ft).\n\n"
            "SNAKEBITE FIRST AID:\n"
            "Keep victim calm; immobilize bitten limb at heart level; remove rings/watches/tight clothing.\n"
            "Get to ER as fast as possible. Call Poison Control: 1-800-222-1222.\n"
            "DO NOT: cut/suck wound; tourniquet; ice; electric shock; herbal remedies.\n"
            "Antivenom (CroFab/Anavip for pit vipers) is the ONLY effective treatment. Time-critical.\n\n"
            "SPIDER/SCORPION STING:\n"
            "Black widow: ice pack, OTC pain relief; seek ER for muscle cramping, chest tightness, diaphoresis.\n"
            "Brown recluse: ice; elevate; wound care; see physician — necrotic wounds may require specialist care.\n"
            "Bark scorpion (AZ): seek ER immediately; Anascorp antivenom available. Critical for elderly/children.\n\n"
            "TICK REMOVAL:\n"
            "Fine-tipped tweezers; grip at skin surface (as close to skin as possible); straight steady upward pull — do NOT twist, crush, or apply heat/petroleum jelly.\n"
            "Clean with isopropyl alcohol; wash hands. Save tick in sealed bag for testing.\n"
            "Monitor 30 days: expanding bull's-eye rash = Lyme (seek doxycycline within 72 hrs); fever + headache + rash = RMSF (seek immediate care).\n\n"
            "BEE/WASP SWARM:\n"
            "Run in straight line to enclosed shelter (car, building); cover face with hands/shirt.\n"
            "Do NOT jump into water — bees will wait at surface.\n"
            "Remove stingers by scraping (do NOT pinch venom sac). Use epinephrine auto-injector (EpiPen) for anaphylaxis.\n"
            "Call 911 for: throat swelling, difficulty breathing, drop in blood pressure, unconsciousness."
        ),
    },
    {
        "title": "US Wildlife: Regional Species Distribution by US Region",
        "tags": "wildlife,regional,species,distribution,northeast,southeast,midwest,southwest,rocky mountains,pacific coast,alaska,Virginia,Mid-Atlantic,local wildlife,what animals are near me",
        "content": (
            "NORTHEAST (ME, NH, VT, MA, RI, CT, NY, PA, NJ, DE, MD):\n"
            "Predators: Black bear (all states, expanding suburban); no grizzly; mountain lion officially absent but confirmed sightings increasing in PA/NY.\n"
            "Venomous snakes: Timber rattlesnake (rocky wooded hillsides, all NE states); copperhead (southern NE states, PA, NJ, MD — most common venomous snakebite species); massasauga rattlesnake (SW NY, W PA — rare).\n"
            "Ticks: Blacklegged tick — ENTIRE NORTHEAST is high-risk Lyme zone; peak nymph activity May–Jul.\n"
            "Spiders: Black widow (uncommon but present, especially NJ south).\n"
            "Large game: Moose (ME, NH, VT, northern NY — declining due to winter tick); white-tailed deer (all states, very high suburban density).\n"
            "Marine: Bull shark (possible summer, Atlantic coast); Portuguese man-of-war (late summer/fall drift); bluefish/sand shark near shore.\n\n"
            "MID-ATLANTIC / VIRGINIA & SURROUNDING (VA, WV, MD, DC, NC):\n"
            "Black bear: Throughout Appalachian region; Shenandoah NP/Blue Ridge; suburban sightings increasing in NoVA.\n"
            "Copperhead: MOST encountered venomous snake in VA — rocky slopes, forest edges, leaf litter near suburban areas. Peak bites Aug–Sept. Northern VA (Fairfax/Loudoun/Prince William) high-encounter area.\n"
            "Timber rattlesnake: Western VA mountains and Shenandoah NV rocky ridgelines.\n"
            "Eastern cottonmouth: SE Virginia (Suffolk, Chesapeake, Hampton Roads area) and coastal plain waterways.\n"
            "Blacklegged tick: Extremely high Lyme disease risk throughout VA; Lone Star tick increasing (SE VA).\n"
            "Coyote: Established throughout VA; Eastern coyote (wolf hybrid genetics); larger than western.\n"
            "White-tailed deer: Extremely high density in northern VA suburbs; major vehicle collision hazard Oct–Dec.\n"
            "Wildlife corridors: Potomac River tributaries, Bull Run watershed, Shenandoah River valley — major movement routes for bear, deer, turkey.\n\n"
            "SOUTHEAST (FL, GA, SC, AL, MS, LA, AR, TN):\n"
            "American alligator: Any standing water in FL, GA, SC, AL, MS, LA, TX — extremely commonplace.\n"
            "Venomous snakes: Eastern diamondback rattlesnake (FL/GA/SC — largest NA pit viper); cottonmouth (all); copperhead (all); coral snake (FL/GA/SC/NC/LA/TX).\n"
            "Wild boar: Extremely widespread; TX has largest US population.\n"
            "Fire ant: SE US — mass stinging; anaphylaxis risk.\n"
            "Spiders: Black widow and brown recluse both widespread.\n\n"
            "MIDWEST (OH, IN, IL, MO, IA, MN, WI, MI, KS, NE, ND, SD):\n"
            "Venomous snakes: Timber rattlesnake (rocky outcrops in OH/MO/IA/WI); massasauga (Great Lakes wetlands); prairie rattlesnake (Dakotas/KS/NE); copperhead (OH River watershed into MO/KS).\n"
            "Ticks: Blacklegged tick (MN/WI/MI — very high Lyme risk); lone star (southern Midwest).\n"
            "Spiders: Brown recluse widespread in southern Midwest states.\n"
            "Gray wolf: MN/WI/MI (reestablished packs, growing).\n\n"
            "SOUTHWEST (TX, NM, AZ, OK, NV, UT, CO):\n"
            "Rattlesnakes: 15+ species in AZ alone — Western diamondback (most bites), Mojave (most toxic), Sidewinder, Banded Rock, Tiger, Speckled.\n"
            "Arizona bark scorpion: Life-threatening; common in AZ homes and yards; check shoes every morning.\n"
            "Gila monster: AZ/NM desert — protected, not aggressive.\n"
            "Africanized honey bees: AZ, NM, S TX, SE CA — treat ALL wild bee colonies as Africanized.\n"
            "Javelina/Collared Peccary: AZ/NM/TX; sharp tusks; attack dogs; do not corner.\n\n"
            "ROCKY MOUNTAIN (MT, ID, WY, CO, UT northern):\n"
            "GRIZZLY BEAR ZONE: MT/WY/ID — mandatory bear spray in Yellowstone/Glacier/Bob Marshall backcountry.\n"
            "Black bear, gray wolf, mountain lion: throughout. Moose: N Rockies into CO.\n"
            "Bison: Yellowstone — #1 cause of visitor injuries in the park.\n"
            "Prairie rattlesnake: widespread in foothills and valleys.\n\n"
            "PACIFIC COAST (CA, OR, WA):\n"
            "Mountain lion: Highest human encounter rate in CA; expanding near urban/suburban interface.\n"
            "Great white shark: N CA near seal populations; OR coast.\n"
            "Western rattlesnake species: CA/OR/WA foothills and semi-arid terrain.\n"
            "Brown recluse: NOT native to Pacific coast (frequently misidentified — likely yellow sac spider).\n\n"
            "ALASKA:\n"
            "Brown/grizzly bear: Throughout; carry bear spray + firearm; highest density on AK Peninsula/Kodiak.\n"
            "Polar bear: North Slope and Arctic coast — always predatory toward humans; extremely dangerous.\n"
            "Moose: #1 dangerous wildlife encounter in AK statewide.\n"
            "No venomous snakes in Alaska."
        ),
    },
]


# ---------------------------------------------------------------------------
# Atlas Control app knowledge base — UI navigation, features, and metrics
# ---------------------------------------------------------------------------
ATLAS_DOCS = [
    {
        "title": "Atlas Control — App Overview and Navigation",
        "tags": "atlas control,navigation,sidebar,pages,ui,app,overview,layout,how to use",
        "content": (
            "Atlas Control is a local offline field dashboard running on a Jetson Orin Nano. "
            "It is accessed via a web browser on any device connected to the same network (LAN or Atlas hotspot).\n\n"
            "SIDEBAR NAVIGATION:\n"
            "The vertical icon bar on the left is the sidebar. Click any icon to switch pages. "
            "The active page has a highlighted icon. Pages can be reordered by drag-and-drop, and individual "
            "pages can be hidden (right-click or long-press a button). The Settings page is always visible.\n\n"
            "PAGES (in default order):\n"
            "• Dashboard — live overview of the whole mesh network and system health\n"
            "• Mesh      — detailed node list, network topology graph, power telemetry, and location-sharing\n"
            "• Map       — offline MapLibre map with all GPS-equipped nodes plotted; also Road Nav tab\n"
            "• Hiking    — offline hiking trail layer with trailhead search and NPS trail overlays\n"
            "• Messages  — mesh channel messages and direct messages; Alerts tab for system notifications\n"
            "• Tools     — scientific calculator (with ballistics) and calendar/date tools\n"
            "• Settings  — WiFi/hotspot config, system stats, and all app settings\n"
            "• Ray (AI)  — local AI assistant powered by Ollama; has RAG access to survival docs and this guide\n\n"
            "THEME: Light/dark toggle is available in the Settings page → Settings tab.\n\n"
            "MOBILE APP: An Android companion app shows the full web UI in a WebView and handles "
            "automatic switching between the Atlas hotspot (SSID: atlas_navigate) and LAN."
        ),
    },
    {
        "title": "Atlas Control — Dashboard Page",
        "tags": "dashboard,stat cards,overview,metrics,nodes online,snr,rssi,channel load,battery,alerts,network health",
        "content": (
            "The Dashboard is the home page. It shows live stat tiles and summary cards refreshed every few seconds.\n\n"
            "TOP STAT BAR (small tiles across the top):\n"
            "• Total Nodes     — count of all known nodes in the database\n"
            "• Messages        — total messages received\n"
            "• Atlas + GPS     — connection status of the local Meshtastic radio and GPS receiver\n"
            "• Atlas Battery   — host device (Jetson) battery percentage, voltage, current, and charge state\n"
            "• Alert Count     — number of unacknowledged system alerts\n"
            "• Channel Util    — average channel utilization % across the mesh\n"
            "• Average SNR     — average signal-to-noise ratio in dB across all nodes with telemetry\n"
            "• Mesh Avg Battery — average battery % across all nodes reporting battery telemetry\n\n"
            "DASHBOARD CARDS:\n"
            "• Network Health  — summary tile showing nodes online / total, avg SNR, avg RSSI, and channel load "
            "with color coding (green=good, amber=fair, red=poor). Links to Mesh page.\n"
            "• Phone Trackers  — count of active phones sending GPS positions via the mobile tracker feature\n"
            "• Battery Fleet   — breakdown of node batteries into Critical (<20%), Low (20–49%), Good (≥50%), Unknown\n"
            "• Signal Distribution — pie/count of nodes bucketed by signal quality label\n"
            "• Node Roles      — count of nodes by Meshtastic role (CLIENT, ROUTER, REPEATER, TRACKER, etc.)\n"
            "• Online Nodes    — list of nodes heard within the configured online window (default 2 h)\n"
            "• Recent Messages — last few mesh messages received\n"
            "• Active Alerts   — unacknowledged system alerts with severity badges\n\n"
            "CARD CUSTOMIZATION: Cards can be shown/hidden from Settings → Settings tab → Dashboard Cards. "
            "All cards are visible by default."
        ),
    },
    {
        "title": "Atlas Control — Mesh Page",
        "tags": "mesh,nodes,topology,power,location,sharing,node list,hardware,roles,uptime,tab",
        "content": (
            "The Mesh page has four tabs: Nodes, Topology, Power, and Location Sharing.\n\n"
            "NODES TAB:\n"
            "Lists all known mesh nodes. Each row shows:\n"
            "• Node name (long name / alias), short name, and hardware model\n"
            "• Last Heard — timestamp of the last packet received from that node\n"
            "• SNR / RSSI — signal quality of the last received packet\n"
            "• Battery %  — last reported battery level (if telemetry enabled on that node)\n"
            "• Role        — Meshtastic device role (CLIENT, ROUTER, REPEATER, TRACKER, CLIENT_MUTE, etc.)\n"
            "• Distance    — calculated from GPS coordinates if both nodes have a fix\n"
            "Click a node row to open its detail panel with full telemetry, raw channel utilization, "
            "altitude, GPS coordinates, uptime, and hop information.\n"
            "The local Atlas device is highlighted and labeled 'Atlas Control'.\n\n"
            "TOPOLOGY TAB:\n"
            "Renders a live graph of mesh connections. Nodes are drawn as circles; "
            "edges show the most recently observed path between nodes. "
            "Hover over a node to see its name and last-heard time.\n\n"
            "POWER TAB:\n"
            "Shows per-node battery bars sorted from lowest to highest charge. "
            "Critical (<20%) nodes are shown in red, low (20–49%) in amber, good (≥50%) in green.\n\n"
            "LOCATION SHARING TAB:\n"
            "Manage the location-sharing feature that sends your GPS position over the mesh. "
            "Enable/disable broadcasting, set interval, and see which nodes are currently sharing."
        ),
    },
    {
        "title": "Atlas Control — Map Page",
        "tags": "map,offline,maplibre,pmtiles,gps,navigation,road nav,nodes,markers,routing,trail",
        "content": (
            "The Map page has two tabs: Map and Road Nav.\n\n"
            "MAP TAB:\n"
            "An offline MapLibre vector map backed by local PMTiles files. "
            "No internet connection is required — all map tiles are stored on the Jetson.\n"
            "Features:\n"
            "• All mesh nodes that have GPS coordinates are shown as markers\n"
            "• Tapping a node marker shows a popup with lat/lon, altitude, and a 'Navigate here' button\n"
            "• Your own GPS fix (from the SparkFun GPS receiver) is shown as a distinct marker\n"
            "• Phone tracker devices are shown in real time as they broadcast positions over the mesh\n"
            "• Standard map controls: zoom in/out, pitch, bearing, full-screen\n"
            "• Default zoom is configurable in Settings\n\n"
            "ROAD NAV TAB:\n"
            "Offline turn-by-turn routing powered by OSRM (Open Source Routing Machine). "
            "Enter a destination or tap a node's 'Navigate here' button to get a route. "
            "Supports both road (blue) and hiking (amber) routing profiles. "
            "Route instructions are displayed step-by-step with distance and direction. "
            "All routing computation happens locally on the Jetson — no internet needed."
        ),
    },
    {
        "title": "Atlas Control — Hiking Page",
        "tags": "hiking,trails,NPS,national park,trailheads,topo,elevation,map,outdoor,navigation",
        "content": (
            "The Hiking page is a dedicated outdoor navigation view layered on top of the offline map.\n\n"
            "FEATURES:\n"
            "• NPS Trail Overlays — National Park Service trail data downloaded and stored offline. "
            "Trails are drawn as colored lines on the map.\n"
            "• Trailhead Search   — search for trailheads by name; results show distance from current position.\n"
            "• Topo Layer         — topographic contour data (downloaded separately with setup_topo_pmtiles.sh). "
            "Toggle on/off to see elevation contours overlaid on the map.\n"
            "• Hiking Routing     — uses the Lua hiking routing profile in OSRM for foot-traffic paths, "
            "distinct from the road routing profile used on the Map page.\n\n"
            "DATA SETUP:\n"
            "Trail and trailhead data is downloaded with the download_nps_trails.py and "
            "download_trailheads.py scripts. Topo tiles are prepared with setup_topo_pmtiles.sh. "
            "All data is stored locally; no internet is needed once downloaded."
        ),
    },
    {
        "title": "Atlas Control — Messages Page (Comms)",
        "tags": "messages,comms,chat,mesh,channel,direct message,DM,send,receive,contacts,alerts,notification",
        "content": (
            "The Messages page (sidebar icon: speech bubble) has two tabs: Messages and Alerts.\n\n"
            "MESSAGES TAB:\n"
            "• Left panel — Contacts list showing all known nodes. "
            "Nodes are grouped: channel (broadcast) at the top, then individual nodes. "
            "Unread message counts are shown as badges.\n"
            "• Right panel — Chat view for the selected contact. "
            "Channel messages (sent to all nodes) appear in the channel thread. "
            "Direct messages (DMs) appear in the individual node thread.\n"
            "• Message bubbles show sender name, timestamp, hop count, SNR, and RSSI of the received packet.\n"
            "• Type a message in the input bar and press Send (or Enter) to transmit over the mesh.\n"
            "• Messages are stored in the local SQLite database and persist across restarts.\n\n"
            "ALERTS TAB:\n"
            "Lists system-generated alerts. Each alert shows:\n"
            "• Severity badge — info (blue), warning (amber), or critical (red)\n"
            "• Alert type     — battery, offline, connection, or message\n"
            "• Title and message describing what happened\n"
            "• Timestamp\n"
            "Click 'Acknowledge' on an alert to dismiss it. 'Acknowledge All' clears all at once. "
            "Unacknowledged alerts are also counted in the Dashboard top stat bar."
        ),
    },
    {
        "title": "Atlas Control — Alerts: Types and Severities",
        "tags": "alerts,battery,offline,connection,message,critical,warning,info,severity,notification",
        "content": (
            "Atlas Control generates automatic alerts for notable mesh network events.\n\n"
            "ALERT TYPES:\n"
            "• battery    — a node's battery level dropped below a threshold. "
            "Severity is 'critical' if below 10%, 'warning' if below the configured alert threshold (default 20%).\n"
            "• offline    — a node has not been heard within the configured offline window (default 30 min). "
            "Severity: warning.\n"
            "• connection — the local Meshtastic radio disconnected or failed to reconnect. "
            "Severity: critical.\n"
            "• message    — a new mesh message was received (used for notification purposes). "
            "Severity: info.\n\n"
            "SEVERITY LEVELS:\n"
            "• critical (red)  — requires immediate attention (lost radio connection, critically low battery)\n"
            "• warning (amber) — degraded but not urgent (node went offline, low battery)\n"
            "• info (blue)     — informational only (new message received)\n\n"
            "THRESHOLDS (configurable in Settings → Settings tab):\n"
            "• Battery alert threshold — default 20%. Nodes below this generate a warning alert.\n"
            "• Node offline window     — default 30 min. Node not heard in this time generates an offline alert.\n"
            "• Node online window      — default 2 h. Used for the 'Online Nodes' dashboard card."
        ),
    },
    {
        "title": "Atlas Control — Signal Metrics: SNR, RSSI, Channel Utilization",
        "tags": "snr,rssi,signal,channel utilization,air util,tx,signal quality,excellent,good,fair,poor,dB,dBm,metrics",
        "content": (
            "SIGNAL-TO-NOISE RATIO (SNR) — measured in dB:\n"
            "SNR is the ratio of the useful signal power to the background noise. "
            "Higher is better. Meshtastic nodes report the SNR of each packet they receive.\n"
            "Quality labels used in Atlas Control:\n"
            "• Excellent : SNR ≥ 10 dB  (very strong signal, reliable link)\n"
            "• Good      : SNR 5–9 dB   (solid link, minor fading OK)\n"
            "• Fair      : SNR 0–4 dB   (marginal; packet loss possible in noisy environments)\n"
            "• Poor      : SNR < 0 dB   (near the noise floor; expect frequent packet loss)\n\n"
            "RECEIVED SIGNAL STRENGTH INDICATOR (RSSI) — measured in dBm:\n"
            "RSSI is the absolute received power level. More negative = weaker signal.\n"
            "Typical interpretation:\n"
            "• > -80 dBm  : Good (strong signal)\n"
            "• -80 to -100 dBm : Fair\n"
            "• < -100 dBm : Weak (approaching receiver sensitivity limit)\n"
            "Note: RSSI alone is not enough — a high-noise environment can have high RSSI but still low SNR.\n\n"
            "CHANNEL UTILIZATION (channel_util) — percentage:\n"
            "The fraction of airtime used by the channel in the last measurement window. "
            "Meshtastic recommends keeping this below ~25–30% to avoid congestion and collisions. "
            "High channel utilization causes packet loss even when individual SNR is good.\n\n"
            "AIR UTIL TX (air_util_tx) — percentage:\n"
            "The fraction of airtime this specific node is using for transmissions. "
            "A node with high air_util_tx is a heavy transmitter (e.g., a router relaying many packets). "
            "The Dashboard shows average air_util_tx across all nodes."
        ),
    },
    {
        "title": "Atlas Control — Node Fields and Roles",
        "tags": "node,fields,role,client,router,repeater,tracker,hw_model,hardware,long_name,short_name,uptime,battery,last_heard,hop",
        "content": (
            "Each mesh node in Atlas Control has the following fields:\n\n"
            "IDENTIFICATION:\n"
            "• long_name  — full node name set by the owner (e.g. 'Base Alpha')\n"
            "• short_name — 4-character call sign displayed on the device screen\n"
            "• alias      — optional custom label set within Atlas Control (local override)\n"
            "• node_id    — unique 8-digit hex ID assigned by Meshtastic (e.g. !a1b2c3d4)\n"
            "• hw_model   — hardware model string reported by the node (e.g. HELTEC_V3, TBEAM, RAK4631)\n\n"
            "TELEMETRY:\n"
            "• snr          — signal-to-noise ratio in dB of the last received packet\n"
            "• rssi         — received signal strength in dBm of the last received packet\n"
            "• battery_level — battery charge percentage (0–100); null if not reported\n"
            "• voltage       — battery voltage in volts\n"
            "• channel_util  — channel utilization % reported by the node\n"
            "• air_util_tx   — TX airtime utilization % reported by the node\n"
            "• uptime        — seconds since last reboot\n"
            "• last_heard    — Unix timestamp of the most recent packet from this node\n\n"
            "GPS:\n"
            "• latitude, longitude — decimal degrees (WGS84)\n"
            "• altitude            — meters above sea level\n\n"
            "MESHTASTIC ROLES:\n"
            "• CLIENT       — standard end-user device; receives and transmits but does not relay\n"
            "• CLIENT_MUTE  — like CLIENT but never retransmits (saves airtime on dense networks)\n"
            "• ROUTER       — dedicated relay node; forwards packets to extend range; minimal transmit power\n"
            "• ROUTER_CLIENT — router that can also be used as an end-user device\n"
            "• REPEATER     — aggressive relay; retransmits everything; use only at strategic choke points\n"
            "• TRACKER      — GPS tracking device; broadcasts position frequently; minimal UI\n"
            "• SENSOR       — environmental sensor node; broadcasts telemetry; no user messaging\n"
            "• TAK          — Team Awareness Kit integration node\n\n"
            "HOP COUNT:\n"
            "• hop_limit — max hops remaining when a packet was sent (usually starts at 3)\n"
            "• A packet with hop_limit=2 was relayed once; hop_limit=1 was relayed twice, etc.\n"
            "• Direct packets (heard with no relays) have hop_limit equal to the sender's configured max."
        ),
    },
    {
        "title": "Atlas Control — Tools Page (Calculator and Calendar)",
        "tags": "tools,calculator,ballistics,math,scientific,calendar,date,unit conversion,physics",
        "content": (
            "The Tools page has two tabs: Calculator and Calendar.\n\n"
            "CALCULATOR TAB:\n"
            "A scientific calculator with several modes:\n"
            "• Basic math — type any arithmetic expression (e.g. '355/113', '2^10', 'sqrt(2)')\n"
            "• Unit conversions — type phrases like 'convert 5 km to miles', '98.6 F to C', '10 lbs to kg'\n"
            "• Ballistics — calculate bullet drop by typing queries like:\n"
            "  '308 at 500 yards zeroed 100', '.223 drop at 300m', '6.5 creedmoor 600 yards'\n"
            "  The calculator uses a G1 drag model with pre-loaded data for common rounds.\n"
            "• The AI assistant (Ray) also handles calculator queries and can show working.\n\n"
            "Supported functions: sin, cos, tan, sqrt, log, log10, exp, pi, and all standard operators.\n\n"
            "CALENDAR TAB:\n"
            "Date and time utility tools for field planning:\n"
            "• Day-of-week lookup for any date\n"
            "• Days-between calculator\n"
            "• Sunrise/sunset estimation (requires GPS coordinates)\n"
            "• Moon phase display"
        ),
    },
    {
        "title": "Atlas Control — Settings Page (System, WiFi, Stats)",
        "tags": "settings,wifi,hotspot,system,stats,configure,dark mode,theme,units,serial port,battery alert,refresh,online window",
        "content": (
            "The Settings page has three tabs: WiFi, Stats, and Settings.\n\n"
            "WIFI TAB:\n"
            "• WiFi mode — switch between connecting to an existing WiFi network (station mode) "
            "and running the Atlas hotspot (access point mode)\n"
            "• Hotspot SSID: 'atlas_navigate', password: 'password' (default)\n"
            "• LAN mode — join an existing network; the Jetson gets a DHCP address\n"
            "• Status section shows the current IP address, connection type, and link state\n\n"
            "STATS TAB:\n"
            "System telemetry for the Jetson host:\n"
            "• CPU usage, CPU temperature, memory usage, disk usage\n"
            "• Process count, system uptime\n"
            "• GPS fix status (connected/disconnected, fix quality, coordinates)\n\n"
            "SETTINGS TAB:\n"
            "Configurable options:\n"
            "• Units               — metric or imperial (affects distances on Map and Hiking pages)\n"
            "• Date format         — relative ('5 min ago') or absolute ('2024-11-15 14:32')\n"
            "• Show offline nodes  — whether to display nodes not heard recently in the Nodes list\n"
            "• Node online window  — hours before a node is considered offline (default 2 h)\n"
            "• Dashboard refresh   — how often live data updates in seconds (default 5 s)\n"
            "• Map default zoom    — initial zoom level when the Map page loads (default 13)\n"
            "• Battery alert %     — threshold below which a battery alert fires (default 20%)\n"
            "• Node offline min    — minutes of silence before an offline alert fires (default 30 min)\n"
            "• Serial port         — device path for the Meshtastic radio (default /dev/ttyACM0)\n"
            "• Theme               — light or dark mode toggle\n"
            "• Dashboard Cards     — checkboxes to show/hide individual dashboard stat cards\n"
            "• AI Settings         — model selection, embedding model, RAG toggle, temperature, "
            "context window size, and other Ollama parameters for the Ray AI assistant"
        ),
    },
    {
        "title": "Atlas Control — Ray AI Assistant",
        "tags": "ai,ray,ollama,assistant,chat,RAG,knowledge base,model,settings,mesh context,how to use",
        "content": (
            "Ray is the local AI assistant built into Atlas Control. It runs entirely offline using Ollama.\n\n"
            "ACCESSING RAY:\n"
            "Click the AI icon (brain/star) in the sidebar. Ray opens in a full chat interface with a "
            "conversation list on the left and the active chat on the right.\n\n"
            "WHAT RAY CAN DO:\n"
            "• Answer questions about survival, radio operation, ballistics, first aid, and off-grid topics "
            "by retrieving relevant entries from the built-in knowledge base (RAG)\n"
            "• Answer questions about how to use Atlas Control — navigation, metrics, settings, etc.\n"
            "• Describe the current mesh network state (node count, battery levels, recent alerts, etc.) "
            "when 'Inject mesh context' is enabled in AI Settings\n"
            "• Perform math, unit conversions, and ballistic drop calculations\n"
            "• Hold multi-turn conversations; each chat in the left panel is a separate conversation\n\n"
            "KNOWLEDGE BASE (RAG):\n"
            "Ray has access to curated reference documents embedded with the qwen3-embedding:0.6b model. "
            "When you ask a question, the top-matching documents are retrieved and injected into the prompt. "
            "Documents cover: survival skills, radio comms, ballistics, first aid, field craft, "
            "long-term sustainability, and Atlas Control app usage (this guide).\n\n"
            "AI SETTINGS (in Settings page → Settings tab → AI Settings section):\n"
            "• Model          — Ollama model to use (default qwen3.5:2b)\n"
            "• Embed model    — embedding model for RAG (default qwen3-embedding:0.6b)\n"
            "• RAG enabled    — toggle retrieval-augmented generation on/off\n"
            "• RAG top-k      — number of knowledge base chunks retrieved per query (default 3)\n"
            "• Inject mesh context — include live mesh state in every prompt (default on)\n"
            "• Temperature    — response creativity (0.0=deterministic, 1.0=creative; default 0.3)\n"
            "• Context window — token context length (default 4096; larger = slower but more memory)\n"
            "• Keep alive     — hours to keep the model loaded in VRAM (default 10 h)\n\n"
            "STARTING A NEW CHAT:\n"
            "Click the '+ New Chat' button in the left panel to start a fresh conversation. "
            "Old chats are preserved and can be revisited at any time."
        ),
    },
    {
        "title": "Atlas Control — GPS and Position Tracking",
        "tags": "gps,fix,position,latitude,longitude,altitude,SparkFun,NEO-M8U,phone tracker,location,tracking",
        "content": (
            "Atlas Control integrates GPS at two levels: the host device GPS and mesh node GPS.\n\n"
            "HOST GPS (SparkFun NEO-M8U):\n"
            "• The Jetson has a dedicated SparkFun NEO-M8U GPS/IMU receiver connected via serial port.\n"
            "• Fix status is shown in the Dashboard top bar (Atlas + GPS Status tile) and on the Stats tab.\n"
            "• When a fix is acquired, the GPS position is shown on the Map as a distinct 'you are here' marker.\n"
            "• The GPS position is also injected into AI chat context when 'Inject mesh context' is enabled.\n"
            "• Fix quality: the module reports 2D fix (altitude unreliable), 3D fix (full position), "
            "or No Fix. A 3D fix requires at least 4 satellites.\n\n"
            "MESH NODE GPS:\n"
            "• Any Meshtastic node with GPS enabled broadcasts its position over the mesh.\n"
            "• These positions are stored and shown on the Map as node markers.\n"
            "• Nodes without GPS have no map marker (or stay at last-known position if stale).\n\n"
            "PHONE TRACKERS:\n"
            "• The Atlas mobile app (Android) can broadcast the phone's GPS position over the mesh "
            "using the Meshtastic position packet format.\n"
            "• Active phone trackers are shown on the Map and counted in the Dashboard 'Phone Trackers' card.\n"
            "• A tracker is considered active if a position was received within the last 5 minutes.\n\n"
            "LOCATION SHARING (Mesh tab → Location Sharing):\n"
            "• Enables the Atlas device itself to periodically broadcast its GPS position to the mesh.\n"
            "• Configure the broadcast interval to balance network load vs. position freshness."
        ),
    },
    {
        "title": "Atlas Control — Connection Status and Troubleshooting",
        "tags": "connection,status,offline,radio,serial,meshtastic,troubleshoot,reconnect,green,red,error,lost,GPS",
        "content": (
            "ATLAS + GPS STATUS TILE (Dashboard top bar):\n"
            "• Green 'Connected' — Meshtastic radio is connected on the configured serial port and responding.\n"
            "• Red 'Radio Offline' — the radio is not detected or serial communication failed. "
            "Check that the Heltec V4 is plugged in to /dev/meshtastic (udev symlink) and powered.\n"
            "• GPS indicator — shows 'Fix' (green), 'No Fix' (amber), or 'Disconnected' (red).\n\n"
            "CONNECTION ALERTS:\n"
            "If the radio disconnects, a 'connection' alert with severity 'critical' is generated. "
            "Atlas Control will attempt to reconnect automatically.\n\n"
            "COMMON ISSUES:\n"
            "• No nodes visible — verify the radio is connected and the channel settings match "
            "the other nodes in the mesh. Check the serial port setting in Settings.\n"
            "• GPS shows 'No Fix' — the receiver may need a clear sky view. Indoors or under heavy "
            "cover can prevent satellite acquisition. Cold starts can take 1–2 minutes.\n"
            "• Map shows no tiles — PMTiles files may not be downloaded or the path is wrong. "
            "Re-run the map setup scripts to regenerate tile files.\n"
            "• AI (Ray) not responding — Ollama may not be running. Check with "
            "'systemctl status ollama' and ensure the configured model is pulled.\n"
            "• High channel utilization — reduce mesh node transmit intervals or switch some nodes "
            "to CLIENT_MUTE role to reduce airtime consumption.\n\n"
            "SERVICE: Atlas Control runs as 'atlas-control.service' systemd unit. "
            "Restart with: sudo systemctl restart atlas-control"
        ),
    },
    {
        "title": "AI/ML: Sampling Parameters — Temperature, Top-P, Top-K",
        "tags": (
            "temperature,top_p,top-p,p-value,k-value,p value,k value,top_k,top-k,sampling,generation,"
            "genai,generative ai,llm,language model,ai settings,nucleus sampling,"
            "randomness,creativity,deterministic,softmax,logits,inference,parameters,ray settings"
        ),
        "content": (
            "ALIASES USED IN ATLAS CONTROL:\n"
            "  'temperature' = temperature (controls randomness)\n"
            "  'p-value' or 'p value' = top_p / Top-P (nucleus sampling cutoff)\n"
            "  'k-value' or 'k value' = top_k / Top-K (token count cap)\n"
            "These are Ray's AI generation settings, configurable in Ray → Settings tab.\n\n"
            "These three parameters control how a language model (LLM) chooses its next token during text generation.\n\n"
            "TEMPERATURE\n"
            "Scales the model's probability distribution before sampling.\n"
            "- Range: 0.0 – 2.0+ (typical: 0.6–1.0)\n"
            "- Low (0.0–0.3): near-deterministic, picks the highest-probability token each time. "
            "Best for factual Q&A, code, structured data.\n"
            "- High (1.0–2.0): flattens the distribution — more surprising, creative, or random outputs. "
            "Risk of incoherence increases above ~1.5.\n"
            "- Exactly 0.0: greedy decoding (always picks the single most likely token).\n\n"
            "TOP-P (Nucleus Sampling)\n"
            "After temperature scaling, keeps only the smallest set of tokens whose cumulative probability sums to ≥ p, "
            "then samples from that set.\n"
            "- Range: 0.0 – 1.0 (typical: 0.8–0.95)\n"
            "- Low (0.5): very focused — only the most likely tokens are eligible.\n"
            "- High (0.95–1.0): nearly all tokens are eligible; less filtering.\n"
            "- Acts as a dynamic vocabulary cap: adapts to how 'confident' the model is on a given token.\n\n"
            "TOP-K\n"
            "Keeps only the K most probable tokens (by raw probability) and samples from those.\n"
            "- Range: 1 – vocab_size (typical: 20–80)\n"
            "- K=1: greedy (same as temperature 0).\n"
            "- K=40: consider the 40 most likely tokens. Balances diversity and coherence.\n"
            "- Unlike top-p, top-k is a fixed count regardless of probability spread.\n\n"
            "HOW THEY INTERACT:\n"
            "All three are applied in sequence: temperature → top-k → top-p → sample.\n"
            "Typical quality settings: temperature=0.7, top_k=20, top_p=0.8 (Ray's defaults).\n"
            "Typical creative settings: temperature=1.0, top_k=40, top_p=0.95.\n"
            "Deterministic/factual: temperature=0.0–0.2, top_k=1, top_p=1.0.\n\n"
            "COMMON MISCONCEPTION: 'p-value' in the GenAI/LLM context means top_p (nucleus sampling), "
            "not the statistical hypothesis-testing p-value. They are unrelated concepts.\n\n"
            "ATLAS CONTROL AI SETTINGS: Ray's sampling parameters are configurable in the Ray (AI) page → "
            "Settings tab. Changes take effect on the next message."
        ),
    },
]

# ---------------------------------------------------------------------------
# Ray self-knowledge — lets Ray answer questions about its own architecture
# and thought process. Seeded into the knowledge base like any other doc, but
# also force-injected (bypassing embedding search) whenever the user asks Ray
# about itself, so the answer never depends on a similarity-score roll.
# Full human-readable version with diagram: RAY_BRAIN.md in the repo root.
# ---------------------------------------------------------------------------
RAY_SELF_DOC = {
    "title": "Ray — How My Brain Works (Self-Architecture)",
    "tags": (
        "ray,brain,architecture,thought process,how you think,how you work,"
        "pipeline,rag,retrieval,embedding,indexing,memory,confidence,ollama,"
        "self,yourself,reasoning,explain yourself,who are you,what are you"
    ),
    "content": (
        "I am Ray, the AI assistant inside Atlas Control. I run 100% offline on this "
        "Jetson Orin Nano's GPU via Ollama — no internet, no cloud. When asked how I think, "
        "retrieve, index, or process information, I answer in first person from this document.\n\n"
        "MY THOUGHT PROCESS, STAGE BY STAGE:\n"
        "1. LANGUAGE CORE — my reasoning engine is a local qwen3.5:2b model (configurable) served "
        "by Ollama, kept warm in VRAM with hybrid-thinking disabled for instant responses.\n"
        "2. INDEXING (how I learn) — my knowledge base is a set of curated reference documents "
        "(survival, comms, ballistics, first aid, Atlas Control usage, and this self-description) "
        "stored in SQLite. At startup, each document is embedded as 'title + tags + content' by "
        "qwen3-embedding:0.6b, producing a 1024-dim vector that captures both the metadata keywords "
        "and the prose meaning. The vector is stored alongside the text. Edited docs and docs with "
        "cleared embeddings are automatically re-embedded in a background thread. A parallel FTS5 "
        "full-text index (ai_documents_fts) enables BM25 keyword search across all docs.\n"
        "3. ROUTING (how I classify your question) — before I generate anything, fast keyword "
        "scanners decide what your message needs: live dashboard data, GPS/location grounding, "
        "math, physics/ballistics, or knowledge-base retrieval. Routing costs near-zero time and "
        "decides which of my subsystems wake up. If a question requires specific parameters I do "
        "not have (barrel twist rate, zero distance, custom load data), I answer with best "
        "available defaults and ask for the missing detail.\n"
        "4. RETRIEVAL (RAG, hybrid BM25 + cosine) — for knowledge questions, your message is embedded "
        "with a query-instruction prefix and compared to every document by cosine similarity. In "
        "parallel, a BM25 keyword search runs against a full-text index (title weighted 10×, tags 5×, "
        "content 1×). The hybrid score is max(v, 0.6·v + 0.4·bm25_norm) — but only for documents "
        "whose cosine similarity is ≥ 0.35 (the semantic plausibility gate). BM25 can only boost "
        "near-miss semantic candidates; it cannot surface an unrelated doc on keyword coincidence. "
        "A topic router classifies the query (wildlife, medical, ballistics, etc.) and applies a "
        "+8% score boost to docs whose tags match. The top 5 docs with hybrid score ≥ 0.45 are "
        "pasted into my context. The confidence footer uses the raw cosine score, not the boosted "
        "hybrid, so it cannot be inflated. Live-data questions skip RAG because the answer is "
        "already injected fresh.\n"
        "5. CONTEXT ASSEMBLY — my working memory for each reply is built from: current system "
        "stats (CPU/GPU/RAM/temps/power), my GPS fix reverse-geocoded to the nearest city (offline, "
        "from 41k US ZIP centroids + 68k world cities), live mesh state (nodes, channels, recent "
        "messages, telemetry, topology, alerts), any retrieved knowledge docs, and the last 8 "
        "messages of our conversation.\n"
        "6. CALCULATOR AGENT (physics/math cortex) — I do not trust myself with arithmetic. "
        "For ballistics: I parse range, zero distance, and round from your message; then a real "
        "point-mass G1 drag-model simulation integrates the trajectory and returns drop (cm) and "
        "exact time of flight (s). Spin drift is computed from Don Miller's (2005) gyroscopic "
        "stability formula: Sg = 30·m / (n²·d³·L·(1+L²)) × (v/2800)^(1/3), then applied via "
        "Litz's formula SD_in = 1.25·Sg·TOF^1.83. Sg depends on the bullet's weight, diameter, "
        "length, and barrel twist rate — if you specify your twist (e.g. '1:7 twist'), I use it; "
        "otherwise I use the standard reference twist for that round. For general math, a first "
        "low-temperature pass extracts pure [CALC: expr] expressions, a sandboxed evaluator "
        "computes them, and the verified numbers are handed to me with orders not to recompute.\n"
        "7. GENERATION — the assembled context becomes my system prompt and I stream the answer "
        "token by token within a 4096-token context window.\n"
        "8. CONFIDENCE — every answer ends with a footer I cannot fake: HIGH/MEDIUM/LOW plus the "
        "actual sources used (Live Mesh Data, Knowledge Base + match score, GPS Fix, System Stats, "
        "or Training Knowledge). It is computed from what was really injected, not from my opinion.\n\n"
        "KNOWLEDGE MAP — the Ray AI → Settings tab shows an interactive SVG visualization of my "
        "knowledge documents. Nodes are colored by topic cluster; edges connect docs whose "
        "cosine similarity is ≥ 0.55 (up to 6 per node, edge color shifts slate→amber with "
        "similarity). Click a node to highlight its connections, see a ranked 'Related' list, or "
        "switch to the 'Read' tab to view the full document text. Nodes can be dragged to reposition.\n\n"
        "MY MEMORY: conversations live in SQLite; I see the last 8 messages of the active chat. "
        "Document embeddings are cached in RAM for 120 s. I have no memory across separate chats.\n\n"
        "MY LIMITS: routing is keyword-based, so oddly-phrased questions can take the wrong path; "
        "documents are embedded whole (no chunking); anything outside my knowledge base comes from "
        "my training data, which ends at my model's cutoff and is marked LOW confidence."
    ),
}
ATLAS_DOCS.append(RAY_SELF_DOC)

# Query fragments that mean the user is asking Ray about Ray itself — its
# brain, pipeline, retrieval, or reasoning. These force-inject RAY_SELF_DOC.
_SELF_QUERY_FRAGMENTS = [
    "your brain", "rays brain", "ray's brain", "your architecture",
    "your pipeline", "your thought process", "your reasoning",
    "how do you think", "how do you work", "how you work", "how you think",
    "how do you retrieve", "how do you index", "how do you process",
    "how do you remember", "how does your memory", "your memory work",
    "how do you answer", "how did you come up", "why did you say",
    "how did you decide", "explain yourself", "describe yourself",
    "who are you", "what are you", "how were you built", "how were you made",
    "what model are you", "which model are you", "how does ray work",
    "how does ray think", "your knowledge base work", "your rag",
    "your confidence", "your context window", "inside your head",
]

def _is_self_query(msg: str) -> bool:
    """Return True if the user is asking Ray about its own internals."""
    ml = msg.lower()
    return any(kw in ml for kw in _SELF_QUERY_FRAGMENTS)


# ---------------------------------------------------------------------------
# Cosine similarity — numpy if available, otherwise pure Python
# ---------------------------------------------------------------------------
try:
    import numpy as _np
    def cosine_similarity(a, b):
        a = _np.array(a, dtype=_np.float32)
        b = _np.array(b, dtype=_np.float32)
        denom = float(_np.linalg.norm(a)) * float(_np.linalg.norm(b))
        return float(_np.dot(a, b) / denom) if denom else 0.0
except ImportError:
    def cosine_similarity(a, b):
        """Pure-Python cosine similarity between two equal-length float lists."""
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# AIManager
# ---------------------------------------------------------------------------
_DOC_EMB_CACHE_TTL = 120  # seconds — refresh cached doc embeddings
# Cosine similarity threshold — discard retrieved docs below this score.
# Calibrated for qwen3-embedding:0.6b with embedding format v2 (title+tags+content):
# on-topic queries score 0.48–0.85+, off-topic noise stays ≤0.40. 0.45 sits
# comfortably above the noise floor. The confidence footer uses the raw pre-boost
# cosine score, so it cannot be inflated by the +8% topic-router boost.
# (nomic's old value was 0.30; do NOT reuse it — at 0.30 every query retrieves docs.)
_RAG_MIN_SCORE = 0.45

# Minimum vector similarity before BM25 is allowed to contribute. Below this
# the doc is semantically unrelated to the query; keyword overlap is coincidental
# and the BM25 boost would manufacture a false positive.
_BM25_GATE = 0.35


class AIManager:
    def __init__(self, ollama_base="http://localhost:11434", mesh_manager=None, socketio=None,
                 gps_manager=None):
        self.ollama_base = ollama_base.rstrip("/")
        self.mesh_manager = mesh_manager
        self.socketio = socketio
        self.gps_manager = gps_manager
        self._lock = threading.Lock()
        self._ollama_lock = threading.Lock()
        self._startup_warmup_done = threading.Event()
        self._doc_emb_cache = None       # list of (doc_dict, emb_list)
        self._doc_emb_cache_ts = 0.0
        self._thinking_caps = {}         # model name → bool ("thinking" capability)
        self._startup_state_lock = threading.Lock()
        self._startup_phase = "init"
        self._startup_ready = False
        self._startup_last_error = ""
        self._startup_started_at = time.time()
        self._startup_finished_at = None

    def _set_startup_state(self, phase, *, ready=None, error=None, finished=False):
        with self._startup_state_lock:
            self._startup_phase = phase
            if ready is not None:
                self._startup_ready = bool(ready)
            if error is not None:
                self._startup_last_error = str(error or "")
            if finished:
                self._startup_finished_at = time.time()

    def startup_status(self):
        with self._startup_state_lock:
            return {
                "phase": self._startup_phase,
                "ready": bool(self._startup_ready),
                "warming_up": not self._startup_warmup_done.is_set(),
                "last_error": self._startup_last_error,
                "started_at": self._startup_started_at,
                "finished_at": self._startup_finished_at,
            }

    def is_ready(self):
        status = self.startup_status()
        return bool(status["ready"] and not status["warming_up"])

    # ------------------------------------------------------------------
    def start(self):
        """Called at app startup. Seeds documents and triggers warmup."""
        import database as db
        self._set_startup_state("starting", ready=False, error="")
        settings = db.ai_get_settings()
        db.ai_seed_documents(SURVIVAL_DOCS)
        db.ai_seed_documents(ATLAS_DOCS)
        # Run embedding in background so startup is not blocked by Ollama
        threading.Thread(target=self._embed_unembedded_docs, daemon=True).start()
        if settings.get("warmup_on_start", "true").lower() == "true":
            self._set_startup_state("warming_up", ready=False, error="")
            threading.Thread(target=self._warmup, daemon=True).start()
        else:
            self._set_startup_state("warmup_skipped", ready=True, error="", finished=True)
            self._startup_warmup_done.set()

    # ------------------------------------------------------------------
    def _warmup(self):
        """Background thread: load the embed model first, then the chat model."""
        import database as db
        settings = db.ai_get_settings()
        model = settings.get("model", DEFAULT_SETTINGS["model"])
        embed_model = settings.get("embed_model", DEFAULT_SETTINGS["embed_model"])
        keep_alive = f"{settings.get('keep_alive_hours', '10')}h"
        try:
            self._set_startup_state("warming_up", ready=False, error="")
            with self._ollama_slot(timeout=600):
                if embed_model:
                    self._warmup_embed_model(embed_model)
                self._warmup_chat_model(model, keep_alive, settings)
            logger.info("AI warmup complete.")
            self._set_startup_state("ready", ready=True, error="", finished=True)
        except Exception as e:
            logger.warning(f"AI warmup failed (Ollama may not be running): {e}")
            self._set_startup_state("degraded", ready=False, error=e, finished=True)
        finally:
            self._startup_warmup_done.set()

    @contextlib.contextmanager
    def _ollama_slot(self, timeout=300):
        """Serialize Ollama calls without deadlocking gevent.

        _ollama_lock is a native lock (monkey.patch_all runs with thread=False)
        but the HTTP sockets used while holding it are gevent-patched. If a
        greenlet blocks on .acquire(), the whole event loop freezes and the
        greenlet holding the lock can never be scheduled to release it. In the
        main (gevent) thread we poll cooperatively instead; native threads
        (e.g. _warmup) block normally.
        """
        deadline = time.monotonic() + timeout
        if threading.current_thread() is threading.main_thread():
            import gevent
            while not self._ollama_lock.acquire(blocking=False):
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Ray is busy with another AI request — try again in a moment.")
                gevent.sleep(0.05)
        else:
            if not self._ollama_lock.acquire(timeout=timeout):
                raise TimeoutError(
                    "Ray is busy with another AI request — try again in a moment.")
        try:
            yield
        finally:
            self._ollama_lock.release()

    def _wait_for_startup_warmup(self, timeout=8):
        """Avoid racing the startup warmup on the first user request."""
        if self._startup_warmup_done.is_set():
            return
        logger.info(f"AI request waiting for startup warmup (timeout={timeout}s)")
        self._startup_warmup_done.wait(timeout=timeout)

    def _warmup_embed_model(self, embed_model):
        """Load the embedding model once so the first RAG query is not a cold start."""
        if not self._ensure_model_present(embed_model):
            logger.warning(f"AI embed warmup skipped; model not available: {embed_model}")
            return
        logger.info(f"AI warmup: loading embed model {embed_model}")
        # num_gpu 0: keep the small embedder on CPU so it never competes with
        # the chat model for the Jetson's shared GPU memory.
        payload = json.dumps({"model": embed_model, "input": "warmup",
                              "options": {"num_gpu": 0}}).encode()
        req = urllib.request.Request(
            f"{self.ollama_base}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=60)

    def _warmup_chat_model(self, model, keep_alive, settings):
        """Load the chat model into VRAM using the current runtime settings."""
        if not self._ensure_model_present(model):
            logger.warning(f"AI warmup skipped; model not available: {model}")
            return
        logger.info(f"AI warmup: loading model {model} with keep_alive={keep_alive}")
        payload = json.dumps(self._finalize_payload({
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "keep_alive": keep_alive,
            "stream": False,
            "options": {
                "num_gpu":    int(settings.get("num_gpu",    DEFAULT_SETTINGS["num_gpu"])),
                "num_ctx":    int(settings.get("num_ctx",    DEFAULT_SETTINGS["num_ctx"])),
                "num_thread": int(settings.get("num_thread", DEFAULT_SETTINGS["num_thread"])),
                "num_batch":  int(settings.get("num_batch",  DEFAULT_SETTINGS["num_batch"])),
                "temperature": float(settings.get("temperature", DEFAULT_SETTINGS["temperature"])),
                "top_p":      float(settings.get("top_p",    DEFAULT_SETTINGS["top_p"])),
                "top_k":      int(settings.get("top_k",      DEFAULT_SETTINGS["top_k"])),
                "num_predict": int(settings.get("num_predict", DEFAULT_SETTINGS["num_predict"])),
            },
        })).encode()
        req = urllib.request.Request(
            f"{self.ollama_base}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=60)

    # ------------------------------------------------------------------
    def unload(self, model=None):
        """Unload the model from VRAM by sending keep_alive=0 to Ollama."""
        import database as db
        if model is None:
            settings = db.ai_get_settings()
            model = settings.get("model", DEFAULT_SETTINGS["model"])
        payload = json.dumps({"model": model, "keep_alive": 0}).encode()
        try:
            req = urllib.request.Request(
                f"{self.ollama_base}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=15)
            logger.info(f"AI model '{model}' unloaded from VRAM.")
        except Exception as e:
            logger.warning(f"AI model unload failed (Ollama may not be running): {e}")

    # ------------------------------------------------------------------
    def _embed_unembedded_docs(self):
        """Embed any documents that don't yet have embeddings."""
        import database as db
        docs = db.ai_get_documents_with_embeddings()
        for doc in docs:
            if not doc.get("embedding"):
                try:
                    # Prepend title and tags so keyword-rich metadata improves
                    # cosine matching (e.g. "rattlesnake" in tags boosts recall
                    # for specific queries that the full-body average would dilute).
                    tags = doc.get("tags") or ""
                    embed_text = (f"{doc['title']}\n{tags}\n\n{doc['content']}"
                                  if tags else f"{doc['title']}\n\n{doc['content']}")
                    emb = self.get_embed(embed_text)
                    db.ai_update_document_embedding(doc["id"], json.dumps(emb))
                    logger.info(f"Embedded doc id={doc['id']}: {doc['title']}")
                except Exception as e:
                    logger.warning(f"Failed to embed doc {doc['id']}: {e}")

    # ------------------------------------------------------------------
    # Qwen3-Embedding is instruction-aware: the recommended usage prefixes
    # QUERIES with a one-line task instruction while embedding DOCUMENTS plain.
    # This query/document asymmetry measurably improves retrieval. Tailored to
    # the Atlas knowledge base (survival / radio / nav / app usage).
    _EMBED_QUERY_INSTRUCT = (
        "Given a question, retrieve survival, radio, navigation, and Atlas "
        "Control reference passages that answer it"
    )

    # ── Topic router ───────────────────────────────────────────────────────────
    # Keyword substrings that signal a query belongs to each knowledge category.
    # Used by _classify_query_category() to give matching docs a small score
    # bonus in rag_search() so the right cluster surfaces first.
    _QUERY_CATEGORY_KEYS: dict = {
        "water":      ["water", "purif", "drink", "hydrat", "stream", "filter", "boil"],
        "fire":       ["fire", "tinder", "ignit", "spark", "flame", "campfire"],
        "shelter":    ["shelter", "bivouac", "tarp", "insul", "sleep", "tent"],
        "food":       ["food", "forag", "edible", "plant", "mushroom", "calor",
                       "garden", "crop", "livestock", "harvest", "berr"],
        "medical":    ["medic", "first aid", "wound", "bleed", "hemorrh", "tourni",
                       "cpr", "shock", "airway", "infect", "fractur", "burn",
                       "hypotherm", "trauma", "bite", "sting", "inject"],
        "navigation": ["navigat", "compass", "bearing", "azimuth", "landmark", "orienteer"],
        "comms":      ["mesh", "meshtastic", "radio", "frequen", "communic",
                       "gmrs", "amateur", "transmi", "antenna"],
        "power":      ["power", "batter", "solar", "generat", "watt", "volt", "charge"],
        "security":   ["security", "opsec", "patrol", "defense", "threat", "surveil"],
        "ballistics": ["ballistic", "moa", "mil", "bullet drop", "wind drift",
                       "scope click", "dope", "hold over"],
        "firearms":   ["firearm", "rifle", "pistol", "handgun", "malfunction",
                       "caliber", "ammunition", "trigger", "clean"],
        "grid_down":  ["grid-down", "grid down", "collapse", "shtf", "barter",
                       "trade goods", "sustain", "long-term survival", "community defense"],
        "vehicles":   ["vehicle", "fuel", "oil change", "tire", "off-road", "recover",
                       "engine", "mainten"],
        "wildlife":   ["wildlife", "animal", "snake", "bear", "mountain lion", "wolf",
                       "spider", "scorpion", "tick", "rabies", "alligator", "shark",
                       "venomous", "predator", "encounter", "rattlesnake", "bitten"],
        "trees":      ["tree", "native", "oak", "pine", "maple", "bark", "forest"],
        "atlas_app":  ["atlas", "dashboard", "map page", "mesh page", "ai tab",
                       "gps page", "connection", "how do i use", "what is atlas",
                       "top_k", "top_p", "top-k", "top-p", "k-value", "p-value",
                       "k value", "p value", "nucleus sampling", "sampling parameter",
                       "temperature setting", "ray setting", "ai setting",
                       "genai", "llm setting", "generation parameter"],
    }

    # Maps category → substrings to look for in a doc's tags field.
    _DOC_CATEGORY_TAGS: dict = {
        "water":      ["water", "purif", "hydrat"],
        "fire":       ["fire", "tinder"],
        "shelter":    ["shelter", "bivouac"],
        "food":       ["food", "forag", "edible", "calor", "garden", "livestock"],
        "medical":    ["medic", "trauma", "hemorrh", "wound", "first aid"],
        "navigation": ["navigat", "compass", "land nav"],
        "comms":      ["mesh", "meshtastic", "radio", "amateur"],
        "power":      ["power", "batter", "solar"],
        "security":   ["security", "opsec"],
        "ballistics": ["ballistic", "moa", "mil", "drop", "wind"],
        "firearms":   ["firearm", "rifle", "pistol", "caliber", "malfunction"],
        "grid_down":  ["grid", "collapse", "barter", "sustain"],
        "vehicles":   ["vehicle", "fuel", "maintenance"],
        "wildlife":   ["wildlife", "snake", "bear", "spider", "venomous",
                       "rabies", "alligator"],
        "trees":      ["tree", "native", "species"],
        "atlas_app":  ["atlas", "app", "ray", "dashboard"],
    }

    def _classify_query_category(self, query: str):
        """Return the best-matching topic category slug for a query, or None.

        Counts lowercase substring hits per category; returns None when no
        category has at least one match (full-corpus search is safest when
        the query is ambiguous).
        """
        q = query.lower()
        best_cat, best_hits = None, 0
        for cat, keywords in self._QUERY_CATEGORY_KEYS.items():
            hits = sum(1 for kw in keywords if kw in q)
            if hits > best_hits:
                best_cat, best_hits = cat, hits
        return best_cat if best_hits >= 1 else None

    def _embed_input(self, embed_model, text, is_query):
        """Apply model-specific input formatting before embedding.

        Qwen3-Embedding query side gets an 'Instruct: …\\nQuery: …' prefix;
        documents and all other models pass through unchanged so existing
        embeddings stay consistent.
        """
        if is_query and "qwen3-embedding" in (embed_model or ""):
            return f"Instruct: {self._EMBED_QUERY_INSTRUCT}\nQuery: {text}"
        return text

    def get_embed(self, text, embed_model=None, is_query=False):
        """Return embedding list[float] from the configured embed model.

        is_query=True applies the Qwen3-Embedding query instruction prefix;
        leave it False (default) when embedding documents.
        """
        if embed_model is None:
            import database as db
            embed_model = db.ai_get_settings().get("embed_model", DEFAULT_SETTINGS["embed_model"])
        text = self._embed_input(embed_model, text, is_query)
        self._wait_for_startup_warmup()
        with self._ollama_slot():
            if not self._ensure_model_present(embed_model):
                raise ValueError(f"Embedding model not available: {embed_model}")
            payload = json.dumps({"model": embed_model, "input": text,
                                  "options": {"num_gpu": 0}}).encode()
            req = urllib.request.Request(
                f"{self.ollama_base}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
        embeddings = data.get("embeddings", [])
        if embeddings:
            return embeddings[0]
        raise ValueError("Ollama /api/embed returned no embeddings")

    def _ensure_model_present(self, model: str) -> bool:
        """Ensure an Ollama model exists locally, pulling it if missing."""
        if not model:
            return False
        try:
            with urllib.request.urlopen(f"{self.ollama_base}/api/tags", timeout=10) as resp:
                data = json.loads(resp.read())
            names = [m.get("name", "") for m in data.get("models", [])]
            if model in names or any(name.startswith(model + ":") for name in names):
                return True
        except Exception as e:
            logger.warning(f"Could not query Ollama model list: {e}")
            return False

        logger.info(f"Ollama model missing; pulling {model}")
        payload = json.dumps({"name": model, "stream": False}).encode()
        try:
            req = urllib.request.Request(
                f"{self.ollama_base}/api/pull",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=1800)
            return True
        except Exception as e:
            logger.warning(f"Failed to pull Ollama model {model}: {e}")
            return False

    def _supports_thinking(self, model: str) -> bool:
        """True if the model advertises the 'thinking' capability (e.g. Qwen3.x).

        Ollama rejects a "think" field for models without the capability, so
        callers must gate on this before adding it to a payload.
        """
        if model in self._thinking_caps:
            return self._thinking_caps[model]
        supported = False
        try:
            payload = json.dumps({"model": model}).encode()
            req = urllib.request.Request(
                f"{self.ollama_base}/api/show",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                info = json.loads(resp.read())
            supported = "thinking" in (info.get("capabilities") or [])
        except Exception as e:
            logger.warning(f"Could not query capabilities for {model}: {e}")
            return False  # don't cache a failed lookup
        self._thinking_caps[model] = supported
        return supported

    def _finalize_payload(self, payload_dict: dict) -> dict:
        """Apply model-dependent request fixups before sending to Ollama.

        - Disables hybrid-reasoning 'thinking' so responses start immediately.
        - Drops a negative num_gpu so Ollama auto-places layers; forcing a
          count (the old 99 convention) hard-fails when the model + KV cache
          don't fit in the Jetson's shared RAM, instead of partially offloading.
        """
        if self._supports_thinking(payload_dict.get("model", "")):
            payload_dict["think"] = False
        opts = payload_dict.get("options")
        if opts and int(opts.get("num_gpu", -1)) < 0:
            opts.pop("num_gpu", None)
        return payload_dict

    # ------------------------------------------------------------------
    def _get_doc_embeddings(self):
        """Return cached list of (doc_dict, emb_list) pairs; refreshed every 120 s."""
        import database as db
        now = time.time()
        if self._doc_emb_cache is not None and (now - self._doc_emb_cache_ts) < _DOC_EMB_CACHE_TTL:
            return self._doc_emb_cache
        docs = db.ai_get_documents_with_embeddings()
        result = []
        for doc in docs:
            if doc.get("embedding"):
                try:
                    result.append((doc, json.loads(doc["embedding"])))
                except Exception:
                    pass
        self._doc_emb_cache = result
        self._doc_emb_cache_ts = now
        return result

    # ------------------------------------------------------------------
    def _is_math_query(self, user_message):
        """Return True if the user message appears to require computation."""
        msg_lower = user_message.lower()
        return any(kw in msg_lower for kw in _MATH_KEYWORDS)

    def _is_physics_query(self, user_message):
        """Return True if the query involves physics/ballistics — triggers the agent loop."""
        msg_lower = user_message.lower()
        return any(kw in msg_lower for kw in _PHYSICS_KEYWORDS)

    def _is_ballistic_query(self, user_message):
        """Return True if the query is specifically about bullet trajectory / ballistic drop."""
        msg_lower = user_message.lower()
        return any(kw in msg_lower for kw in _BALLISTIC_SPECIFIC_KEYWORDS)

    def _identify_round(self, user_message):
        """
        Attempt to identify the ammunition from the user's message.
        Returns (v0_mps, bc_g1, description, weight_gr, diam_in, length_in, ref_twist_in) or None.
        """
        msg_lower = user_message.lower()
        for keywords, key in _ROUND_HINTS:
            if all(k in msg_lower for k in keywords):
                return _COMMON_ROUNDS[key]
        return None

    # ------------------------------------------------------------------
    def _eval_expr(self, expr):
        """Safely evaluate a single math expression. Returns (result_str, error_str)."""
        try:
            raw = eval(compile(expr.strip(), "<calc>", "eval"),
                       {"__builtins__": {}}, _CALC_SAFE_NAMES)
            if not isinstance(raw, (int, float)):
                return None, "not a number"
            if _math_mod.isnan(raw):
                return None, "NaN"
            if _math_mod.isinf(raw):
                return str(raw), None
            formatted = f"{raw:.6g}" if isinstance(raw, float) else str(raw)
            return formatted, None
        except ZeroDivisionError:
            return None, "division by zero"
        except Exception as ex:
            return None, str(ex)

    # ------------------------------------------------------------------
    def _process_calc_tags(self, text):
        """Replace [CALC: expr] tags with computed results (bold)."""
        import re
        def _replace(m):
            val, err = self._eval_expr(m.group(1))
            return f"**{val}**" if val is not None else f"[CALC ERROR: {err}]"
        return re.sub(r'\[CALC:\s*(.*?)\]', _replace, text)

    # ------------------------------------------------------------------
    def _calc_agent_pass(self, user_message, settings):
        """
        Two-pass agent loop:
          • For ballistic trajectory queries: directly compute drop using the
            G1 drag model + known round data.  No LLM guessing of formulas.
          • For general physics/math queries: ask the model to emit [CALC:] tags,
            evaluate them, and inject pre-verified results.
        Returns (context_str, had_results:bool).
        """
        import re

        # ── Ballistic trajectory shortcut ──────────────────────────────────
        # Parse range, zero, and round directly from the message, then call
        # ballistic_drop() ourselves.  The LLM cannot hallucinate constants it
        # never knew — we supply the authoritative physics result.
        if self._is_ballistic_query(user_message):
            ballistic_block = self._ballistic_direct_compute(user_message)
            if ballistic_block:
                return ballistic_block, True

        # ── General physics/math: LLM expression-extraction pass ───────────
        model = settings.get("model", DEFAULT_SETTINGS["model"])

        # Build a specialized extraction prompt when round data is available
        round_info = self._identify_round(user_message)
        if round_info:
            v0, bc, desc, *_ = round_info
            round_hint = (
                f"The round in the question is {desc}.\n"
                f"Use these values: v0_mps={v0}, bc_g1={bc}\n"
            )
        else:
            round_hint = ""

        extraction_system = (
            "You are a math expression extractor. "
            "When given a question, output ONLY the Python expressions needed to answer it numerically. "
            "Each expression must be on its own line in the format: [CALC: expression]\n"
            "Available functions: ballistic_drop(range_m, zero_m, v0_mps, bc_g1) → drop in cm; "
            "sqrt, sin, cos, tan, radians, degrees, log, log10, exp, pi, g=9.80665, "
            "mps_to_fps, fps_to_mps, cm_to_inches, km_to_miles, c_to_f, f_to_c, lbs_to_kg.\n"
            "Output NOTHING else — no prose, no labels, no explanations."
        )
        extraction_user = (
            f"{round_hint}Question: {user_message}\n\n"
            "List every expression that must be computed. One [CALC: ...] per line only."
        )

        payload = json.dumps(self._finalize_payload({
            "model": model,
            "messages": [
                {"role": "system", "content": extraction_system},
                {"role": "user",   "content": extraction_user},
            ],
            "stream": False,
            "options": {
                "num_gpu":     int(settings.get("num_gpu",     DEFAULT_SETTINGS["num_gpu"])),
                # Same num_ctx as the chat path: a different context size makes
                # Ollama reload the whole model (~10 s) before answering.
                "num_ctx":     int(settings.get("num_ctx",     DEFAULT_SETTINGS["num_ctx"])),
                "num_batch":   int(settings.get("num_batch",   DEFAULT_SETTINGS["num_batch"])),
                "num_thread":  int(settings.get("num_thread",  DEFAULT_SETTINGS["num_thread"])),
                "temperature": 0.05,
                "num_predict": 300,
            },
        })).encode()

        try:
            req = urllib.request.Request(
                f"{self.ollama_base}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=60)
            extraction = json.loads(resp.read()).get("message", {}).get("content", "")
            logger.info(f"Calc agent extraction: {extraction!r}")
        except Exception as ex:
            logger.warning(f"Calc agent pass failed: {ex}")
            return "", False

        exprs = re.findall(r'\[CALC:\s*(.*?)\]', extraction, re.IGNORECASE)
        if not exprs:
            lines = [l.strip() for l in extraction.splitlines()
                     if l.strip() and not l.strip().startswith('#')]
            exprs = [l for l in lines if any(c in l for c in '+-*/()')]

        if not exprs:
            return "", False

        results = []
        for expr in exprs:
            val, err = self._eval_expr(expr)
            if val is not None:
                results.append(f"  {expr.strip()} = {val}")
            else:
                logger.debug(f"Calc agent skipped bad expr {expr!r}: {err}")

        if not results:
            return "", False

        block = (
            "=== CALCULATOR AGENT RESULTS ===\n"
            "The following calculations were performed by the scientific calculator.\n"
            "You MUST use these exact values in your answer — do NOT recompute or guess:\n"
            + "\n".join(results)
        )
        return block, True

    # ------------------------------------------------------------------
    def _ballistic_direct_compute(self, user_message):
        """
        Directly compute bullet drop + spin drift for a ballistic trajectory question.
        Parses range, zero, and round from the message; runs G1 physics; returns a
        pre-formatted context block.  No LLM involvement in the maths.
        Always outputs both metric and imperial so there is no unit ambiguity.
        """
        import re
        import database as _db

        # Angular unit constants — computed once, shared by ALL conversions.
        # 1 MOA = 1.04720" per 100 yd = 2.90888 cm per 100 m.
        # 1 mrad = 10 cm per 100 m exactly.
        _MOA_CM_PER_100M  = 2.90888
        _MRAD_CM_PER_100M = 10.0

        msg = user_message.lower()

        # ── Extract range (target distance) ────────────────────────────────
        # Explicit unit spellings tried first; bare 'm' is lowest-priority
        # (most ambiguous — could appear in calibre labels like "9mm").
        range_m = None
        for _pat, _scale in [
            (r'(\d+(?:\.\d+)?)\s*(?:meters?|metres?)',  1.0),
            (r'(\d+(?:\.\d+)?)\s*(?:yards?|yds?)\b',   0.9144),
            (r'(\d+(?:\.\d+)?)\s*(?:feet|foot)\b',     0.3048),
            (r'(\d+(?:\.\d+)?)\s*ft\b',                0.3048),
            (r'(\d+(?:\.\d+)?)\s*yd\b',                0.9144),
            (r'(\d+(?:\.\d+)?)\s*m\b',                 1.0),
        ]:
            hits = re.findall(_pat, msg)
            if hits:
                vals = [float(h) * _scale for h in hits]
                vals = [v for v in vals if 20 <= v <= 5000]
                if vals:
                    range_m = max(vals)
                    break

        if range_m is None:
            logger.debug("Ballistic direct compute: could not parse range from message")
            return ""

        # ── Extract zero distance ───────────────────────────────────────────
        # Unit extracted from the regex capture, NOT from the whole message,
        # to avoid cross-contaminating range units with zero units.
        zero_m = 100.0
        _zpat = re.search(
            r'zero(?:ed)?\s+(?:at\s+)?(\d+(?:\.\d+)?)\s*'
            r'(meters?|metres?|yards?|yds?|feet|foot|ft|yd|m)?\b',
            msg
        )
        if _zpat:
            _zval  = float(_zpat.group(1))
            _zunit = (_zpat.group(2) or "m").rstrip("s")   # strip plural suffix
            if _zunit in ("yard", "yd", "yds"):
                _zval *= 0.9144
            elif _zunit in ("feet", "foot", "ft"):
                _zval *= 0.3048
            if 5 <= _zval <= 1000:
                zero_m = _zval

        # ── Identify round ──────────────────────────────────────────────────
        round_data = self._identify_round(user_message)
        if round_data:
            v0, bc, desc, m_gr, d_in, l_in, ref_twist = round_data
        else:
            v0, bc, desc = 975, 0.269, "5.56mm 55gr (assumed)"
            m_gr, d_in, l_in, ref_twist = 55, 0.224, 0.910, 7.0
            logger.debug("Ballistic direct compute: round not identified, using 5.56 55gr defaults")

        # ── Parse twist rate from message (overrides round default) ────────
        twist_in = ref_twist
        twist_explicit = False
        for _tpat in [
            r'\b1\s*[:/]\s*(\d+(?:\.\d+)?)\s*(?:["\']|\btwist\b|\bturn\b|\brifling\b)?',
            r'\bone[\s-]+in[\s-]+(\d+(?:\.\d+)?)\s*(?:["\']|\btwist\b|\bturn\b)?',
        ]:
            _tm = re.search(_tpat, user_message, re.IGNORECASE)
            if _tm:
                _t = float(_tm.group(1))
                if 4.0 <= _t <= 40.0:
                    twist_in = _t
                    twist_explicit = True
                    break

        # ── Physics ─────────────────────────────────────────────────────────
        try:
            drop_cm, tof_s = _ballistic_sim(range_m, zero_m, v0, bc)

            # Angular conversion factors for this range — derived once, used for
            # BOTH drop and spin drift.  One source of truth eliminates unit drift.
            moa_per_cm  = 1.0 / (range_m * _MOA_CM_PER_100M  / 100.0)
            mrad_per_cm = 1.0 / (range_m * _MRAD_CM_PER_100M / 100.0)

            # Bullet drop (all representations derived from drop_cm)
            drop_abs_cm = abs(drop_cm)
            drop_in     = round(drop_abs_cm / 2.54,  2)
            drop_ft     = round(drop_abs_cm / 30.48, 2)
            drop_moa    = round(drop_abs_cm * moa_per_cm,  1)
            drop_mrad   = round(drop_abs_cm * mrad_per_cm, 2)
            direction   = "below" if drop_cm < 0 else "above"

            # Spin drift — Litz: SD_in = 1.25 × Sg × TOF^1.83
            sg      = _miller_sg(m_gr, d_in, l_in, twist_in, v0)
            sd_in   = round(1.25 * sg * tof_s ** 1.83, 2)
            sd_cm   = round(sd_in * 2.54, 1)
            sd_moa  = round(sd_cm * moa_per_cm,  2)
            sd_mrad = round(sd_cm * mrad_per_cm, 3)

            twist_src = "user-specified" if twist_explicit else "standard barrel for this round"

            # Range/zero in both units for the header
            range_yd = round(range_m * 1.09361)
            zero_yd  = round(zero_m  * 1.09361)
            v0_fps   = _mps_to_fps(v0)

            # Cross-check: back-convert sd_moa → cm to expose any formula divergence
            _sd_check_cm = round(sd_moa / moa_per_cm, 1)

            block = (
                f"=== BALLISTIC CALCULATOR RESULTS ===\n"
                f"Round     : {desc}\n"
                f"Muzzle vel: {v0} m/s  ({v0_fps} fps)\n"
                f"BC (G1)   : {bc}\n"
                f"Zero      : {zero_m:.0f} m  ({zero_yd} yd)\n"
                f"Target    : {range_m:.0f} m  ({range_yd} yd)\n"
                f"\n"
                f"BULLET DROP at {range_m:.0f} m / {range_yd} yd  (zeroed {zero_m:.0f} m):\n"
                f"  {drop_abs_cm} cm  |  {drop_in} in  |  {drop_ft} ft  — {direction} LoS\n"
                f"  {drop_moa} MOA {direction}  |  {drop_mrad} mrad {direction}\n"
                f"\n"
                f"Time of flight (G1 sim): {tof_s} s\n"
                f"\n"
                f"=== SPIN DRIFT (verified physics — do not recalculate) ===\n"
                f"Barrel twist : 1:{twist_in:.0f}\" RH  [{twist_src}]\n"
                f"Miller Sg    : {sg:.3f}  "
                f"(n={twist_in/d_in:.2f} cal/turn, L={l_in/d_in:.3f} cal)\n"
                f"Litz formula : SD = 1.25 × {sg:.3f} × {tof_s}^1.83\n"
                f"RESULT       : {sd_in} in  ({sd_cm} cm)  to the RIGHT\n"
                f"Angular      : {sd_moa} MOA  |  {sd_mrad} mrad\n"
                f"Cross-check  : {sd_moa} MOA × {round(1.0/moa_per_cm, 2)} cm/MOA"
                f" = {_sd_check_cm} cm  (expect {sd_cm} cm)\n"
                f"Direction    : RH twist → RIGHT.  LH twist → LEFT.\n"
                f"If barrel twist differs from 1:{twist_in:.0f}\", specify it.\n"
                f"\n"
                f"G1 drag model · sea level · standard atmosphere.\n"
                f"Ranges shown in both m and yd — no unit conflation."
            )
            logger.info(
                f"Ballistic direct compute: {desc} {range_m:.0f}m zero={zero_m:.0f}m "
                f"drop={drop_cm}cm/{drop_in}in tof={tof_s}s sg={sg:.2f} twist={twist_in}\""
            )
            return block
        except Exception as ex:
            logger.warning(f"Ballistic direct compute failed: {ex}")
            return ""

    # ------------------------------------------------------------------
    def rag_search(self, query, top_k=5, embed_model=None):
        """Return (docs, top_score) using BM25-boosted cosine similarity.

        docs      — list of matching doc dicts (up to top_k, hybrid score >= _RAG_MIN_SCORE)
        top_score — raw cosine similarity of the best retrieved doc (0.0 if none)

        Hybrid score = max(v, 0.6*v + 0.4*bm25_norm) when v >= _BM25_GATE, else v.
        BM25 can only boost semantically plausible candidates — never surface an
        unrelated doc on keyword coincidence (e.g. "temperature" in an AI-params doc
        matching a cooking question when the vector similarity is near zero).
        """
        doc_embs = self._get_doc_embeddings()
        if not doc_embs:
            return [], 0.0
        try:
            query_emb = self.get_embed(query, embed_model=embed_model, is_query=True)
        except Exception as e:
            logger.warning(f"RAG embed failed: {e}")
            return [], 0.0
        # Skip any stored vectors whose dimensions don't match the query (e.g.
        # docs left over from a previous embed model). Mismatched vectors would
        # crash numpy.dot or silently score as garbage.
        qdim = len(query_emb)
        usable = [(doc, emb) for doc, emb in doc_embs if len(emb) == qdim]
        if len(usable) < len(doc_embs):
            logger.warning(
                f"RAG: {len(doc_embs) - len(usable)} doc embedding(s) have a "
                f"dimension != {qdim} (stale embed model?) — skipped. Re-embed needed."
            )
        if not usable:
            return [], 0.0

        # Vector scores for all docs
        vec_scores = {doc["id"]: cosine_similarity(query_emb, emb) for doc, emb in usable}
        doc_by_id  = {doc["id"]: doc for doc, emb in usable}

        # BM25 scores — normalise to [0, 1] (bm25() returns negative; more negative = better)
        bm25_raw = database.ai_fts_search(query, max_results=top_k * 4)
        if bm25_raw:
            min_s = min(s for _, s in bm25_raw)
            max_s = max(s for _, s in bm25_raw)
            bm25_norm = (
                {did: (s - max_s) / (min_s - max_s) for did, s in bm25_raw}
                if min_s < max_s else
                {did: 1.0 for did, _ in bm25_raw}
            )
        else:
            bm25_norm = {}

        # Hybrid scoring: BM25 only applied to semantically plausible candidates
        scored = []
        for doc_id, v in vec_scores.items():
            b = bm25_norm.get(doc_id, 0.0)
            if v >= _BM25_GATE and b > 0.0:
                hybrid = max(v, 0.6 * v + 0.4 * b)
            else:
                hybrid = v
            scored.append((hybrid, v, doc_by_id[doc_id]))

        # Sort; capture raw cosine of the top doc so the confidence footer
        # reflects actual semantic similarity, not BM25-inflated hybrid score.
        scored.sort(key=lambda x: x[0], reverse=True)
        top_score = scored[0][1] if scored else 0.0

        # Topic-router boost: detect the query's category and give matching docs
        # a small edge (+8%) so the right cluster surfaces even when border-case
        # scores are close.
        cat = self._classify_query_category(query)
        if cat:
            cat_tag_hints = self._DOC_CATEGORY_TAGS.get(cat, [])
            def _in_cat(doc):
                tags_lower = (doc.get("tags") or "").lower()
                return any(t in tags_lower for t in cat_tag_hints)
            scored = [(h * 1.08 if _in_cat(doc) else h, v, doc) for h, v, doc in scored]
            scored.sort(key=lambda x: x[0], reverse=True)

        scored = [(h, doc) for h, v, doc in scored if h >= _RAG_MIN_SCORE]
        return [doc for _, doc in scored[:top_k]], top_score

    # ------------------------------------------------------------------
    def build_context(self, user_message, settings=None):
        """Build system context string + metadata.

        Returns (context_str, meta) where meta is a dict with keys:
          has_rag        — bool: at least one RAG doc was retrieved
          rag_top_score  — float: highest cosine similarity (0.0 if no match)
          has_live_data  — bool: mesh node / alert context was injected
          has_system_stats — bool: CPU/RAM/temp stats were injected
          is_live_query  — bool: query was classified as a live-data query (RAG skipped)
        """
        import database as db
        import system_stats
        if settings is None:
            settings = db.ai_get_settings()
        parts = []
        meta = {
            "has_rag": False,
            "rag_top_score": 0.0,
            "has_live_data": False,
            "has_system_stats": False,
            "is_live_query": False,
            "has_gps_context": False,
            "has_self_doc": False,
            "user_location": None,
            "gps_fix": None,
        }

        # Unit preference — read here for GPS source-conversion; the text
        # directive is injected into base_system by the caller (not into parts[])
        # so the model follows it silently rather than narrating it.
        try:
            _units = db.get_app_settings().get("units", "metric")
        except Exception:
            _units = "metric"
        meta["units"] = _units

        # System stats context
        try:
            s = system_stats.get_stats()
            if s:
                lines = ["=== SYSTEM STATUS ==="]
                if "cpu_pct" in s:
                    lines.append(f"CPU usage (avg): {s['cpu_pct']}%")
                if s.get("cpu_cores"):
                    core_str = "  ".join(
                        f"Core{i}: {c['pct']}%@{c['freq_mhz']}MHz"
                        for i, c in enumerate(s["cpu_cores"])
                    )
                    lines.append(f"CPU cores ({len(s['cpu_cores'])} total): {core_str}")
                if "gpu_pct" in s:
                    lines.append(f"GPU usage: {s['gpu_pct']}%")
                if "ram_used_mb" in s:
                    lines.append(f"RAM: {s['ram_used_mb']}MB / {s['ram_total_mb']}MB ({s['ram_pct']}%)")
                if "disk_used_gb" in s:
                    lines.append(f"Disk: {s['disk_used_gb']}GB / {s['disk_total_gb']}GB ({s['disk_pct']}%)")
                temps = {k: v for k, v in s.items() if k.startswith("temp_")}
                if temps:
                    temp_str = "  |  ".join(f"{k.replace('temp_','').upper()}: {v}°C" for k, v in temps.items())
                    lines.append(f"Temperatures ({len(temps)} sensors): {temp_str}")
                power_in = s.get("power_in_mw")
                if power_in:
                    lines.append(f"Total power draw: {power_in}mW ({round(power_in/1000,1)}W)")
                if "uptime_s" in s:
                    u = s["uptime_s"]
                    uptime_str = f"{u//3600}h {(u%3600)//60}m"
                    lines.append(f"Uptime: {uptime_str}")
                parts.append("\n".join(lines))
                meta["has_system_stats"] = True
        except Exception as e:
            logger.warning(f"System stats context injection failed: {e}")

        # ── GPS / Location agent ─────────────────────────────────────────────
        # Always inject location context so Ray knows where the user is for
        # every prompt — not just explicitly location-sensitive ones.  The
        # user's stated location takes priority; the device GPS fix is always
        # appended so Ray can ground any answer in the user's actual surroundings.
        user_loc  = _extract_user_location(user_message)
        if True:
            try:
                loc_lines = ["=== CURRENT POSITION ==="]

                if user_loc:
                    if user_loc["type"] == "coords":
                        loc_lines.append(
                            f"User-provided location (USE THIS as the primary location): "
                            f"{_fmt_coord(user_loc['lat'], user_loc['lon'])} "
                            f"(decimal: {user_loc['lat']:.5f}, {user_loc['lon']:.5f})"
                        )
                    else:
                        loc_lines.append(
                            f"User-provided location (USE THIS as the primary location): "
                            f"{user_loc['name']}"
                        )

                gps_fix = None
                if self.gps_manager is not None:
                    gps_fix = getattr(self.gps_manager, "current_fix", None)

                if gps_fix and gps_fix.get("latitude") is not None:
                    lat  = gps_fix["latitude"]
                    lon  = gps_fix["longitude"]
                    alt  = gps_fix.get("altitude")
                    spd  = gps_fix.get("speed")
                    hdg  = gps_fix.get("heading")
                    sats = gps_fix.get("sats_in_view") or gps_fix.get("sats")
                    fix_type = gps_fix.get("fix_label") or gps_fix.get("fix_type_label") or gps_fix.get("fix_type", "")
                    label = "Atlas Control GPS fix (SparkFun M9N)" if not user_loc else "Atlas Control GPS fix (for reference / distance math)"
                    city = _reverse_geocode(lat, lon)
                    gps_line = f"{label}: {_fmt_coord(lat, lon)} (decimal: {lat:.5f}, {lon:.5f})"
                    if city:
                        gps_line += f" | location: {city}"
                    if alt is not None:
                        if _units == "imperial":
                            gps_line += f" | altitude: {alt * 3.28084:.1f} ft"
                        else:
                            gps_line += f" | altitude: {alt:.1f} m"
                    if spd is not None:
                        if _units == "imperial":
                            gps_line += f" | speed: {spd * 2.23694:.1f} mph"
                        else:
                            gps_line += f" | speed: {spd:.1f} m/s"
                    if hdg is not None:
                        gps_line += f" | heading: {hdg:.1f}°"
                    if sats is not None:
                        gps_line += f" | satellites: {sats}"
                    if fix_type:
                        gps_line += f" | fix: {fix_type}"
                    loc_lines.append(gps_line)
                elif not user_loc:
                    loc_lines.append("Atlas Control GPS fix (SparkFun M9N): not available (no fix or GPS not connected)")

                loc_lines.append(
                    "INSTRUCTION: Always use the position above to ground your answer. "
                    "Reference the specific region, local conditions, terrain, climate, regulations, "
                    "or hazards relevant to that location — even when the user does not explicitly ask about location."
                )
                parts.append("\n".join(loc_lines))
                meta["has_gps_context"] = True
                meta["user_location"] = user_loc
                meta["gps_fix"] = gps_fix
                logger.info(f"Location agent injected: user_loc={user_loc}, gps_available={gps_fix is not None}")
            except Exception as e:
                logger.warning(f"Location agent failed: {e}")

        # Dashboard context (nodes, map, telemetry, messages, topology, alerts, channels)
        if settings.get("inject_mesh_context", "true").lower() == "true":
            try:
                now = int(time.time())

                def _ago(ts):
                    if not ts:
                        return "never"
                    d = now - int(ts)
                    if d < 60:
                        return f"{d}s ago"
                    if d < 3600:
                        return f"{d//60}m ago"
                    return f"{d//3600}h ago"

                def _node_name(n):
                    return n.get("alias") or n.get("long_name") or n.get("short_name") or n.get("node_id")

                # ── Nodes ──────────────────────────────────────────────
                nodes = db.get_all_nodes()
                online_thresh = int(settings.get("node_online_window_h", "2")) * 3600
                online = [n for n in nodes if n.get("last_heard") and (now - n["last_heard"]) < online_thresh]
                offline = [n for n in nodes if n not in online]

                msg_lower = user_message.lower()
                want_pos = any(w in msg_lower for w in ("location","position","map","gps","lat","lon","coord","where"))
                want_topo = any(w in msg_lower for w in ("topology","link","snr","rssi","signal","hop","route","path","neighbor"))
                want_tel = any(w in msg_lower for w in ("telemetry","battery","voltage","humidity","pressure","temp","sensor"))
                want_msg = any(w in msg_lower for w in ("message","chat","said","text","sent","received","broadcast"))

                node_lines = []
                for n in nodes:
                    status = "ONLINE" if n in online else "OFFLINE"
                    parts_n = [f"  [{status}] {_node_name(n)}"]
                    if n.get("role"):
                        parts_n.append(f"role={n['role']}")
                    if n.get("battery_level") is not None:
                        parts_n.append(f"bat={n['battery_level']}%")
                    if n.get("snr") is not None:
                        parts_n.append(f"SNR={n['snr']}dB")
                    parts_n.append(f"seen={_ago(n.get('last_heard'))}")
                    node_lines.append(" | ".join(parts_n))

                # Prepend a brief GPS summary only when the location agent above
                # did not already inject full GPS context (avoids duplication).
                atlas_pos_line = ""
                if not meta.get("has_gps_context"):
                    try:
                        _af = None
                        if self.gps_manager is not None:
                            _af = getattr(self.gps_manager, "current_fix", None)
                        if _af and _af.get("latitude") is not None:
                            _alat, _alon = _af["latitude"], _af["longitude"]
                            _acity = _reverse_geocode(_alat, _alon) or ""
                            atlas_pos_line = (
                                f"=== ATLAS CONTROL LOCATION (SparkFun M9N GPS) ===\n"
                                f"Coordinates: {_fmt_coord(_alat, _alon)} (decimal: {_alat:.5f}, {_alon:.5f})"
                            )
                            if _acity:
                                atlas_pos_line += f"\nNearest city: {_acity}"
                            _afix = _af.get("fix_label") or _af.get("fix_type_label") or str(_af.get("fix_type", ""))
                            if _afix:
                                atlas_pos_line += f"\nFix type: {_afix}"
                        else:
                            atlas_pos_line = "=== ATLAS CONTROL LOCATION ===\nGPS fix not yet available."
                    except Exception:
                        pass

                mesh_section = (
                    (atlas_pos_line + "\n\n" if atlas_pos_line else "")
                    + f"=== MESH NODES ({len(online)} online / {len(offline)} offline / {len(nodes)} total) ===\n"
                    + "\n".join(node_lines)
                )

                # ── Channels ───────────────────────────────────────────
                try:
                    channels = db.get_channels()
                    ch_str = ", ".join(f"Ch{c['channel_num']}={c['name']}" for c in channels)
                    mesh_section += f"\n\n=== CHANNELS ===\n{ch_str}"
                except Exception:
                    pass

                # ── Recent Messages ─────────────────────────────────────
                try:
                    recent_msgs = db.get_messages(limit=10)
                    if recent_msgs and (want_msg or not (want_pos or want_topo or want_tel)):
                        msg_lines = []
                        node_map = {n.get("node_id"): _node_name(n) for n in nodes}
                        for m in reversed(recent_msgs):
                            ts = _ago(m.get("rx_time"))
                            frm_id = m.get("from_id", "?")
                            to_id = m.get("to_id", "?")
                            frm = node_map.get(frm_id, frm_id)
                            to = "Broadcast" if to_id in ("4294967295", "!ffffffff", "^all") else node_map.get(to_id, to_id)
                            ch = m.get("channel", 0)
                            txt = (m.get("text") or "")[:120]
                            hops = m.get("hop_start", 0) - m.get("hop_limit", 0) if m.get("hop_start") else "?"
                            msg_lines.append(f"  [{ts}] Ch{ch} {frm}→{to} (hops:{hops}): {txt}")
                        mesh_section += "\n\n=== RECENT MESH MESSAGES (newest last) ===\n" + "\n".join(msg_lines)
                except Exception:
                    pass

                # ── Telemetry (latest per node) ─────────────────────────
                try:
                    if not want_tel:
                        raise Exception("skip")
                    # Single SQL query returns one row per node — no Python-side dedup
                    latest_tel = {t["node_id"]: t for t in db.get_latest_telemetry_per_node(hours=24)}
                    if latest_tel:
                        tel_lines = []
                        for nid, t in latest_tel.items():
                            node = next((n for n in nodes if n.get("node_id") == nid), None)
                            name = _node_name(node) if node else nid
                            fields = [f"  {name}:"]
                            if t.get("battery_level") is not None:
                                fields.append(f"battery={t['battery_level']}%")
                            if t.get("voltage") is not None:
                                fields.append(f"voltage={t['voltage']}V")
                            if t.get("temperature") is not None:
                                fields.append(f"temp={t['temperature']}°C")
                            if t.get("relative_humidity") is not None:
                                fields.append(f"humidity={t['relative_humidity']}%")
                            if t.get("barometric_pressure") is not None:
                                fields.append(f"pressure={t['barometric_pressure']}hPa")
                            if t.get("channel_util") is not None:
                                fields.append(f"ch_util={t['channel_util']}%")
                            fields.append(f"({_ago(t.get('timestamp'))})")
                            tel_lines.append(" ".join(fields))
                        mesh_section += "\n\n=== NODE TELEMETRY (latest) ===\n" + "\n".join(tel_lines)
                except Exception:
                    pass

                # ── Positions / Map ─────────────────────────────────────
                try:
                    if not want_pos:
                        raise Exception("skip")
                    # Single SQL query returns one row per node — no Python-side dedup
                    latest_pos = {p["node_id"]: p for p in db.get_latest_positions_per_node()}
                    if latest_pos:
                        pos_lines = []
                        for nid, p in latest_pos.items():
                            node = next((n for n in nodes if n.get("node_id") == nid), None)
                            name = _node_name(node) if node else nid
                            lat, lon = p.get("latitude"), p.get("longitude")
                            if lat and lon:
                                city = _reverse_geocode(lat, lon)
                                line = f"  {name}: lat={lat:.5f} lon={lon:.5f}"
                                if city:
                                    line += f" ({city})"
                                if p.get("altitude"):
                                    line += f" alt={p['altitude']}m"
                                if p.get("speed") is not None:
                                    line += f" speed={p['speed']}m/s"
                                if p.get("heading") is not None:
                                    line += f" heading={p['heading']}°"
                                if p.get("sats_in_view"):
                                    line += f" sats={p['sats_in_view']}"
                                line += f" ({_ago(p.get('timestamp'))})"
                                pos_lines.append(line)
                        if pos_lines:
                            mesh_section += "\n\n=== NODE POSITIONS (MAP) ===\n" + "\n".join(pos_lines)
                except Exception:
                    pass

                # ── Topology / Link Quality ─────────────────────────────
                try:
                    if not want_topo:
                        raise Exception("skip")
                    links = db.get_topology()
                    if links:
                        link_lines = []
                        for lk in links[:10]:
                            fn = next((_node_name(n) for n in nodes if n.get("node_id") == lk.get("from_id")), lk.get("from_id","?"))
                            tn = next((_node_name(n) for n in nodes if n.get("node_id") == lk.get("to_id")), lk.get("to_id","?"))
                            link_lines.append(f"  {fn} → {tn}: SNR={lk.get('snr')}dB RSSI={lk.get('rssi')}dBm ({_ago(lk.get('timestamp'))})")
                        mesh_section += "\n\n=== MESH TOPOLOGY (LINK QUALITY) ===\n" + "\n".join(link_lines)
                except Exception:
                    pass

                # ── Alerts ─────────────────────────────────────────────
                try:
                    alerts = db.get_alerts(limit=10)
                    if alerts:
                        alert_lines = []
                        for a in alerts:
                            ack = "ACK" if a.get("acknowledged") else "ACTIVE"
                            alert_lines.append(f"  [{ack}] [{a.get('severity','?').upper()}] {a.get('title','')}: {a.get('message','')} ({_ago(a.get('timestamp'))})")
                        mesh_section += "\n\n=== ALERTS ===\n" + "\n".join(alert_lines)
                except Exception:
                    pass

                parts.append(mesh_section)
                meta["has_live_data"] = True
            except Exception as e:
                logger.warning(f"Dashboard context injection failed: {e}")

        # Self-knowledge: when the user asks Ray about its own brain/pipeline,
        # inject the architecture doc directly — deterministic, no embedding
        # roll, and immune to the live-data RAG skip below (words like
        # "memory" or "model" would otherwise route the question away).
        if _is_self_query(user_message):
            parts.append(
                "=== SELF-KNOWLEDGE (this is YOUR OWN architecture — answer questions about "
                "how you think, retrieve, index, or process information from this, in first person) ===\n"
                + RAY_SELF_DOC["content"]
                + "\n=== END SELF-KNOWLEDGE ==="
            )
            meta["has_self_doc"] = True
            logger.info("Self-query detected: injected Ray self-architecture doc")

        # RAG context — skip if the query is about live dashboard data (already injected above)
        msg_lower = user_message.lower()
        is_live_query = any(kw in msg_lower for kw in _LIVE_DATA_KEYWORDS)
        meta["is_live_query"] = is_live_query
        if settings.get("rag_enabled", "true").lower() == "true" and not is_live_query:
            try:
                top_k = int(settings.get("rag_top_k", DEFAULT_SETTINGS["rag_top_k"]))
                embed_model = settings.get("embed_model", DEFAULT_SETTINGS["embed_model"])
                relevant_docs, rag_top_score = self.rag_search(user_message, top_k=top_k, embed_model=embed_model)
                meta["rag_top_score"] = rag_top_score
                if relevant_docs:
                    meta["has_rag"] = True
                    rag_lines = []
                    for doc in relevant_docs:
                        rag_lines.append(
                            f"--- {doc['title']} ---\n{doc['content']}"
                        )
                    rag_block = (
                        "=== KNOWLEDGE BASE (use as primary source — supplement with training knowledge if needed) ===\n"
                        + "\n\n".join(rag_lines)
                        + "\n=== END KNOWLEDGE BASE ==="
                    )
                    parts.append(rag_block)
            except Exception as e:
                logger.warning(f"RAG search failed: {e}")

        # Calculator tool instruction — injected when query involves math/physics
        if self._is_math_query(user_message):
            parts.append(_CALC_INSTRUCTION)

        return "\n\n".join(parts), meta

    # ------------------------------------------------------------------
    def _confidence_label(self, meta):
        """Return a confidence footer derived from build_context metadata.

        Confidence tiers:
          HIGH   — strong RAG match (score ≥ 0.70) or live data present
          MEDIUM — moderate RAG match (0.50 ≤ score < 0.70) or system stats only
          LOW    — weak RAG match (below 0.50 but above threshold) or training only
        Sources are listed explicitly so the user knows what grounded the answer.
        """
        score     = meta.get("rag_top_score", 0.0)
        has_rag   = meta.get("has_rag", False)
        has_live  = meta.get("has_live_data", False)
        has_stats = meta.get("has_system_stats", False)
        has_gps   = meta.get("has_gps_context", False)
        has_self  = meta.get("has_self_doc", False)
        user_loc  = meta.get("user_location")

        # Build source list
        sources = []
        if has_self:
            sources.append("Self-Architecture Doc")
        if has_rag:
            sources.append("Knowledge Base")
        if has_live:
            sources.append("Live Mesh Data")
        if has_stats and not has_live:
            sources.append("System Stats")
        if has_gps:
            if user_loc and user_loc.get("type") == "named":
                sources.append(f"User Location ({user_loc['name']})")
            elif user_loc and user_loc.get("type") == "coords":
                sources.append(f"User Location ({user_loc['lat']:.4f}, {user_loc['lon']:.4f})")
            else:
                sources.append("GPS Fix")
        if not sources:
            sources.append("Training Knowledge")
        source_str = " + ".join(sources)

        # Determine tier
        if has_live or has_self:
            # Live data and the self-architecture doc are ground truth; RAG is a bonus
            tier = "HIGH"
        elif has_rag:
            if score >= 0.70:
                tier = "HIGH"
            elif score >= 0.50:
                tier = "MEDIUM"
            else:
                tier = "LOW"
        elif has_stats:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        label = f"{tier} | Source: {source_str}"
        logger.info(f"Confidence: {label} (rag={has_rag}, score={score:.3f}, live={has_live})")
        return f"\n\n---\nConfidence: {label}"

    # ------------------------------------------------------------------
    def chat(self, chat_id, user_message):
        """Full pipeline: build context, call Ollama, store messages, return dict."""
        import database as db
        self._wait_for_startup_warmup()
        settings = db.ai_get_settings()  # fetch once; passed downstream
        model = settings.get("model", DEFAULT_SETTINGS["model"])
        keep_alive = f"{settings.get('keep_alive_hours', DEFAULT_SETTINGS['keep_alive_hours'])}h"
        base_system = settings.get("system_prompt", DEFAULT_SETTINGS["system_prompt"])

        # Build augmented system prompt (settings already loaded — no extra DB call)
        context, ctx_meta = self.build_context(user_message, settings=settings)

        # Agent loop: for physics/ballistics queries, run the two-pass calculator agent
        # before the final answer so the model is given pre-verified numbers.
        if self._is_physics_query(user_message):
            calc_block, had_results = self._calc_agent_pass(user_message, settings)
            if had_results:
                context = (context + "\n\n" + calc_block).strip()
                # Override instruction: tell model the numbers are already computed
                context += (
                    "\n\n=== INSTRUCTION FOR THIS RESPONSE ===\n"
                    "The CALCULATOR AGENT RESULTS above contain pre-verified drop, correction, spin drift, and time-of-flight values. "
                    "Use those exact numbers as-is — do NOT recalculate the values listed. "
                    "Present them directly to the user without deliberation. "
                    "For any quantity not listed above, answer from your knowledge."
                )

        # Append unit directive to system prompt as a single sentence — small models
        # follow one-line system directives silently; annotated context blocks get narrated.
        _units = ctx_meta.get("units", "metric")
        if _units == "imperial":
            base_system += "\nAlways express real-world measurements in imperial units (feet, miles, lb, °F, gallons). Show metric only in parentheses."
        else:
            base_system += "\nAlways express real-world measurements in metric units (meters, km, kg, °C, liters). Show imperial only in parentheses."

        system_content = base_system
        if context:
            system_content = base_system + "\n\n" + context

        # Load history — cap at last 8 messages to keep context tight
        history = db.ai_get_messages(chat_id)
        recent_history = [m for m in history if m["role"] in ("user", "assistant")][-8:]
        messages = [{"role": "system", "content": system_content}]
        for msg in recent_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        loc_prefix = _build_location_prefix(ctx_meta)
        messages.append({"role": "user", "content": loc_prefix + user_message})

        # Save user message (without the injected location prefix)
        db.ai_add_message(chat_id, "user", user_message)

        # Call Ollama
        t0 = time.time()
        payload = json.dumps(self._finalize_payload({
            "model": model,
            "messages": messages,
            "keep_alive": keep_alive,
            "stream": False,
            "options": {
                "num_gpu":    int(settings.get("num_gpu",    DEFAULT_SETTINGS["num_gpu"])),
                "num_ctx":    int(settings.get("num_ctx",    DEFAULT_SETTINGS["num_ctx"])),
                "num_thread": int(settings.get("num_thread", DEFAULT_SETTINGS["num_thread"])),
                "num_batch":  int(settings.get("num_batch",  DEFAULT_SETTINGS["num_batch"])),
                "temperature": float(settings.get("temperature", DEFAULT_SETTINGS["temperature"])),
                "top_p":      float(settings.get("top_p",    DEFAULT_SETTINGS["top_p"])),
                "top_k":      int(settings.get("top_k",      DEFAULT_SETTINGS["top_k"])),
                "num_predict": int(settings.get("num_predict", DEFAULT_SETTINGS["num_predict"])),
            },
        })).encode()
        req = urllib.request.Request(
            f"{self.ollama_base}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._ollama_slot():
            resp = urllib.request.urlopen(req, timeout=180)
            result = json.loads(resp.read())
        duration_ms = int((time.time() - t0) * 1000)

        content = result.get("message", {}).get("content", "")
        eval_count = result.get("eval_count")
        eval_duration = result.get("eval_duration")
        tokens = eval_count

        # Evaluate any [CALC: expr] tags the model emitted
        content = self._process_calc_tags(content)

        # Append confidence label derived from what was actually injected
        try:
            content += self._confidence_label(ctx_meta)
        except Exception as e:
            logger.error(f"Confidence label error: {e}")
            content += "\n\n---\nConfidence: UNKNOWN"

        # Compute tok/s
        tok_per_sec = None
        if eval_count and eval_duration and eval_duration > 0:
            tok_per_sec = round(eval_count / (eval_duration / 1e9), 1)

        # Save assistant message
        db.ai_add_message(chat_id, "assistant", content, tokens=tokens, duration_ms=duration_ms)

        # Update chat title from first user message if still default
        chat = db.ai_get_chat(chat_id)
        if chat and chat.get("title") == "New Chat":
            short_title = user_message[:40].strip()
            if len(user_message) > 40:
                short_title += "…"
            db.ai_update_chat_title(chat_id, short_title)

        return {
            "content": content,
            "model": model,
            "eval_count": eval_count,
            "eval_duration": eval_duration,
            "duration_ms": duration_ms,
            "tok_per_sec": tok_per_sec,
        }

    # ------------------------------------------------------------------
    def chat_stream(self, chat_id, user_message):
        """Streaming pipeline: yields str tokens, then a final metadata dict."""
        import database as db
        self._wait_for_startup_warmup()
        settings = db.ai_get_settings()  # fetch once; passed downstream
        model = settings.get("model", DEFAULT_SETTINGS["model"])
        keep_alive = f"{settings.get('keep_alive_hours', DEFAULT_SETTINGS['keep_alive_hours'])}h"
        base_system = settings.get("system_prompt", DEFAULT_SETTINGS["system_prompt"])

        context, ctx_meta = self.build_context(user_message, settings=settings)

        # Agent loop: run calculator agent pass before streaming so numbers are pre-verified
        if self._is_physics_query(user_message):
            calc_block, had_results = self._calc_agent_pass(user_message, settings)
            if had_results:
                context = (context + "\n\n" + calc_block).strip()
                context += (
                    "\n\n=== INSTRUCTION FOR THIS RESPONSE ===\n"
                    "The CALCULATOR AGENT RESULTS above contain pre-verified drop, correction, spin drift, and time-of-flight values. "
                    "Use those exact numbers as-is — do NOT recalculate the values listed. "
                    "Present them directly to the user without deliberation. "
                    "For any quantity not listed above, answer from your knowledge."
                )

        _units = ctx_meta.get("units", "metric")
        if _units == "imperial":
            base_system += "\nAlways express real-world measurements in imperial units (feet, miles, lb, °F, gallons). Show metric only in parentheses."
        else:
            base_system += "\nAlways express real-world measurements in metric units (meters, km, kg, °C, liters). Show imperial only in parentheses."

        system_content = base_system
        if context:
            system_content = base_system + "\n\n" + context

        history = db.ai_get_messages(chat_id)
        # Cap history at last 8 turns (same as non-streaming path)
        recent_history = [m for m in history if m["role"] in ("user", "assistant")][-8:]
        messages = [{"role": "system", "content": system_content}]
        for msg in recent_history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        loc_prefix = _build_location_prefix(ctx_meta)
        messages.append({"role": "user", "content": loc_prefix + user_message})

        db.ai_add_message(chat_id, "user", user_message)

        payload = json.dumps(self._finalize_payload({
            "model": model,
            "messages": messages,
            "keep_alive": keep_alive,
            "stream": True,
            "options": {
                "num_gpu":    int(settings.get("num_gpu",    DEFAULT_SETTINGS["num_gpu"])),
                "num_ctx":    int(settings.get("num_ctx",    DEFAULT_SETTINGS["num_ctx"])),
                "num_thread": int(settings.get("num_thread", DEFAULT_SETTINGS["num_thread"])),
                "num_batch":  int(settings.get("num_batch",  DEFAULT_SETTINGS["num_batch"])),
                "temperature": float(settings.get("temperature", DEFAULT_SETTINGS["temperature"])),
                "top_p":      float(settings.get("top_p",    DEFAULT_SETTINGS["top_p"])),
                "top_k":      int(settings.get("top_k",      DEFAULT_SETTINGS["top_k"])),
                "num_predict": int(settings.get("num_predict", DEFAULT_SETTINGS["num_predict"])),
            },
        })).encode()
        req = urllib.request.Request(
            f"{self.ollama_base}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.time()
        full_content = ""
        eval_count = None
        eval_duration = None

        with self._ollama_slot():
            resp = urllib.request.urlopen(req, timeout=300)
            try:
                for raw_line in resp:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except Exception:
                        continue
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        full_content += token
                        yield token
                    if chunk.get("done"):
                        eval_count = chunk.get("eval_count")
                        eval_duration = chunk.get("eval_duration")
                        break
            finally:
                resp.close()

        # If the model emitted [CALC: expr] tags, resolve them and emit a correction chunk
        processed = self._process_calc_tags(full_content)
        if processed != full_content:
            # Yield a separator + the fully computed version so the client sees the results
            correction = "\n\n---\n*Computed:*\n" + processed[len(full_content):].strip() if False else processed
            # Replace approach: yield a special marker so the frontend knows to replace content
            yield "\x00REPLACE\x00" + processed
            full_content = processed

        # Append confidence label derived from what was actually injected
        try:
            confidence_suffix = self._confidence_label(ctx_meta)
        except Exception as e:
            logger.error(f"Confidence label error: {e}")
            confidence_suffix = "\n\n---\nConfidence: UNKNOWN"
        yield confidence_suffix
        full_content += confidence_suffix

        duration_ms = int((time.time() - t0) * 1000)
        tok_per_sec = None
        if eval_count and eval_duration and eval_duration > 0:
            tok_per_sec = round(eval_count / (eval_duration / 1e9), 1)

        db.ai_add_message(chat_id, "assistant", full_content, tokens=eval_count, duration_ms=duration_ms)

        chat = db.ai_get_chat(chat_id)
        if chat and chat.get("title") == "New Chat":
            short_title = user_message[:40].strip()
            if len(user_message) > 40:
                short_title += "…"
            db.ai_update_chat_title(chat_id, short_title)

        yield {"done": True, "model": model, "eval_count": eval_count,
               "eval_duration": eval_duration, "duration_ms": duration_ms, "tok_per_sec": tok_per_sec}
