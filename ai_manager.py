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
    "9mm_115":   (370,  0.145, "9mm Luger 115gr FMJ",                  115,  0.355, 0.680, 10.0),
    "380_95":    (290,  0.115, ".380 ACP 95gr FMJ",                     95,  0.355, 0.550, 16.0),
    "45acp_230": (259,  0.195, ".45 ACP 230gr FMJ",                    230,  0.452, 0.800, 16.0),
    "22lr_40":   (340,  0.138, ".22 LR 40gr LRN",                       40,  0.223, 0.400, 16.0),
    "300blk_125":(675,  0.345, ".300 Blackout 125gr OTM (supersonic)", 125,  0.308, 1.130,  8.0),
    "300blk_220":(308,  0.608, ".300 Blackout 220gr OTM (subsonic)",   220,  0.308, 1.460,  8.0),
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
    # .380 ACP (9mm Kurz) — distinct from 9mm/.45
    (["380", "acp"],          "380_95"),
    (["380", "auto"],         "380_95"),
    # .300 Blackout — supersonic (light) vs subsonic (heavy) fly very differently
    (["300", "blackout", "125"],        "300blk_125"),
    (["300", "blackout", "220"],        "300blk_220"),
    (["300", "blk", "125"],             "300blk_125"),
    (["300", "blk", "220"],             "300blk_220"),
    (["300", "blackout", "subsonic"],   "300blk_220"),
    (["300", "blk", "subsonic"],        "300blk_220"),
    (["300", "blackout", "supersonic"], "300blk_125"),
    (["300", "blk", "supersonic"],      "300blk_125"),
    # .22 LR — explicit ".22 lr"/"22lr" forms (the ".223" fallback below still
    # wins for .223 queries because ".223" never contains "lr" or "22lr")
    ([".22", "lr"],           "22lr_40"),
    (["22lr"],                "22lr_40"),
    # Fallback by caliber only
    (["5.56"],                "5.56_55"),
    ([".223"],                "5.56_55"),
    ([".308"],                "308_168"),
    (["7.62x51"],             "308_147"),
    (["6.5", "creed"],        "65cm_140"),
    (["338", "lapua"],        "338lm_250"),
    (["9mm"],                 "9mm_115"),
    ([".45"],                 "45acp_230"),
    ([".380"],                "380_95"),
    (["300", "blackout"],     "300blk_125"),
    (["300", "blk"],          "300blk_125"),
    (["300blk"],              "300blk_125"),
    (["7.62x35"],             "300blk_125"),
    # ".22" alone is LAST so ".223"/"5.56"/"7.62x39" etc. resolve first
    # (".223" contains ".22" as a substring — order is what disambiguates).
    ([".22"],                 "22lr_40"),
]

# Keywords that specifically indicate a ballistics trajectory question
_BALLISTIC_SPECIFIC_KEYWORDS = {
    "zeroed", "zero at", "zero'd", "zero distance",
    "bullet drop", "elevation drop", "drop off", "holdover", "come-up",
    "moa", "mrad", "mil", "scope dial",
    "trajectory", "ballistic", "flight path",
    "5.56", ".223", ".308", "7.62", "6.5 creedmoor", ".338", ".50 bmg",
    "9mm", ".45 acp", ".45", "45 acp",
    ".22 lr", "22lr", ".380", "380 acp", "300 blackout", "300 blk", "300blk",
    "grain", "gr bullet", "gr round",
    "fps", "feet per second",
    "spin drift", "gyroscopic drift", "spin", "drift",
    "dope", "dope card", "dope table",
}

# Short, collision-prone tokens that must match as WHOLE WORDS, never as bare
# substrings. These are abbreviations, math-function names, and units of
# measure — tokens whose substring matches land in UNRELATED common words and
# flip a query's routing:
#   mil → miles/military/millionaire   lat → relate/plate/installation
#   lon → "how long"/along/colony      ram → paramedic/program/mainframe
#   tan → distance/mountain            add → address/saddle      grain → migraine
#   gram → program/telegram            force → reinforce/enforce
# Matching these as substrings is what routed a nuclear "30 miles from the
# blast" question to ballistics. Same precedent as the phrase-only "emp" guard.
#
# Deliberately EXCLUDED: topical content words (snake, water, wound, infect,
# signal, power, voltage…) whose "collisions" are on-topic variants we WANT to
# keep matching (rattlesnake, waterproof, wounded, infections). A trailing 's'
# is tolerated so real plurals still match ("hold 2 mils", "30 miles").
# Consulted by _kw_hit(); everything not listed here stays a substring match.
_WORD_BOUNDARY_KEYWORDS = {
    # unit & angular abbreviations
    "mil", "moa", "mrad", "fps", "mph", "kph",
    # math function names & operators
    "sin", "cos", "tan", "log", "sqrt", "add", "sum", "plus", "minus",
    "total", "ratio", "mass", "area", "inch", "time",
    # units of measure
    "mile", "feet", "foot", "yard", "gram", "ounce", "pound", "meter",
    "liter", "gallon",
    # ballistic / quantity short tokens
    "spin", "drift", "drop", "range", "force", "grain", "rifle", "bullet",
    "speed",
    # host-telemetry abbreviations (live-sense gate)
    "ram", "cpu", "gpu", "snr", "rssi", "gps", "lat", "lon", "hop", "hops",
    "temp", "temps", "stat", "stats", "status", "node", "nodes", "link",
    "disk", "map", "model", "relay", "online", "sensor",
    # "radio" must be whole-word so "radioactive"/"radiology"/"radiogram" do
    # not pull in mesh-radio context or the comms topic category.
    "radio",
    # survival/nav acronyms that collide with common words
    "utm", "mgrs", "cbrn", "gmrs", "edc", "cme", "sos",
    # air-quality tokens that live inside unrelated words ("hepatitis", "hazel")
    "aqi", "hepa", "haze",
    # "watt" lives inside "wattle" (bushcraft); word-bound it ("watts" still hits)
    "watt",
    # herbal tokens: "salve" lives inside "salvage", "herb" inside "herbivore"
    "salve", "herb",
}


def _kw_hit(kw, text):
    """True if keyword kw appears in (lowercased) text.

    Most keywords use a plain substring test. Tokens in _WORD_BOUNDARY_KEYWORDS
    instead match on word boundaries (with an optional plural 's') so a 3-char
    unit like 'mil' cannot fire on '30miles'.
    """
    if kw in _WORD_BOUNDARY_KEYWORDS:
        return re.search(r'\b' + re.escape(kw) + r's?\b', text) is not None
    return kw in text

# Explicit arithmetic operators / function names that, on their own, mean the
# user wants a number computed — enough to offer the [CALC:] tag hint. Bare unit
# and quantity nouns ("range", "distance", "depth", "mile") are deliberately NOT
# here: they pepper ordinary survival questions, and _is_math_query gates them
# behind compute intent (via _is_physics_query) instead, so "how deep within 30
# miles of a blast" no longer gets calculator instructions stapled to its prompt.
_MATH_OPERATOR_KEYWORDS = {
    "plus", "minus", "multipl", "divid", "subtract",
    "sum", "average", "percent", "%", "ratio", "proportion",
    "sqrt", "square root", "log", "logarithm",
    "sin", "cos", "tan", "radian",
    "equation", "formula", "arithmetic",
}

# Keywords that trigger the full two-pass calculator agent (extract → compute →
# answer), split by strength.
#
# STRONG keywords name an unambiguous computation on their own — a physics word
# problem or a trajectory query.
#
# WEAK keywords are bare units / quantity nouns ("range", "mile", "depth",
# "distance") that also pepper ordinary survival questions ("within 30 miles of
# a blast", "how deep should the trench be", "how far to the next town"). On
# their own they must NOT fire the calculator agent: with no real arithmetic to
# do, the small extraction model fabricates an expression (it emitted
# mps_to_fps(30e5) for the blast-depth question) which then gets injected as a
# "pre-verified" result. A WEAK keyword only counts when the query ALSO shows
# explicit compute/conversion intent (_COMPUTE_INTENT_KEYWORDS).
_PHYSICS_STRONG_KEYWORDS = {
    "ballistic", "trajectory", "projectile", "bullet drop", "bullet",
    "muzzle velocity", "muzzle energy", "flight time", "time of flight",
    "maximum range", "elevation angle", "angle of elevation",
    "kinetic energy", "momentum", "parabolic", "feet per second",
}
_PHYSICS_WEAK_KEYWORDS = {
    "range", "velocity", "acceleration", "gravity",
    "force", "physics", "fps", "grain", "caliber", "rifle",
    "celsius", "fahrenheit", "kelvin",
    "mph", "kph", "km/h", "m/s",
    "meter", "kilomet", "kilometer", "mile", "feet", "foot", "yard",
    "kilogram", "pound", "ounce", "gram",
    "distance", "speed", "height", "altitude", "depth",
}
# Explicit "compute a number for me" signals. Only these license a WEAK physics
# keyword to fire the calculator agent. Kept deliberately tight: broad question
# stems like "how far"/"how long"/"how fast" are NOT here because they are
# overwhelmingly non-arithmetic in survival questions.
_COMPUTE_INTENT_KEYWORDS = {
    "calculat", "comput", "convert", "conversion",
    "how many", "how much", "equation", "formula", "solve",
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

# Live "senses" (host telemetry + mesh state) are heavy, and when they are
# ALWAYS present the small model tries to weave them into unrelated answers and
# hallucinates around them.  So they are treated as a RETRIEVED source: the
# CPU/GPU/RAM/thermal block and the mesh/alert block are injected only when the
# message actually asks for them.  GPS/location grounding is intentionally NOT
# gated here — it is cheap, factual, and useful for grounding every answer.
_SYSTEM_STATS_KEYWORDS = {
    "cpu", "gpu", "ram", "memory", "disk", "storage", "temperature", "temps",
    "thermal", "overheat", "how hot", "uptime", "watt", "voltage",
    "power draw", "power usage", "power consumption", "system stat",
    "system status", "system load", "resource usage", "utilization",
    "throttl", "fan speed", "how's atlas", "hows atlas", "deck doing",
}
_MESH_CONTEXT_KEYWORDS = {
    "mesh", "node", "nodes", "snr", "rssi", "signal strength", "topology",
    "alert", "alerts", "telemetry", "channel", "channels", "neighbor",
    "hop", "hops", "relay", "router", "online", "offline", "network",
    "radio", "last heard", "link quality", "who's on", "whos on",
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
            "Boiling (most reliable): bring to a ROLLING boil 1 min; 3 min above 6,500 ft (2,000 m). "
            "Kills bacteria, viruses, and protozoa. Let cool; do not boil longer (wastes fuel/water).\n"
            "Bleach (plain, unscented sodium hypochlorite — no splash-less/scented): dose by strength,\n"
            "  6% bleach: 8 drops/gal (2 drops/liter). 8.25% bleach: 6 drops/gal. Cloudy/cold: DOUBLE it.\n"
            "  Stir, let stand 30 min; water should smell faintly of chlorine. If not, repeat once.\n"
            "Iodine tablets: 1 tab/liter. Wait 30 min (60 min if cold or turbid). Not for pregnancy/thyroid.\n"
            "Filtration (Sawyer/LifeStraw): removes bacteria/protozoa — NOT viruses. Combine with chemical treatment.\n"
            "SODIS: clear PET bottle in direct sun 6 hrs (2 days overcast). Kills bacteria/viruses.\n\n"
            "COLLECTION:\n"
            "• Morning dew: wipe vegetation with cloth, wring into container\n"
            "• Rain catchment: tarps, buckets, gutters into barrels\n"
            "• Dry riverbed: dig 1–2 ft from bank to find subsurface water\n"
            "• Running > standing water. Avoid near dead animals.\n\n"
            "Improvised filter (pre-treatment only — always boil/treat after):\n"
            "Layer in container: gravel → sand → crushed wood charcoal.\n\n"
            "NEVER drink seawater (2× dehydration rate) or urine.\n\n"
            "SCOPE: these methods kill GERMS (bacteria/viruses/protozoa) only. They do NOT remove "
            "radioactive fallout, chemicals, heavy metals, or salt — and BOILING actually concentrates "
            "them. For water after a nuclear, chemical, or flood event, see the Contaminated Water doc."
        ),
    },
    {
        "title": "Contaminated Water: Radiological, Chemical & Floodwater — What Treatment Removes",
        "tags": "contaminated water,radioactive water,radiological water,fallout water,chemical "
                "contamination,floodwater,flood water,heavy metals,salt,is the water safe,"
                "purify radioactive water,decontaminate water,distillation,reverse osmosis,activated "
                "carbon,water,nuclear,fallout,cbrn,survival,grid-down",
        "content": (
            "RULE: killing germs is NOT the same as removing contamination. Boiling, bleach, iodine, "
            "SODIS, and squeeze filters (Sawyer/LifeStraw) kill PATHOGENS but do NOT remove dissolved "
            "radioactive material, chemicals, heavy metals, or salt. BOILING makes radioactive/chemical "
            "water WORSE — the water leaves as steam and the contaminant stays behind, concentrated. "
            "Never assume 'boiled = safe' after a nuclear, chemical, or industrial event.\n\n"
            "RADIOACTIVE (FALLOUT) WATER:\n"
            "• Best water is what was COVERED/SEALED before fallout fell — capped bottles, closed tanks, "
            "the water heater, water already inside house pipes, a well with an intact cap.\n"
            "• Fallout is mostly PARTICLES. Let cloudy water SETTLE, pour off the clear top, then run it "
            "through a tight filter or a packed sand-and-clay/charcoal column to catch the particles — "
            "this removes most fallout radioactivity. Dissolved radioiodine/cesium are harder.\n"
            "• Reverse osmosis and DISTILLATION remove most dissolved radionuclides; ion-exchange and "
            "activated carbon help with some. When unsure, drink stored/covered water and ration.\n\n"
            "CHEMICAL / INDUSTRIAL / FLOODWATER:\n"
            "• Assume floodwater carries BOTH sewage and fuel/pesticide/industrial chemicals.\n"
            "• ACTIVATED CARBON removes many organic chemicals (fuel, solvents, pesticides) and odor — "
            "but NOT salt, metals, or most radionuclides.\n"
            "• DISTILLATION (boil, condense the steam on a clean cool surface, collect the drips) removes "
            "salt, heavy metals, and most non-volatile chemicals and radionuclides — but NOT volatile "
            "solvents/fuels that boil and travel WITH the steam. If it smells of fuel/solvent, that "
            "source is unsafe even to distill — find another.\n\n"
            "WHAT REMOVES WHAT (quick reference):\n"
            "• Boil / bleach / iodine / SODIS → germs ONLY.\n"
            "• Settle + decant → particles (helps fallout).\n"
            "• Squeeze/hollow-fiber filter → germs + particles, NOT dissolved chem/salt/radionuclides.\n"
            "• Activated carbon → many organic chemicals + taste, NOT salt/metals/most radionuclides.\n"
            "• Reverse osmosis → nearly everything, including salt, metals, many radionuclides.\n"
            "• Distillation → salt, metals, non-volatile chemicals + radionuclides (NOT volatile fuels).\n\n"
            "ORDER OF OPERATIONS: remove particles first (settle + filter), then distill or RO if you "
            "have it, and disinfect for germs LAST. RELATED: Water Purification in the Field (germs); "
            "Nuclear & Radiological Survival (fallout/dose); Radiological Protection (CBRN/decon); "
            "Severe Weather & Natural Disasters (floodwater); Emergency Water Procurement."
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
            "Green vegetation = white smoke (day). Rubber/plastic = black smoke.\n\n"
            "SAFETY: never burn a fire, stove, or heater inside a tent, snow shelter, vehicle, or "
            "sealed room — combustion makes carbon monoxide, which kills. See the Carbon Monoxide doc."
        ),
    },
    {
        "title": "Carbon Monoxide: The Silent Killer in Shelters, Tents, Vehicles & After Storms",
        "tags": "carbon monoxide,co,co poisoning,silent killer,generator,generator indoors,stove,"
                "heater,propane heater,kerosene,charcoal,grill,exhaust,fumes,ventilation,vent,"
                "snow cave,quinzhee,tent,enclosed space,fire indoors,garage,headache,survival,grid-down",
        "content": (
            "RULE: anything that burns fuel — flame OR engine — makes CARBON MONOXIDE (CO), an "
            "invisible, ODORLESS gas that kills with no warning. After winter storms and disasters, CO "
            "kills more people than the cold or the event itself. Never run an engine or burn fuel in an "
            "enclosed space without strong through-ventilation.\n\n"
            "SOURCES THAT KILL INDOORS / IN SHELTERS:\n"
            "Gasoline/propane GENERATORS, camp STOVES and backpacking burners, CHARCOAL and grills, "
            "propane/kerosene HEATERS, vehicle engines, and open FIRES — used inside tents, snow caves/"
            "quinzhees, sealed rooms, basements, garages, or vehicles. CHARCOAL indoors is especially "
            "lethal. A running car in a closed (or snow-blocked) garage is fatal within minutes.\n\n"
            "WHY YOUR OWN SHELTER IS THE TRAP: a good weather shelter is sealed to hold heat — which is "
            "exactly what traps CO. This is the real reason a quinzhee/snow cave MUST keep a vent hole, "
            "and why even a fallout shelter must ventilate. Heat safely with body heat, warm water "
            "bottles, and insulation — NOT with combustion inside the space.\n\n"
            "SAFE PRACTICE:\n"
            "• Generators: run OUTSIDE only, ≥20 ft from any door/window/vent, exhaust pointed away.\n"
            "• Cooking/heating with a flame: outdoors, or only with a wide cross-draft (two openings).\n"
            "• Snow shelter: keep a vent open to the outside and clear it often so it can't ice over.\n"
            "• Never sleep with a fuel-burning stove or heater running in a tent or vehicle.\n"
            "• Put a battery CO ALARM in any shelter, RV, cabin, vehicle, or home — cheap, lifesaving.\n\n"
            "SYMPTOMS (mistaken for flu, altitude, or fatigue — but there is NO fever): headache, "
            "dizziness, nausea, weakness, confusion, blurred vision; then drowsiness, collapse, death. "
            "RED FLAG: several people or pets in the same space getting sick at once. Judgment fails "
            "early, so victims often don't realize what's happening.\n\n"
            "RESPONSE: get the person into FRESH AIR immediately and turn off the source; open "
            "everything. If unresponsive or not breathing, start rescue breathing/CPR and evacuate to "
            "medical care — CO binds blood ~200x tighter than oxygen and takes hours to clear "
            "(high-flow oxygen speeds recovery). RELATED: Emergency Shelter Construction (vent it), "
            "Expedient Fallout Shelter (ventilate, don't seal), Fire Starting Techniques, Off-Grid "
            "Power and Battery Management (generators), Cold & Heat Injuries (safe rewarming)."
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
            "PRIORITY ORDER: 1. Ground insulation  2. Windbreak  3. Rain protection  4. Heat retention\n\n"
            "SCOPE — WEATHER/EXPOSURE ONLY: these shelters (debris hut, lean-to, quinzhee, tarp) "
            "protect from cold, wind, and rain. They provide ZERO radiation shielding — there is no "
            "mass to stop gamma. For nuclear fallout you need an earth/mass shelter: see the "
            "Expedient Fallout Shelter and Nuclear & Radiological Survival docs. For tornado/hurricane "
            "see Severe Weather; for chemical/biological threats see the Radiological Protection (CBRN) "
            "doc. To pick the right shelter for a given threat, see Choosing the Right Shelter for the Threat."
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
        "title": "Ballistics: DOPE Cards — Multiple Calibers",
        "tags": "ballistics,bullet drop,TOF,time of flight,DOPE card,trajectory,308 Win,223 Rem,5.56,9mm,6.5 creedmoor,300 win mag,338 lapua,gravity,zero,spin drift",
        "content": (
            "All tables: G1 drag model, sea level, std atmosphere, 100 m zero. "
            "Altitude/temperature/barrel length will shift values. "
            "Spin drift column = gyroscopic drift RIGHT (RH barrel). "
            "Columns: Range | Drop cm | MOA↑ | mrad | TOF(s) | Drift cm\n\n"

            "5.56mm 55gr FMJ M193 — 975 m/s, BC=0.269, 1:7\" twist:\n"
            " 100m |    0.0 |  0.0 | 0.00 | 0.108 |  0.1R\n"
            " 200m |  -12.5 |  2.1 | 0.62 | 0.225 |  0.5R\n"
            " 300m |  -40.0 |  4.6 | 1.33 | 0.355 |  1.1R\n"
            " 400m |  -85.7 |  7.4 | 2.14 | 0.498 |  2.0R\n"
            " 500m | -153.6 | 10.6 | 3.07 | 0.656 |  3.2R\n"
            " 600m | -248.9 | 14.3 | 4.15 | 0.832 |  5.0R\n"
            " 700m | -378.2 | 18.6 | 5.40 | 1.028 |  7.4R\n"
            " 800m | -550.4 | 23.7 | 6.88 | 1.250 | 10.6R\n"
            " 900m | -778.3 | 29.7 | 8.65 | 1.505 | 14.8R\n"
            "1000m |-1079.9 | 37.1 |10.80 | 1.797 | 20.5R\n\n"

            "5.56mm 62gr FMJ M855 — 930 m/s, BC=0.307, 1:7\" twist:\n"
            " 100m |    0.0 |  0.0 | 0.00 | 0.112 |  0.1R\n"
            " 200m |  -13.4 |  2.3 | 0.67 | 0.234 |  0.4R\n"
            " 300m |  -42.6 |  4.9 | 1.42 | 0.366 |  1.0R\n"
            " 400m |  -90.6 |  7.8 | 2.26 | 0.510 |  1.8R\n"
            " 500m | -160.9 | 11.1 | 3.22 | 0.668 |  2.9R\n"
            " 600m | -258.0 | 14.8 | 4.30 | 0.840 |  4.4R\n"
            " 700m | -387.4 | 19.0 | 5.53 | 1.031 |  6.5R\n"
            " 800m | -556.2 | 23.9 | 6.95 | 1.242 |  9.1R\n"
            " 900m | -774.1 | 29.6 | 8.60 | 1.478 | 12.5R\n"
            "1000m |-1054.1 | 36.2 |10.54 | 1.745 | 16.9R\n\n"

            "5.56mm 77gr OTM Mk262 — 884 m/s, BC=0.372, 1:8\" twist:\n"
            " 100m |    0.0 |  0.0 | 0.00 | 0.117 |  0.1R\n"
            " 200m |  -14.4 |  2.5 | 0.72 | 0.243 |  0.4R\n"
            " 300m |  -45.4 |  5.2 | 1.51 | 0.377 |  0.8R\n"
            " 400m |  -95.5 |  8.2 | 2.39 | 0.521 |  1.4R\n"
            " 500m | -167.6 | 11.5 | 3.35 | 0.677 |  2.3R\n"
            " 600m | -265.3 | 15.2 | 4.42 | 0.844 |  3.4R\n"
            " 700m | -392.7 | 19.3 | 5.61 | 1.025 |  4.9R\n"
            " 800m | -555.0 | 23.8 | 6.94 | 1.221 |  6.7R\n"
            " 900m | -758.8 | 29.0 | 8.43 | 1.436 |  9.1R\n"
            "1000m |-1012.0 | 34.8 |10.12 | 1.671 | 12.0R\n\n"

            ".308 Win 168gr BTHP M118LR — 820 m/s, BC=0.447, 1:10\" twist:\n"
            " 100m |    0.0 |  0.0 | 0.00 | 0.126 |  0.2R\n"
            " 200m |  -16.4 |  2.8 | 0.82 | 0.259 |  0.6R\n"
            " 300m |  -51.3 |  5.9 | 1.71 | 0.400 |  1.4R\n"
            " 400m | -107.0 |  9.2 | 2.67 | 0.550 |  2.6R\n"
            " 500m | -186.2 | 12.8 | 3.72 | 0.709 |  4.1R\n"
            " 600m | -291.9 | 16.7 | 4.86 | 0.879 |  6.0R\n"
            " 700m | -428.0 | 21.0 | 6.11 | 1.060 |  8.5R\n"
            " 800m | -598.5 | 25.7 | 7.48 | 1.254 | 11.6R\n"
            " 900m | -808.8 | 30.9 | 8.99 | 1.463 | 15.3R\n"
            "1000m |-1065.0 | 36.6 |10.65 | 1.688 | 19.9R\n\n"

            "6.5 Creedmoor 140gr BTHP — 869 m/s, BC=0.626, 1:8\" twist:\n"
            " 100m |    0.0 |  0.0 | 0.00 | 0.117 |  0.2R\n"
            " 200m |  -14.1 |  2.4 | 0.70 | 0.240 |  0.6R\n"
            " 300m |  -43.5 |  5.0 | 1.45 | 0.367 |  1.3R\n"
            " 400m |  -89.5 |  7.7 | 2.24 | 0.500 |  2.3R\n"
            " 500m | -153.6 | 10.6 | 3.07 | 0.638 |  3.6R\n"
            " 600m | -237.3 | 13.6 | 3.96 | 0.783 |  5.2R\n"
            " 700m | -342.3 | 16.8 | 4.89 | 0.934 |  7.2R\n"
            " 800m | -470.7 | 20.2 | 5.88 | 1.091 |  9.5R\n"
            " 900m | -624.6 | 23.9 | 6.94 | 1.256 | 12.4R\n"
            "1000m | -806.4 | 27.7 | 8.06 | 1.428 | 15.6R\n\n"

            ".300 Win Mag 190gr BTHP — 932 m/s, BC=0.560, 1:10\" twist:\n"
            " 100m |    0.0 |  0.0 | 0.00 | 0.110 |  0.1R\n"
            " 200m |  -12.4 |  2.1 | 0.62 | 0.224 |  0.4R\n"
            " 300m |  -38.2 |  4.4 | 1.27 | 0.344 |  1.0R\n"
            " 400m |  -78.9 |  6.8 | 1.97 | 0.470 |  1.7R\n"
            " 500m | -135.7 |  9.3 | 2.71 | 0.601 |  2.7R\n"
            " 600m | -210.3 | 12.0 | 3.51 | 0.739 |  3.9R\n"
            " 700m | -304.4 | 14.9 | 4.35 | 0.883 |  5.4R\n"
            " 800m | -420.0 | 18.0 | 5.25 | 1.035 |  7.3R\n"
            " 900m | -559.2 | 21.4 | 6.21 | 1.194 |  9.4R\n"
            "1000m | -724.6 | 24.9 | 7.25 | 1.361 | 12.0R\n\n"

            ".338 Lapua Magnum 250gr BTHP — 905 m/s, BC=0.587, 1:10\" twist:\n"
            " 100m |    0.0 |  0.0 | 0.00 | 0.113 |  0.1R\n"
            " 200m |  -13.1 |  2.3 | 0.66 | 0.231 |  0.5R\n"
            " 300m |  -40.4 |  4.6 | 1.35 | 0.354 |  1.0R\n"
            " 400m |  -83.2 |  7.2 | 2.08 | 0.482 |  1.7R\n"
            " 500m | -142.9 |  9.8 | 2.86 | 0.617 |  2.7R\n"
            " 600m | -221.2 | 12.7 | 3.69 | 0.757 |  4.0R\n"
            " 700m | -319.7 | 15.7 | 4.57 | 0.904 |  5.5R\n"
            " 800m | -440.4 | 18.9 | 5.50 | 1.058 |  7.3R\n"
            " 900m | -585.5 | 22.4 | 6.51 | 1.219 |  9.5R\n"
            "1000m | -757.5 | 26.0 | 7.58 | 1.389 | 12.0R\n\n"

            "PHYSICS NOTE: drop is relative to line of sight at 100 m zero. "
            "Bullet is above LoS between ~25 m and 100 m (rising through bore-line offset). "
            "For on-demand DOPE card: ask 'give me a dope card for [caliber]'."
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
            "TIMELINE: Days 1–7: cash. Weeks 2–4: barter starts. Month 1–3: commodity money dominates.\n\n"
            "RELATED: Community Defense and Mutual Aid Group; Survival Priorities; "
            "Bug-Out Bag; Field Antibiotics and Common Medications."
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
            "Marigolds: companion plant that repels aphids and nematodes.\n\n"
            "RELATED: Survival Gardening; Food Preservation Without Refrigeration; "
            "Caloric Needs and Food Rationing; Trapping, Snaring & Fishing."
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
            "remove excess weight (100 lb ≈ 1% worse mpg); minimize idling.\n\n"
            "RELATED: Vehicle Maintenance and Off-Road Recovery; "
            "Off-Grid Power and Battery Management; Barter Economics."
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
            "Coolant flush: 2 years. Belts/hoses: inspect annually. Battery: test annually, replace at 5+ yrs.\n\n"
            "RELATED: Fuel Storage and Vehicle Fuel Math; Off-Grid Power and Battery Management."
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

    # ── NUCLEAR / RADIOLOGICAL (CBRN) ─────────────────────────────────────────
    {
        "title": "Nuclear & Radiological Survival: Fallout, Dose, and Sheltering",
        "tags": "nuclear,radiation,fallout,rad,rem,roentgen,sievert,gray,dose,dosimeter,"
                "geiger,7-10 rule,7 10 rule,blast,cbrn,nuke,dirty bomb,radioactive iodine,"
                "acute radiation syndrome,shelter in place,survival,grid-down",
        "content": (
            "THREE THREATS FROM A NUCLEAR DETONATION:\n"
            "1. BLAST/PRESSURE — overpressure, flying debris, collapse. Drop flat, feet toward blast, "
            "behind hard cover; mouth open to protect eardrums.\n"
            "2. THERMAL FLASH — intense light/heat causes flash burns and starts fires. Do NOT look at "
            "the fireball. Cover exposed skin; get behind anything opaque.\n"
            "3. RADIATION — initial (prompt) radiation in the first minute, then FALLOUT: dirt and debris "
            "made radioactive, lofted by the cloud, falling back to earth. Heaviest, most dangerous "
            "particles land within the first hour; the visible fallout looks like grit, sand, or ash.\n\n"
            "THE 7-10 RULE (dose-rate decay — memorize this):\n"
            "For every 7-fold increase in time after the blast, the radiation dose RATE drops by a factor "
            "of 10. So if it is 1000 R/hr at H+1 hour, it is ~100 R/hr at H+7 hours, ~10 R/hr at H+49 hours "
            "(~2 days), and ~1 R/hr at about 2 weeks. This is why the FIRST 48 HOURS of sheltering save the "
            "most lives — radiation falls off fastest early.\n\n"
            "THREE WAYS TO CUT YOUR DOSE — TIME, DISTANCE, SHIELDING:\n"
            "• TIME: minimize hours in the radiation field. Shelter hardest in the first 1-2 days.\n"
            "• DISTANCE: fallout on the roof, ground, and outer walls is the source — get to the center, "
            "lowest level (basement core) so mass sits between you and those surfaces.\n"
            "• SHIELDING (HALVING THICKNESS — each layer cuts gamma dose in half):\n"
            "  Lead ~0.4 in · Steel ~0.7 in · Concrete ~2.4 in · Packed earth ~3.3 in · "
            "Water ~4.8 in · Wood/snow ~11 in. Stacking ~10 halving-thicknesses cuts dose ~1000x. "
            "Sandbags, books, full water containers, and dirt-filled drawers all work.\n\n"
            "PROTECTION FACTOR (PF) of common refuges:\n"
            "Open frame house ~2-3 · House basement, center ~10-20 · Multi-story building core ~20-100 · "
            "Dedicated, earth-covered fallout shelter 40-1000+. Higher PF = lower dose. Aim for PF 40+.\n\n"
            "RADIATION DOSE — WHAT THE NUMBERS MEAN (gamma: 1 rad ~ 1 rem; 1 Gy = 100 rad; 1 Sv = 100 rem):\n"
            "• Under 50 rem — no immediate symptoms.\n"
            "• 50-200 rem — nausea/vomiting hours later, fatigue; recovery expected with rest.\n"
            "• 200-450 rem — Acute Radiation Syndrome; hair loss, immune drop; LD50/60 ~450 rem "
            "(about half die within 60 days WITHOUT medical care).\n"
            "• 450-800 rem — severe ARS, survival unlikely without hospital care.\n"
            "• Over 800 rem — usually fatal.\n"
            "Dose is cumulative: 5 R/hr for 10 hours = 50 R. A simple dosimeter (e.g. RADIACMETER / "
            "CDV-742 pen, or a digital survey meter) tells you when it is safe to move.\n\n"
            "SHELTER PLAN:\n"
            "1. Get inside, get to the center/lowest level, stay in. Brush/wipe fallout off before "
            "entering; leave outer clothes at the door.\n"
            "2. Seal the room loosely against dust (tape plastic over vents/windows) but DO NOT fully "
            "airtight — you still need to breathe; fallout is a dust hazard, not a gas.\n"
            "3. Plan to stay sheltered at least 24-48 hours; ideally up to 2 weeks, leaving only briefly "
            "for urgent needs once a meter (or the 7-10 rule estimate) shows the rate has fallen.\n"
            "4. Food in cans/sealed packaging is safe — fallout is external dust; wipe the container, "
            "wash hands. Covered/stored water is safe; running tap water is generally usable.\n"
            "RELATED: to BUILD radiation protection from scratch when no basement or hardened "
            "building is available, see the Expedient Fallout Shelter doc. See the Radiological "
            "Protection (KI/decontamination/CBRN) doc for potassium iodide, decontamination, and "
            "protective gear; and the EMP & Solar Storm doc for electronics survival after a nuclear "
            "or solar event."
        ),
    },
    {
        "title": "Expedient Fallout Shelter: Building Radiation Protection from Earth & Mass",
        "tags": "fallout shelter,radiation shelter,nuclear shelter,blast shelter,expedient shelter,"
                "earth shelter,trench shelter,build a fallout shelter,build a radiation shelter,"
                "how to build a shelter from radiation,shielding,halving thickness,protection factor,"
                "sandbags,packed earth,below grade,nuclear,fallout,radiation,cbrn,survival,grid-down",
        "content": (
            "RULE: radiation shielding is MASS and DISTANCE — nothing else (here DISTANCE means the gap "
            "between you and the fallout DUST, NOT how many miles away the blast was). Gamma rays are stopped "
            "ONLY by putting dense material between you and the fallout. A lean-to, tent, tarp, "
            "plastic sheeting, branches, or duct tape stop fallout DUST but do NOT reduce gamma dose "
            "at all. Do not confuse a weather shelter with a fallout shelter — they are built on "
            "opposite principles (insulation vs. mass). The goal here is a high PROTECTION FACTOR "
            "(PF 40+): see the Nuclear & Radiological Survival doc for dose, the 7-10 rule, and PF.\n\n"
            "HOW DEEP / HOW MUCH EARTH — AND DOES YOUR DISTANCE FROM THE BLAST CHANGE IT? NO. "
            "How far you are from the detonation does NOT set how deep to dig or how much earth you need, "
            "and you should NOT convert the mileage or plug it into any formula — distance from the blast "
            "does not enter the shelter calculation. Past roughly 3-5 miles you are already outside the "
            "prompt blast, heat, and initial radiation; from there out to tens of miles the hazard is "
            "FALLOUT carried on the wind, and the shelter spec is the SAME whether the blast was 10, 30, "
            "or 100 miles away: enough MASS to reach your protection-factor target. Target PF 40+ → about "
            "24-36 in (2-3 ft) of packed earth (or equivalent) on every side AND overhead, OR a "
            "door-covered trench dug chest-to-head deep (3.5-4+ ft) with 18-30 in of packed earth piled "
            "on the roof. Being closer to the blast means you want MORE mass and to get below ground "
            "FASTER (heavier, earlier fallout) — never a deeper hole just because of the miles.\n\n"
            "HOW MUCH MASS — HALVING THICKNESS (each layer halves the gamma dose):\n"
            "Packed earth ~3.3 in · Concrete ~2.4 in · Steel ~0.7 in · Water ~4.8 in · Wood ~11 in. "
            "Stacking ~10 halvings cuts dose ~1000x. Practical target: about 24-36 in (2-3 ft) of "
            "packed earth — or its equivalent — on EVERY side AND overhead.\n\n"
            "BEST OPTION — GO BELOW GRADE (earth is free mass):\n"
            "DOOR-COVERED TRENCH (Kearny expedient, highest PF you can dig by hand):\n"
            "1. Dig a trench as deep as you can — at least chest-to-head deep (3.5-4+ ft), just wide "
            "enough to sit/lie in. The earth walls already shield you from ground-level fallout.\n"
            "2. Roof it with interior doors, poles, or planks laid across the trench.\n"
            "3. Pile 18-30 in of packed earth on top of the roof. Mound earth around the edges too.\n"
            "4. Leave a small crawl entrance — see ENTRANCE below.\n\n"
            "IF YOU CANNOT DIG (rock, frozen ground, high water table) — BUILD UP WITH FILLED MASS:\n"
            "Make walls and a roof of anything you can fill with earth, sand, or water: sandbags, "
            "earth-filled boxes/totes/drawers/trash cans, stacked full water containers, dense "
            "firewood, packed books. Build a SMALL box around yourself — ~2 ft of packed earth "
            "equivalent on all four sides and on the roof. Small footprint = less mass needed.\n\n"
            "INSIDE AN EXISTING HOUSE (fastest, often best):\n"
            "Pick the basement corner with the most earth around it. Pile mass OVERHEAD on the floor "
            "above (sandbags, full water containers, earth-filled furniture) and against the walls; "
            "block any basement windows with sandbags or earth from outside. A basement core can reach "
            "PF 20-100 with added mass.\n\n"
            "GEOMETRY THAT MULTIPLIES YOUR MASS:\n"
            "• Keep the space SMALL and the ceiling LOW — less surface to shield, less mass to move.\n"
            "• ENTRANCE: gamma travels in straight lines and also scatters ('skyshine'). Use an "
            "L-shaped / right-angle crawl entrance or a baffle so no fallout-covered sky or ground "
            "has a straight line into where you sit. A 90° turn blocks most scattered radiation.\n\n"
            "VENTILATION — DO NOT SEAL AIRTIGHT:\n"
            "Fallout is DUST, not gas, so you need airflow to breathe. Leave a vent and an air "
            "intake; pump air with a hinged flap (Kearny Air Pump / KAP) if it is stuffy. Cover the "
            "intake with cloth to filter grit. (This is the OPPOSITE of chemical/biological sheltering, "
            "where you DO seal airtight and filter the air — see the CBRN doc.)\n\n"
            "WHAT DOES NOT WORK (do not rely on these for radiation): open lean-to, tent, tarp, "
            "single layer of plywood or branches, plastic sheeting, duct-taped gaps. They keep dust "
            "off but provide essentially ZERO gamma shielding. Mass is the only thing that counts.\n"
            "OCCUPY: hardest for the first 48 hours; plan for up to ~2 weeks. RELATED: Nuclear & "
            "Radiological Survival (dose, 7-10 rule, PF); Choosing the Right Shelter for the Threat; "
            "Radiological Protection (KI/decon/CBRN); Emergency Shelter Construction (weather shelters "
            "only — NOT radiation)."
        ),
    },
    {
        "title": "Choosing the Right Shelter for the Threat: Weather vs. Fallout vs. Storm vs. CBRN",
        "tags": "shelter selection,which shelter,what kind of shelter,shelter for radiation,"
                "shelter for tornado,shelter for fallout,shelter decision,threat shelter,"
                "weather shelter vs fallout shelter,disambiguation,shelter type,survival,grid-down",
        "content": (
            "RULE: the kind of shelter you build depends entirely on the THREAT. The principles are "
            "different — sometimes OPPOSITE — so do not blend them. Pick the threat, then build the "
            "matching shelter. This is the map that connects the shelter, nuclear, weather, and CBRN "
            "docs.\n\n"
            "COLD / WIND / RAIN / EXPOSURE  → WEATHER SHELTER (insulation):\n"
            "Debris hut, lean-to, or quinzhee. The job is to trap body heat and block wind/rain. "
            "Mass does not matter; INSULATION and ground insulation do. → Emergency Shelter "
            "Construction doc. NOTE: provides ZERO radiation protection.\n\n"
            "NUCLEAR FALLOUT / RADIATION  → FALLOUT SHELTER (mass + distance):\n"
            "Get below grade or behind 2-3 ft of packed earth (or equivalent) on all sides and "
            "overhead; aim for PF 40+; ventilate (dust, not gas). A lean-to or tarp does NOTHING "
            "here. → Expedient Fallout Shelter + Nuclear & Radiological Survival docs.\n\n"
            "TORNADO / EXTREME WIND  → INTERIOR REFUGE (structure + distance from windows):\n"
            "Smallest interior room, lowest floor, center of the building, away from glass; basement "
            "or storm cellar is best; cover with mattresses against debris. → Severe Weather doc.\n\n"
            "HURRICANE / FLOOD  → opposite of tornado for water: evacuate the flood zone or go to a "
            "HIGHER floor of a sound structure; never the basement in a flood. → Severe Weather doc.\n\n"
            "CHEMICAL / BIOLOGICAL (CBRN gas/aerosol)  → SEALED ROOM (airtight + filtered air):\n"
            "Here the hazard IS a gas/aerosol, so you DO seal airtight, tape vents, and use a "
            "respirator/filtered air — the OPPOSITE of fallout (where sealing airtight would suffocate "
            "you). → Radiological Protection (CBRN) doc.\n\n"
            "WILDFIRE  → do NOT shelter in a flimsy structure; evacuate early. Last resort: a wide "
            "cleared area, a vehicle with vents closed, or a body of water. → Severe Weather doc.\n\n"
            "KEY RELATIONSHIPS (so the answers never get mixed up):\n"
            "• Fallout shelter ≠ weather shelter: fallout needs MASS; cold needs INSULATION.\n"
            "• Fallout shelter ≠ chemical shelter: fallout is loose DUST → VENTILATE; chemical/bio is "
            "a GAS → SEAL AIRTIGHT + filter.\n"
            "• Tornado ≠ flood: tornado go LOW and central; flood go HIGH.\n"
            "RELATED: Emergency Shelter Construction; Expedient Fallout Shelter; Nuclear & "
            "Radiological Survival; Radiological Protection (CBRN); Severe Weather & Natural Disasters."
        ),
    },
    {
        "title": "Radiological Protection: Potassium Iodide (KI), Decontamination & CBRN PPE",
        "tags": "potassium iodide,KI,thyroid,iodine-131,decontamination,decon,gas mask,respirator,"
                "n95,cbrn,chemical agent,nerve agent,biological,radiation protection,ppe,"
                "contamination,fallout,mustard,chlorine,atropine,survival",
        "content": (
            "POTASSIUM IODIDE (KI) — WHAT IT DOES AND DOES NOT DO:\n"
            "KI floods the thyroid with stable iodine so it cannot absorb RADIOACTIVE iodine-131 from "
            "fallout. It protects ONLY the thyroid, ONLY against radioiodine. It is NOT an anti-radiation "
            "pill, does not shield the rest of the body, and does nothing against gamma, cesium, or other "
            "isotopes. Sheltering and shielding remain the primary defense.\n"
            "DOSING (take only when authorities advise, or when fallout from a fission event is imminent — "
            "ideally just before or within a few hours of exposure; one dose lasts ~24 hours):\n"
            "• Adults & children over 12 (or over ~150 lb): 130 mg/day\n"
            "• Children 3-12 yr: 65 mg/day\n"
            "• Infants 1 mo-3 yr: 32 mg/day · under 1 mo: 16 mg/day\n"
            "Repeat daily only while exposure continues. AVOID if iodine-allergic or with certain thyroid "
            "conditions. Greatest benefit is for children and pregnant/nursing women; least for over-40 adults.\n\n"
            "DECONTAMINATION (removing fallout/agent from skin and gear):\n"
            "1. Removing OUTER clothing eliminates up to ~90% of contamination — peel it off (don't pull "
            "over your head; cut it off), bag it, set it far outside the living space.\n"
            "2. Wash with lukewarm water and soap, gently — do NOT scrub hard or use abrasives; broken "
            "skin lets contamination in. Blot, don't rub.\n"
            "3. Shampoo hair but DO NOT use conditioner — conditioner binds radioactive particles to hair.\n"
            "4. Gently blow nose, wipe eyelids and ears/lashes with a clean damp cloth.\n"
            "5. Re-dress in clean, stored clothing.\n\n"
            "RESPIRATORY & BODY PROTECTION (PPE):\n"
            "• Fallout DUST: an N95/P100 respirator or even a tight cloth/HEPA mask greatly cuts inhalation "
            "of particles — the main internal-dose risk from fallout. Goggles + hat + covered skin help.\n"
            "• CHEMICAL/BIOLOGICAL agents (gases/aerosols): a particulate mask is NOT enough. You need a "
            "full-face respirator with a CBRN/NBC combination canister (e.g. 40mm NATO). A tight face seal "
            "requires being clean-shaven. Pair with a coverall (Tyvek), gloves, and boots, taped at the gaps.\n\n"
            "CHEMICAL AGENT CLASSES (recognize to respond):\n"
            "• NERVE (sarin, VX — organophosphates): pinpoint pupils, drooling, convulsions (SLUDGE: "
            "Salivation, Lacrimation, Urination, Defecation, GI distress, Emesis). Antidote: atropine + "
            "pralidoxime (2-PAM) auto-injector; get upwind/uphill immediately.\n"
            "• BLISTER (mustard, lewisite): delayed skin blistering, eye/lung burns. Decon fast.\n"
            "• BLOOD (hydrogen cyanide): rapid collapse, gasping. \n"
            "• CHOKING (chlorine, phosgene): coughing, chest tightness; move to fresh air, rest, sit upright.\n"
            "BIOLOGICAL THREATS: isolate the sick, strict hand hygiene, PPE, boil/treat water — see "
            "the Sanitation & Disease Prevention doc. RELATED: see the Nuclear & Radiological Survival doc."
        ),
    },
    {
        "title": "EMP & Solar Storm (CME): Protecting Electronics & Faraday Cages",
        "tags": "emp,electromagnetic pulse,e1,e3,cme,solar storm,solar flare,coronal mass ejection,"
                "carrington,geomagnetic,faraday cage,grid down,electronics protection,radio,"
                "surge,transformer,nuclear,survival",
        "content": (
            "TWO SOURCES OF A LARGE ELECTROMAGNETIC EVENT:\n"
            "• NUCLEAR EMP (HEMP) — a high-altitude nuclear burst. Produces three pulses:\n"
            "  E1: a near-instant, very fast spike that couples into circuit traces and destroys "
            "unshielded semiconductors/microelectronics (radios, vehicle ECUs, computers, solar charge "
            "controllers) over a continental footprint.\n"
            "  E2: lightning-like; ordinary surge protection handles it, but it can arrive while E1 has "
            "already defeated that protection.\n"
            "  E3: a slow, seconds-to-minutes geomagnetic-style surge that induces large currents in long "
            "conductors (power lines, pipelines) and burns out grid transformers.\n"
            "• SOLAR / CME (Coronal Mass Ejection) — a geomagnetic storm (Carrington 1859; Quebec blackout "
            "1989). Behaves like E3: little direct threat to small handheld electronics, but it can collapse "
            "the power grid for weeks-to-months by destroying hard-to-replace high-voltage transformers. "
            "Space-weather warning (aurora far south, NOAA G5) may give hours of notice.\n\n"
            "WHAT ACTUALLY FAILS:\n"
            "Grid power, the internet/cell networks, anything connected to long wiring, and unshielded "
            "solid-state electronics. Simple/old devices (manual tools, non-electronic engines) survive. "
            "The grid, not your gadgets, is the long-term problem in a CME.\n\n"
            "FARADAY CAGE — how to actually build one:\n"
            "A Faraday cage is a CONTINUOUS conductive enclosure that shunts the pulse around its contents. "
            "It does NOT need to be grounded to protect what is inside.\n"
            "1. Use a metal box with conductive, overlapping seams: a steel ammo can, a galvanized trash "
            "can with a tight metal lid, or a metal toolbox.\n"
            "2. INSULATE the contents from the metal — the device must not touch the conductive shell. Wrap "
            "each item in cardboard, bubble wrap, or cloth, then nest inside.\n"
            "3. Ensure the lid makes metal-to-metal contact all the way around; tighten gaps with conductive "
            "(aluminum) tape if needed.\n"
            "4. Nesting two layers (wrapped device → small metal box → larger metal can) adds margin.\n"
            "NOTE: a microwave oven is NOT a reliable Faraday cage. Test a cage by sealing a powered-on "
            "phone or FM radio inside — if it loses all signal, the shielding is good.\n\n"
            "WHAT TO STORE IN A CAGE (spares, kept disconnected):\n"
            "A backup handheld/ham radio, a spare solar charge controller and small inverter, LED "
            "flashlights, a multimeter, spare vehicle ECU/ignition module, USB drives with offline maps and "
            "references, and a small power bank. Keep critical Atlas/comms spares shielded.\n"
            "DEFENSE FOR IN-USE GEAR: whole-home and inline surge isolation, and physically disconnecting "
            "antennas/chargers from the grid when a space-weather or attack warning is issued. "
            "RELATED: see the Nuclear & Radiological Survival, off-grid power, and ham-radio comms docs."
        ),
    },

    # ── WEATHER & NATURAL DISASTERS ──────────────────────────────────────────
    {
        "title": "Severe Weather & Natural Disasters: Tornado, Hurricane, Flood, Earthquake, Wildfire",
        "tags": "weather,tornado,hurricane,flood,flash flood,earthquake,wildfire,lightning,blizzard,"
                "disaster,evacuation,storm surge,shelter,preparedness,survival",
        "content": (
            "GENERAL: a WATCH means conditions are possible — prepare. A WARNING means it is happening or "
            "imminent — act now. Know your safe room and two evacuation routes in advance.\n\n"
            "TORNADO:\n"
            "Signs: dark greenish sky, large hail, a low rotating wall cloud, a loud freight-train roar. "
            "Go to the lowest floor, an interior room with no windows (closet, bathroom), put as many walls "
            "between you and outside as possible. Cover with a mattress/blankets; protect head and neck. "
            "In a vehicle or mobile home, GET OUT — flee to a sturdy building or, as a last resort, lie flat "
            "in a low ditch away from the vehicle, watching for flooding. Avoid highway overpasses.\n\n"
            "HURRICANE / TROPICAL STORM:\n"
            "Days of warning — evacuate if told to, especially from storm-surge zones (surge, not wind, is "
            "the top killer). If sheltering: board windows, fill bathtubs/containers with water, charge and "
            "stage gear, move to an interior room away from windows. Beware the calm EYE — the back wall "
            "brings winds from the opposite direction. Never drive through flooded roads.\n\n"
            "FLOOD / FLASH FLOOD:\n"
            "TURN AROUND, DON'T DROWN. 6 inches of moving water can knock you down; 12 inches can float and "
            "sweep most cars; 2 feet sweeps trucks/SUVs. Move to high ground immediately; flash floods rise "
            "in minutes, often miles from the rain. Never camp in dry washes/canyons. If trapped, get to the "
            "highest point and signal. After: assume floodwater is contaminated (sewage, chemicals).\n\n"
            "EARTHQUAKE:\n"
            "DROP, COVER, HOLD ON — get under a sturdy table, protect head/neck, stay away from windows and "
            "anything that can fall. Indoors stay in; do not run outside during shaking (falling facade/glass). "
            "Outdoors move to open ground away from buildings, trees, power lines. After: expect aftershocks, "
            "check for gas leaks (smell/hiss — shut off main, no flames/switches), structural damage, and "
            "move to open space. On the coast, strong/long shaking is a tsunami warning — get to high ground.\n\n"
            "WILDFIRE:\n"
            "Embers travel a mile ahead of a fire. Create defensible space (clear brush 30+ ft). Evacuate "
            "early — don't wait. If trapped: a building is usually safer than open ground or a car; if "
            "outdoors, find a low bare area, pond, or wide road; lie face-down in a depression under a "
            "wool/cotton blanket (not synthetic), cover nose/mouth, let the front pass. Smoke kills more "
            "than flame — stay low.\n\n"
            "LIGHTNING:\n"
            "When thunder roars, go indoors; if you can hear thunder you are in range. 30-30 rule: take "
            "cover if flash-to-bang is under 30 s; wait 30 min after the last thunder. No safe place outside "
            "— avoid high ground, lone trees, water, metal. Last resort: crouch low on the balls of your "
            "feet, minimal ground contact, NOT lying flat. RELATED: see the Weather Prediction, "
            "Cold & Heat Injuries, and Air Quality & Wildfire Smoke docs."
        ),
    },
    {
        "title": "Air Quality & Wildfire Smoke: PM2.5, Masks, Clean Rooms & DIY Filtration",
        "tags": "air quality,aqi,wildfire smoke,smoke,pm2.5,particulates,n95,kn95,p100,respirator,"
                "hepa,air purifier,box fan filter,corsi-rosenthal,clean room,haze,smog,ash,"
                "smoke inhalation,asthma,visibility,canadian wildfires,mask",
        "content": (
            "Wildfire smoke can blanket regions THOUSANDS of miles from the fire (Canadian fires have "
            "repeatedly pushed US city air into the worst categories). The dangerous part is PM2.5 — "
            "particles under 2.5 microns that pass deep into the lungs and bloodstream. Highest risk: "
            "asthma/COPD, heart disease, children, elderly, pregnancy.\n\n"
            "AQI BANDS (US): 0-50 Good · 51-100 Moderate · 101-150 Unhealthy for Sensitive Groups · "
            "151-200 Unhealthy · 201-300 Very Unhealthy (everyone limit outdoor exertion) · 301+ "
            "Hazardous (stay inside).\n\n"
            "NO-INTERNET AQI ESTIMATE (5-3-1 visibility method): face away from the sun and judge how "
            "far you can see landmarks through the haze. Over ~10 mi: good. ~5 mi: sensitive groups cut "
            "outdoor exertion. ~3 mi: unhealthy for sensitive groups. ~1.5 mi: unhealthy for EVERYONE. "
            "Under ~1 mi: very unhealthy/hazardous — stay in.\n\n"
            "MASKS: cloth masks, surgical masks, and wet bandanas do NOT stop PM2.5. A fitted N95/KN95 "
            "(or better, P100) respirator filters ≥95% of fine particles — it must seal (beards break "
            "the seal; pinch the nose wire). An exhalation valve is fine for smoke (it protects the "
            "wearer). NO particulate mask stops carbon monoxide or hot toxic gases — near an active "
            "fire the answer is DISTANCE and evacuation, not a mask (see Carbon Monoxide doc).\n\n"
            "CLEAN ROOM: pick one interior room with few windows. Close all windows/doors and any "
            "fireplace damper; run HVAC on RECIRCULATE with the best filter it accepts (MERV 13 if the "
            "blower can handle it); shut fresh-air intakes. Run a HEPA purifier if you have one. While "
            "smoke is heavy: no candles, incense, frying, wood stove, vacuuming (non-HEPA), or smoking "
            "indoors — they all add fine particles.\n\n"
            "DIY BOX-FAN FILTER (Corsi–Rosenthal box): tape one MERV-13 furnace filter to the intake "
            "side of a box fan (arrow on filter pointing INTO the fan) — or tape four filters + the fan "
            "into a cube on a cardboard base for more airflow. Cheap and genuinely effective at scrubbing "
            "PM2.5 from a room. Replace filters when visibly gray. Newer (post-2012) box fans only.\n\n"
            "VEHICLES: windows up, ventilation on RECIRCULATE. A vehicle is a usable clean-air refuge "
            "for short trips, not for sleeping near an active fire front.\n\n"
            "SYMPTOMS: burning eyes, scratchy throat, cough, headache are expected; CHEST PAIN, "
            "wheezing, real shortness of breath, or confusion mean get to cleaner air and treat as a "
            "medical problem. Drink water; smoke dehydrates airways.\n\n"
            "ASH CLEANUP (after the event): wet ash down first, wear N95 + gloves + long sleeves, never "
            "use leaf blowers, wash ash off skin promptly, keep it out of storm drains and gardens used "
            "for food. RELATED: Severe Weather & Natural Disasters (wildfire behavior/evacuation); "
            "Carbon Monoxide: The Silent Killer; Radiological Protection (P100/CBRN PPE); "
            "Water Purification in the Field."
        ),
    },
    {
        "title": "Weather Prediction Without Instruments",
        "tags": "weather,forecast,clouds,barometric pressure,barometer,wind,red sky,storm prediction,"
                "nature signs,front,humidity,navigation,survival",
        "content": (
            "Reading weather buys hours of warning with no electronics. Watch the trend over a few hours, "
            "not a single sign.\n\n"
            "CLOUDS (the sky writes the forecast):\n"
            "• High wispy CIRRUS ('mare's tails') thickening to a milky CIRROSTRATUS halo around sun/moon "
            "often means a warm front and rain within 24-36 hours.\n"
            "• Lowering, thickening STRATUS/altostratus greying the sky = steady rain approaching.\n"
            "• Puffy fair-weather CUMULUS that towers up into CUMULONIMBUS (anvil top) by afternoon = "
            "thunderstorms, hail, gusts, possible tornado. Towering clouds + sudden cold downdraft = take cover.\n"
            "• Lens-shaped LENTICULAR clouds over mountains = strong high-altitude wind.\n\n"
            "PRESSURE & WIND (a falling barometer is the strongest storm signal):\n"
            "• Rapidly FALLING pressure = approaching storm/front, often within hours; faster fall = stronger "
            "storm. RISING/steady high pressure = clearing, fair weather.\n"
            "• No barometer? Improvise: a balloon over a jar stretches/bulges as pressure drops; smoke that "
            "rises straight = high pressure (fair), smoke that swirls/hangs low = low pressure (storm).\n"
            "• Wind backing/shifting and increasing = a front arriving. Buys-Ballot rule (N. Hemisphere): "
            "stand with your back to the wind and low pressure (bad weather) is to your LEFT.\n\n"
            "FOLK SIGNS WITH REAL BASIS:\n"
            "• 'Red sky at night, sailor's delight; red sky at morning, sailors take warning' — at mid-"
            "latitudes weather moves west-to-east; a red sunset = clear air to the west (good coming), a red "
            "sunrise = the clear air has passed and a system follows.\n"
            "• 'Ring around the moon, rain soon' — cirrostratus halo = moisture/warm front.\n"
            "• Distant sounds carry farther, smells get stronger, hair frizzes, pinecones close, salt clumps, "
            "joints ache = rising humidity/falling pressure ahead of rain.\n"
            "• Dew or frost on the grass at dawn usually means a dry day; a dry dawn can mean rain coming.\n"
            "• Birds/insects fly low before rain (denser air); calm, low-flying swallows = storm building.\n\n"
            "DEW POINT / FROST & FOG: clear calm nights radiate heat — expect cold, dew, or valley fog and "
            "possible frost even when the day was mild. RELATED: see the Severe Weather & Natural Disasters doc."
        ),
    },

    # ── COLD & HEAT INJURIES ─────────────────────────────────────────────────
    {
        "title": "Cold & Heat Injuries: Hypothermia, Frostbite, Heat Stroke",
        "tags": "hypothermia,frostbite,heat stroke,heat exhaustion,heat cramps,cold injury,trench foot,"
                "dehydration,rewarming,medical,first aid,exposure,temperature,survival",
        "content": (
            "COLD AND HEAT EMERGENCIES KILL FAST — recognize the stage and act.\n\n"
            "HYPOTHERMIA (core temperature dropping):\n"
            "MILD (~95-97F / 35-36C): shivering, clumsiness, slurred speech, 'the umbles' (stumbles, "
            "mumbles, fumbles, grumbles). Act here — it is reversible.\n"
            "MODERATE (~90-95F): violent then FADING shivering, confusion, poor judgment, drowsiness.\n"
            "SEVERE (<90F / 32C): shivering STOPS, rigid muscles, very slow weak pulse/breathing, "
            "unconscious; may appear dead ('paradoxical undressing' can occur). 'Not dead until WARM and "
            "dead' — attempt rewarming/CPR.\n"
            "TREAT: get out of wind/wet; remove wet clothing; insulate from the GROUND first; dry layers; "
            "wrap head/neck. Add a vapor barrier and external heat to the CORE (chest, armpits, neck, groin) "
            "— warm water bottles, body-to-body in a bag. Give warm sweet drinks ONLY if fully alert. "
            "Handle a severe casualty GENTLY (rough movement can trigger cardiac arrest); do not rub limbs; "
            "do not give alcohol/caffeine. Rewarm slowly.\n\n"
            "FROSTBITE (tissue freezing — fingers, toes, nose, ears, cheeks):\n"
            "FROSTNIP: white/numb skin, no ice crystals — rewarm with skin contact, fully reversible.\n"
            "FROSTBITE: hard, waxy, white/grey, numb skin; deep frostbite freezes solid and blisters/blackens.\n"
            "TREAT: rewarm ONLY when there is NO chance of refreezing — a partial thaw then refreeze is far "
            "worse. Immerse in warm (not hot) water 99-104F / 37-40C for 15-30 min until flushed/pliable; "
            "expect severe pain. Do NOT rub, do NOT use direct fire/stove heat (numb tissue burns easily), "
            "do NOT walk on thawed feet. Separate fingers/toes with gauze; protect blisters; evacuate.\n\n"
            "TRENCH/IMMERSION FOOT: prolonged cold + wet (above freezing). Prevent by keeping feet dry and "
            "changing socks; warm and dry gradually; do not rub.\n\n"
            "HEAT ILLNESS (escalating — catch it early):\n"
            "HEAT CRAMPS: painful muscle cramps from salt/water loss. Rest in shade, sip water with a pinch "
            "of salt or electrolytes; stretch gently.\n"
            "HEAT EXHAUSTION: heavy sweating, cool/clammy PALE skin, weakness, nausea, headache, fast weak "
            "pulse, dizziness. Move to shade, lie down, elevate legs, loosen clothing, cool with wet cloths/"
            "fanning, sip electrolyte fluids. Recover before any exertion.\n"
            "HEAT STROKE — LIFE THREAT: HOT skin (often dry, sweating may stop), red, temp 104F+ / 40C+, "
            "confusion, slurred speech, seizures, collapse. COOL AGGRESSIVELY NOW: shade, strip clothing, "
            "douse with water and fan, ice/cold packs to neck-armpits-groin, immerse in cool water if "
            "possible. Do not force fluids on the unconscious. This is a true emergency — cool first, evacuate.\n"
            "PREVENTION: hydrate ahead of thirst, electrolytes, shade, pace work to the cooler hours, "
            "acclimatize over days. RELATED: see the Severe Weather & Natural Disasters and wilderness first-aid docs."
        ),
    },

    # ── FIELD MEDICATIONS & SANITATION ───────────────────────────────────────
    {
        "title": "Field Antibiotics & Common Medications (Grid-Down Pharmacy)",
        "tags": "antibiotic,antibiotics,medication,amoxicillin,doxycycline,cephalexin,keflex,"
                "metronidazole,flagyl,ciprofloxacin,cipro,azithromycin,smz-tmp,fish antibiotic,"
                "dosage,painkiller,allergy,antihistamine,medical,pharmacy,infection,survival",
        "content": (
            ">> NOT MEDICAL ADVICE. Antibiotic misuse breeds resistance, causes dangerous reactions, and "
            "masks conditions needing surgery. Use a real clinician whenever possible. This is reference "
            "for austere, no-other-option grid-down scenarios only.\n\n"
            "STOCK & SHELF LIFE: most sealed, dry tablets/capsules remain largely potent years past the "
            "printed date (a conservative date, not a hard cliff). EXCEPTIONS — do NOT use expired: "
            "liquid antibiotics, tetracycline/doxycycline past date (degradation can harm kidneys), "
            "EpiPens, insulin, nitroglycerin, most reconstituted suspensions. Store cool, dark, dry.\n\n"
            "COMMON ORAL ANTIBIOTICS (typical adult ranges — confirm a real source before use):\n"
            "• AMOXICILLIN — broad first-line: ear/sinus/dental/respiratory/UTI. ~500 mg every 8 hr, 7-10 d.\n"
            "• AMOXICILLIN-CLAVULANATE (Augmentin) — animal bites, tougher infections.\n"
            "• CEPHALEXIN (Keflex) — skin/soft-tissue (cellulitis): ~500 mg every 6-8 hr.\n"
            "• DOXYCYCLINE — tick-borne (Lyme, RMSF), respiratory, MRSA skin, malaria; ~100 mg twice daily. "
            "Avoid in pregnancy and young children; causes sun sensitivity.\n"
            "• CIPROFLOXACIN — UTI, severe GI/dysentery, some respiratory; ~500 mg twice daily. Tendon/"
            "nerve risks; reserve for serious cases.\n"
            "• METRONIDAZOLE (Flagyl) — anaerobic/abdominal/dental abscess, giardia, C. diff; NO alcohol.\n"
            "• AZITHROMYCIN (Z-pack) — respiratory, some STIs, traveler's diarrhea.\n"
            "• SULFAMETHOXAZOLE-TRIMETHOPRIM (Bactrim/SMZ-TMP) — UTI, MRSA skin.\n"
            "FISH/AQUARIUM ANTIBIOTICS ('Fish Mox' = amoxicillin, 'Fish Flex' = cephalexin) are the same "
            "USP molecules; risk is unverified purity/dose and self-misdiagnosis.\n"
            "PRINCIPLES: pick the narrowest drug that covers the likely bug; complete the FULL course (do "
            "not stop when you feel better); watch for allergy (rash, swelling, trouble breathing = STOP, "
            "treat anaphylaxis). Penicillin-allergic? avoid amoxicillin/cephalexin family.\n\n"
            "OTC / SUPPORTIVE MEDS TO STOCK (know adult dosing):\n"
            "• IBUPROFEN (anti-inflammatory, fever, pain) and ACETAMINOPHEN (pain/fever, liver-limited) — "
            "can alternate. • ASPIRIN — chew 324 mg for suspected heart attack. • ANTIHISTAMINE "
            "(diphenhydramine/loratadine) for allergy; diphenhydramine also for sleep. • EPINEPHRINE "
            "auto-injector for anaphylaxis (life-saving). • LOPERAMIDE for diarrhea (but NOT with bloody/"
            "fever dysentery). • ORAL REHYDRATION SALTS. • ANTACID/PPI. • HYDROCORTISONE & ANTIFUNGAL "
            "creams. • TRIPLE-ANTIBIOTIC ointment. • ELECTROLYTES. RELATED: see the Sanitation & Disease "
            "Prevention, field-trauma, and wilderness first-aid docs."
        ),
    },
    {
        "title": "Sanitation & Disease Prevention in Grid-Down",
        "tags": "sanitation,latrine,outhouse,sewage,human waste,cholera,dysentery,typhoid,giardia,"
                "hygiene,hand washing,disease prevention,feces,vectors,fecal-oral,grid-down,medical,survival",
        "content": (
            "When plumbing and trash service stop, DISEASE — not violence — becomes the top killer. More "
            "people died of dysentery and typhoid in past collapses and sieges than of any weapon. "
            "Sanitation discipline is survival.\n\n"
            "THE FECAL-ORAL THREAT: cholera, typhoid, dysentery, hepatitis A, giardia, and norovirus spread "
            "when microscopic traces of feces reach the mouth — via hands, flies, and contaminated water. "
            "Break that chain at every step.\n\n"
            "HUMAN WASTE DISPOSAL:\n"
            "• CAT HOLE (short stay): 6-8 in deep, at least 200 ft (70 big steps) from any water, camp, or "
            "trail. Cover and disguise.\n"
            "• SLIT TRENCH / DEEP LATRINE (group, days-weeks): a trench downhill and well away (200 ft+) "
            "from water and the kitchen; add a thin layer of soil/lime/ash after each use to cut flies and "
            "odor; keep a lid/cover. Place it DOWNWIND and DOWNHILL of shelter and water sources.\n"
            "• BUCKET TOILET (indoors/urban): 5-gal bucket with a liner; cover each use with sawdust, peat, "
            "ash, or soil (a 'sawdust/humanure' system) to control odor and pathogens; seal and store/compost "
            "well away from food and water.\n"
            "• Keep latrines, graves, and animal pens far from and below wells/water intake.\n\n"
            "HAND HYGIENE (the single highest-impact habit):\n"
            "Wash with soap and water after any toileting, before handling food/water, and after handling "
            "the sick or the dead. No running water? Use a 'tippy-tap' (a hung jug tipped by a foot lever) "
            "so hands never re-touch a shared tap. Alcohol hand sanitizer (60%+) is a backup but does NOT "
            "remove visible dirt or kill norovirus/C.diff well.\n\n"
            "WATER & FOOD SAFETY: assume all surface and flood water is contaminated — boil/filter/treat "
            "(see water-purification doc). Keep stored/treated water covered and dip with a clean ladle, "
            "never bare hands. Cook food thoroughly; keep raw and cooked separate; protect all food from "
            "FLIES, which carry feces directly to your plate. Wash/peel produce with treated water.\n\n"
            "GREYWATER & TRASH: drain dishwater into a soakaway pit away from camp, not into standing pools "
            "(mosquito breeding). Burn or bury organic trash; standing water and garbage draw rats, flies, "
            "and mosquitoes — the vectors for plague, typhus, and malaria/dengue.\n\n"
            "SICKROOM DISCIPLINE: isolate the sick, dedicate utensils, disinfect surfaces with diluted bleach "
            "(~1 part household bleach to ~9 parts water), and aggressively replace fluids — most dysentery/"
            "cholera deaths are from DEHYDRATION, treatable with oral rehydration salts (1 L clean water + "
            "6 level tsp sugar + 1/2 tsp salt). RELATED: see the Field Antibiotics & Medications and water docs."
        ),
    },

    # ── FIELD SKILLS: KNOTS, SIGNALING, FOOD PROCUREMENT ─────────────────────
    {
        "title": "Knots & Rope Work for Survival",
        "tags": "knot,knots,rope,cordage,lashing,hitch,bowline,clove hitch,square knot,paracord,"
                "prusik,trucker's hitch,taut-line,bend,shelter,survival",
        "content": (
            "A handful of knots covers nearly every field need: a fixed loop, two hitches, a bend, a "
            "friction hitch, and a tensioning system. Practice them until you can tie them cold and in the "
            "dark.\n\n"
            "THE LOOP — BOWLINE:\n"
            "A fixed, non-slipping loop that never jams and is easy to untie after load. 'The rabbit comes "
            "out of the hole, around the tree, and back down the hole.' Use for rescue (around a person), "
            "securing a line to a fixed loop, hanging a ridgeline. The MOST important single knot.\n\n"
            "HITCHES (rope to a post/tree/ring):\n"
            "• CLOVE HITCH — fast, adjustable start/finish for lashings and ridgelines; can slip under "
            "varying load, so back it up.\n"
            "• TWO HALF-HITCHES — reliable general-purpose tie-off to a post or grommet; secure under steady "
            "load.\n"
            "• TAUT-LINE HITCH — an ADJUSTABLE friction loop that holds tension but slides by hand: perfect "
            "for tent guy-lines and tarp tie-outs you need to tighten.\n"
            "• TIMBER HITCH — for dragging/hauling a log or starting a diagonal lashing.\n\n"
            "BENDS (joining two ropes):\n"
            "• SHEET BEND — joins two ropes, ESPECIALLY of different thickness or wet; more secure than a "
            "square knot for this.\n"
            "• SQUARE (REEF) KNOT — flat binding knot for bundles, bandages, and lashings of EQUAL rope; do "
            "NOT trust it to join two lines under load (it can capsize) — use a sheet bend instead.\n\n"
            "FRICTION HITCH — PRUSIK:\n"
            "A loop of thin cord wrapped around a thicker rope; grips when loaded, slides when unweighted. "
            "Used to ascend a rope, rig a backup, or create a moveable anchor. Needs cord ~60-80% the "
            "diameter of the main rope.\n\n"
            "TENSION SYSTEM — TRUCKER'S HITCH:\n"
            "Creates a ~3:1 mechanical advantage to cinch a load TIGHT: form a loop midline (a slippery "
            "marlinspike/figure-8 on a bight), pass the working end around the anchor and back through the "
            "loop, haul, and lock off with two half-hitches. Use to lash loads to a pack/vehicle, tension a "
            "ridgeline drum-tight, or build a stretcher.\n\n"
            "LASHINGS (joining poles into structures): SQUARE LASHING binds two crossing poles at right "
            "angles (shelters, racks, frames); DIAGONAL LASHING braces poles that tend to spring apart; "
            "SHEAR LASHING joins two parallel poles (A-frame, tripod for water filtration or a pot hanger).\n\n"
            "CORDAGE TIPS: 550 paracord = 7 inner strands you can pull out for fishing line, snares, sewing, "
            "or floss. Make natural cordage by reverse-wrapping plant fibers (dogbane, nettle, yucca, inner "
            "bark, cattail). Singe or tape synthetic rope ends to stop fraying. RELATED: shelter, signaling, "
            "and trapping docs."
        ),
    },
    {
        "title": "Signaling & Rescue: Ground-to-Air, Mirror, Whistle & Morse",
        "tags": "signal,signaling,rescue,ground-to-air,signal mirror,heliograph,whistle,morse,sos,"
                "distress,flare,smoke,pace,search and rescue,survival",
        "content": (
            "RULE OF THREE = DISTRESS: three of anything — three fires, three whistle blasts, three "
            "flashes, three shouts — is the universal signal for help. Signals work best in contrast "
            "(dark on light, light on dark, movement against stillness) and in groups/patterns nature does "
            "not make (straight lines, geometric shapes).\n\n"
            "SIGNAL MIRROR / HELIOGRAPH (the longest-range daytime signal — flashes seen 10+ miles, even "
            "by aircraft over the horizon):\n"
            "Hold the mirror near your eye; extend your other hand toward the target making a 'V' or "
            "peace-sign with two fingers; flash the bright spot of sunlight back and forth across that "
            "notch so it sweeps the aircraft/target. Sweep the whole horizon even with no target in sight — "
            "pilots often see the flash before you see them. Any shiny surface (phone screen, CD, polished "
            "can lid, glasses) works.\n\n"
            "WHISTLE (carries far, saves your voice, works in fog/dark): three sharp blasts = HELP. A "
            "whistle beats shouting — far louder for far less energy. Keep one on your person.\n\n"
            "FIRE & SMOKE: three fires in a triangle or line = distress. DAY: make WHITE smoke with green "
            "vegetation, moss, or water on the fire — shows against dark ground/forest. Make BLACK smoke "
            "with rubber, oil, or plastic — shows against snow or light sky. NIGHT: bright flame; a fire "
            "built on a height or in a clearing is seen farthest. Keep signal fires ready to light fast.\n\n"
            "GROUND-TO-AIR SIGNALS (stamp in snow/sand, lay out gear, rocks, logs, or contrasting fabric — "
            "make them BIG, 10+ ft, with sharp straight edges):\n"
            "• V = Require assistance\n"
            "• X = Require medical assistance / unable to proceed\n"
            "• I (vertical bar) = Need a doctor, serious injuries\n"
            "• Arrow / → = Proceeding this direction (point of travel)\n"
            "• Y = Yes / affirmative · N = No / negative\n"
            "• SOS or a large triangle = general distress.\n"
            "Add SHADOW and motion: prop signals at an angle so they cast shadows visible from the air.\n\n"
            "MORSE / SOS: ... --- ... = three short, three long, three short (dot=short, dash=long). Send "
            "by flashlight, mirror, whistle, or banging metal: 'dit' short, 'dah' ~3x as long; gap between "
            "letters; longer gap between words. SOS is sent as one continuous group with no letter gaps.\n\n"
            "BODY/AIR REPLY FROM AIRCRAFT: a pilot rocking the wings (or a green light) = 'message "
            "received, understood'; circling/flying a straight line away then back can indicate a direction "
            "to travel. STAY VISIBLE and stay put once spotted — most lost persons are found near their last "
            "known point.\n\n"
            "WHEN NOT TO SIGNAL — RESCUE vs. OPSEC (opposite goals): signaling broadcasts your exact "
            "location to EVERYONE in range. If rescue is coming (lost hiker, SAR active), signal big and "
            "loud — everything above. If your threat is other PEOPLE and no rescue is coming (lawless "
            "grid-down period), do the OPPOSITE: go low-visibility — no signal fires, light and noise "
            "discipline at night, cook by day to hide flame/smoke. Decide which problem you actually have "
            "first. RELATED: see the Situational Awareness & Security doc for staying hidden, and the "
            "Knots & Rope Work, fire-starting, and navigation docs."
        ),
    },
    {
        "title": "Trapping, Snaring & Fishing: Off-Grid Food Procurement",
        "tags": "trap,trapping,snare,deadfall,fishing,trotline,gill net,fish trap,hunting,game,protein,"
                "field dress,gut,skin,procurement,calories,food,survival",
        "content": (
            "TRAPS WORK WHILE YOU SLEEP — the calorie math of survival favors passive traps and lines over "
            "active hunting, which burns more energy than it usually returns. Set MANY (a dozen+) along sign "
            "and let numbers do the work. NOTE: snaring/trapping wild game is restricted by law except in a "
            "genuine survival emergency — this is survival reference.\n\n"
            "READ THE SIGN FIRST: set traps on active runs/trails between feeding and water/cover — look for "
            "tracks, droppings (scat), nibbled vegetation, burrows, and 'pinch points' (gaps in fences, "
            "logs, under brush) where animals are funneled. Funnel them with sticks ('fences') into the trap.\n\n"
            "SNARES (small game — rabbit, squirrel, groundhog):\n"
            "A wire or cord noose set in a run at the animal's head height (rabbit loop ~fist-sized, ~1 "
            "hand-width off the ground). Anchor solidly or to an engine-bend/spring pole. Use a locking "
            "noose that tightens and won't reopen. Brass/steel snare wire holds a shape; paracord inner "
            "strands work in a pinch. Set in numbers; check at dawn.\n\n"
            "DEADFALLS (crushing trap): a heavy flat rock/log propped to drop on triggered prey. The "
            "PAIUTE and FIGURE-4 triggers are the classics — a bait stick releases the weight when nibbled. "
            "Use heavy weight (5-10x the animal's size). Effective for rodents and small game without wire.\n\n"
            "FISHING (often the best calorie return in the wild):\n"
            "• TROTLINE / SET LINE — a main line across a stream with many baited drop hooks; passive, runs "
            "overnight, checks at dawn. The highest-yield method per effort.\n"
            "• GILL NET — mesh stretched across a channel snags fish by the gills; very effective where legal/"
            "in emergency.\n"
            "• FISH TRAP / WEIR — a funnel of rocks or stakes (a 'V' pointing downstream into a pen) that "
            "guides fish in but not out; bottle/basket traps for minnows and crawfish.\n"
            "• HAND LINE & IMPROVISED HOOKS — from wire, thorns, bone, or a soda-tab gorge hook; bait with "
            "worms, grubs, insects, or cut bait. Fish dawn/dusk, near structure, undercut banks, and inlets.\n\n"
            "AFTER THE KILL — FIELD DRESS PROMPTLY (meat spoils fast):\n"
            "1. Bleed and GUT quickly — open the belly carefully (don't puncture intestines/bladder), remove "
            "the entrails, keep the heart, liver, and kidneys (nutrient-dense; discard if spotted/diseased).\n"
            "2. SKIN and cool the carcass; keep meat clean, shaded, and airy. \n"
            "3. PRESERVE what you can't eat now: cut into thin strips and dry/smoke into jerky (see "
            "food-preservation doc), or cool/cache.\n"
            "4. COOK THOROUGHLY — wild game carries trichinella, tularemia, and parasites; never eat "
            "predator/scavenger liver in excess (vitamin A toxicity). Wear gloves dressing rabbits/rodents "
            "(tularemia). RELATED: see the Knots & Rope Work, foraging, and food-preservation docs."
        ),
    },

    # ── BUSHCRAFT & PRIMITIVE TECHNOLOGY ─────────────────────────────────────
    {
        "title": "Bushcraft Adhesives & Sealants from Nature: Pine Pitch Glue, Birch Tar & Waterproofing",
        "tags": "bushcraft,primitive,adhesive,glue,sealant,pine pitch,pitch glue,resin,sap,tree resin,"
                "birch tar,pine tar,make tar,waterproofing,hide glue,beeswax,hafting,seal from nature",
        "content": (
            "You can make strong glue, sealant, and true tar entirely from the landscape. Do NOT reach "
            "for roofing/asphalt products for field repairs — those are petroleum products with toxic "
            "fumes, and everything they do for gear, pine pitch or birch tar does from nature.\n\n"
            "COLLECTING RESIN: pine, spruce, and fir bleed sap at wounds that hardens into amber lumps — "
            "pry these off with a stick or knife spine (sun-warmed lumps come off easier). To harvest "
            "more, cut a shallow 2–3 in V-notch through the bark low on the trunk of a healthy conifer "
            "and return in days–weeks; never cut a ring around the tree (girdling kills it).\n\n"
            "PINE PITCH GLUE (the workhorse):\n"
            "1. MELT the resin lumps SLOWLY in a tin near coals — never over open flame; hot resin is "
            "flammable and boils over. A tin-inside-a-tin (double boiler) is safest.\n"
            "2. STRAIN out bark/bugs (pour through grass bundle or cloth).\n"
            "3. TEMPER: stir in roughly 1 part fine-ground charcoal powder per 3–4 parts resin, plus a "
            "pinch of fiber binder (dry-crushed plant fiber, sawdust, or dried herbivore dung). Charcoal "
            "hardens it; fiber stops cracking. ~10–20% beeswax or hard tallow makes it less brittle "
            "(more flex, slightly weaker).\n"
            "4. PITCH STICKS: dip a stick in the melt, cool, re-dip until you have a lollipop of glue. "
            "To use later, warm the lump and smear. Reheatable forever.\n"
            "USES: hafting stone/bone points and blades onto handles (wrap with sinew, then coat the "
            "wrap), sealing seams of bark containers and canoes, waterproofing sewn seams and thread, "
            "plugging small holes in bottles/gourds/tarps, sticky firestarter.\n\n"
            "BIRCH TAR (true tar from nature, no petroleum): dry distillation. Pack a lidded tin TIGHT "
            "with rolled birch bark and punch one small hole in the tin's BOTTOM. Bury a smaller catch "
            "cup in the ground, set the packed tin on top so the hole drains into the cup, seal dirt "
            "around the seam, and build a fire AROUND/OVER the packed tin for 1–2 hours. With no oxygen "
            "inside, the bark bakes instead of burning and dark oil drips down into the cup. Simmer that "
            "oil gently to thicken it into tar/pitch. Birch tar glues arrowheads, waterproofs leather "
            "and rope, and protects wood. Resin-rich 'fatwood' pine heartwood dry-distills into pine "
            "tar the same way.\n\n"
            "HIDE GLUE: simmer (never boil) hide scrapings, sinew scraps, or split hooves in water for "
            "hours until the liquid gels when cooled — paint on hot. Very strong on wood (bow backing, "
            "furniture) but NOT water-resistant; seal it with pitch or wax if it may get wet.\n\n"
            "MEDICINE TOO: these same materials are real topical medicines — conifer resin salve "
            "(clinically studied for wound healing), diluted birch/pine tar for skin conditions and "
            "insect repellent, and fresh spruce gum as an antiseptic cover for minor cuts — full "
            "uses, dilutions, and cautions in the Medicinal Tars, Resins & Barks doc. "
            "RELATED: Medicinal Uses of Tree Tars, Resins & Barks; Fire Starting Techniques "
            "(fatwood/tinder); Bushcraft Cordage (sinew wraps to coat); Primitive Tools & Containers "
            "(bark containers, hafting); Emergency Shelter Construction."
        ),
    },
    {
        "title": "Bushcraft Cordage from Nature: Plant Fibers, Sinew & the Reverse Wrap",
        "tags": "bushcraft,primitive,cordage,natural fiber,plant fiber,dogbane,milkweed,nettle,yucca,"
                "basswood,cedar bark,sinew,rawhide,spruce root,reverse wrap,bowstring,withies,"
                "make rope,make string",
        "content": (
            "Rope and string are the multiplier for every other bushcraft task — shelters, traps, "
            "fishing, tools, repairs. Making cordage from plants is slow but the skill is simple.\n\n"
            "BEST NORTH AMERICAN FIBERS (strongest first):\n"
            "• SINEW (animal tendon — backstrap and lower legs): strongest per weight; dries hard and "
            "shrinks tight, ideal for hafting and bowstrings. Pound dry tendon, strip into threads, "
            "moisten to use.\n"
            "• RAWHIDE (babiche): cut a wet hide in a spiral into long lace; shrinks vice-tight as it "
            "dries — lashings, snowshoe webbing, bindings.\n"
            "• DOGBANE (Indian hemp): the premier plant fiber. Harvest DEAD dry red-brown stalks in "
            "late fall/winter. Also: MILKWEED and STINGING NETTLE stalks (same processing).\n"
            "• YUCCA leaves: pound/ret the leaf, keep the long fibers (tip even comes with a needle).\n"
            "• INNER BARK (bast) of basswood, cedar, willow, tulip poplar, elm: strip bark in spring, "
            "soak (ret) 2–4 weeks in still water until inner layers peel into ribbons.\n"
            "• SPRUCE ROOTS (watap): pull up shallow runners, split lengthwise, peel — ready-made "
            "sewing/lashing material for bark work, no twisting needed.\n"
            "• Quick-and-weak: cattail/grass braids for temporary ties and mats.\n\n"
            "PROCESSING STALK FIBER (dogbane/milkweed/nettle): flatten the stalk, snap the woody core "
            "in sections and peel it away from the bark ribbon, then rub/buff the ribbon between palms "
            "until soft fiber remains. Wear gloves for fresh nettle (stings until dried).\n\n"
            "REVERSE-WRAP TWO-PLY (the core technique):\n"
            "1. Take a bundle of fiber, twist its middle until it kinks, and fold at the kink — you now "
            "hold two plies.\n"
            "2. Twist the ply FARTHER from you tightly AWAY from you (clockwise), then wrap it TOWARD "
            "you back over the other ply (counterclockwise). Repeat with the ply now on top.\n"
            "3. Opposite twists lock against each other — that opposition is what makes rope rope.\n"
            "4. SPLICING: when a ply thins, lay a new pinch of fiber alongside and twist it in; stagger "
            "splices so both plies never thin at the same point.\n"
            "For thicker rope, reverse-wrap two finished cords together (4-ply), or braid three.\n\n"
            "STRENGTH RULES: test every cord before trusting it; wet plant cordage is weaker; NEVER "
            "trust natural cordage for climbing or life-loading. Good two-ply dogbane rivals light "
            "commercial string and has served as bowstring.\n\n"
            "WITHIES: green willow/hazel shoots twisted along their length until fibers separate become "
            "instant heavy 'rope' for lashing frames, rafts, and fences — crude but fast.\n"
            "USES BY MATERIAL: bow-drill string (rawhide/nettle/dogbane), snares (sinew/dogbane), net "
            "making, fishing line (nettle), sewing (sinew/spruce root), lashings (bark/withies/rawhide). "
            "RELATED: Knots & Rope Work for Survival (what to tie with it); Bushcraft Construction "
            "(lashed structures); Trapping, Snaring & Fishing; Bushcraft Adhesives (sinew + pitch hafting)."
        ),
    },
    {
        "title": "Bushcraft Construction: Lashed Structures, Wattle & Camp Engineering from Raw Timber",
        "tags": "bushcraft,primitive,lashing,tripod,shear lashing,square lashing,wattle,daub,"
                "camp furniture,raised bed,pot hanger,glut,baton,splitting,notch,travois,raft,"
                "build from nature,build with nothing",
        "content": (
            "With poles, cordage, and three lashings you can build most of a camp: beds, racks, "
            "tripods, shelters, fences, ladders, and rafts — no nails, no hardware.\n\n"
            "THE THREE LASHINGS (see knots doc for the underlying clove hitch/frapping):\n"
            "• SQUARE LASHING — joins two poles at ~90°: clove hitch on the vertical, 3+ wraps "
            "alternating over/under both poles, 2–3 frapping turns BETWEEN the poles to cinch, finish "
            "with a clove hitch. Platforms, racks, frames.\n"
            "• DIAGONAL LASHING — for poles that cross at an angle or must be sprung together "
            "(cross-bracing): timber hitch first, wraps across both diagonals, frap, clove hitch.\n"
            "• SHEAR (SHEER) LASHING — two or three poles side by side that scissor open: loose wraps "
            "around all poles, frap between each pair, spread the legs. This is the A-frame and TRIPOD "
            "lashing.\n"
            "With NATURAL cordage (bark, withies, rawhide): use more wraps than you would with rope, "
            "and favor wet rawhide — it shrinks drum-tight as it dries.\n\n"
            "THE TRIPOD IS THE WORKHORSE: cooking rig over fire, water-filter stand (tripod of cloth "
            "layers), smoke rack for meat, hide-drying frame, camp chair back, signal-fire platform.\n"
            "POT HANGER/CRANE: forked stake + leaning pole counterweighted, or a notched 'wagon stick' "
            "hung from the tripod chain-link style — raise/lower the pot by notch.\n\n"
            "SLEEP OFF THE GROUND: a RAISED BED (two rails square-lashed to four Y-stakes or two logs, "
            "cross-slats, then 4+ inches of boughs/duff) beats any ground bed — conduction to ground "
            "steals more heat than air does (see shelter doc).\n\n"
            "WATTLE: drive a row of stakes, weave flexible green shoots (willow/hazel) over-under — "
            "instant fence, windbreak, shelter wall, garden bed, or fish weir. DAUB (clay + chopped "
            "grass + a little dung, plastered over wattle both sides) turns it into a solid windproof "
            "wall. Keep daubed walls away from open flame until fully dry.\n\n"
            "WORKING TIMBER WITHOUT A SAW:\n"
            "• SPLITTING: baton your knife into the end grain, then drive hardwood WEDGES (gluts) down "
            "the split with a wooden maul. Rails, slats, and boards from logs.\n"
            "• FELLING/BUCKING small standing dead: controlled BURN-THROUGH at the cut point (wet mud "
            "collar limits the burn) beats hours of chopping with poor tools.\n"
            "• NOTCHES: a saddle notch (round cradle) locks stacked logs — small cabins, raised food "
            "caches; a V-notch and lashing joins rafters to a ridgepole.\n\n"
            "MOVING LOADS: TRAVOIS — two long poles crossed and lashed at one end (drag frame with "
            "cross-slats) hauls loads a person can't carry. RAFT: dry standing-dead logs (they float "
            "high), two cross-poles top and bottom, shear-lash every crossing with withies/rawhide; "
            "test in the shallows first.\n"
            "RELATED: Bushcraft Cordage (the lashing material); Knots & Rope Work for Survival; "
            "Emergency Shelter Construction; Primitive Tools & Containers (the cutting tools)."
        ),
    },
    {
        "title": "Primitive Tools & Containers: Stone Knapping, Fire-Hardening, Burn Bowls & Bark Baskets",
        "tags": "bushcraft,primitive,stone tools,flint knapping,knapping,obsidian,chert,flake,"
                "fire hardening,burn bowl,coal burning,bark container,birch bark basket,clay pottery,"
                "bone tools,awl,fish hook,hot rock boiling,digging stick,make tools",
        "content": (
            "Tools and containers from stone, wood, bone, and bark — the true 'build with nothing' "
            "kit.\n\n"
            "EXPEDIENT STONE TOOLS (a sharp flake in 5 minutes):\n"
            "• STONE CHOICE: you need glassy, fine-grained rock that breaks with curved (conchoidal) "
            "shell-shaped fractures — chert/flint, obsidian, jasper, quartzite; thick glass bottle "
            "bottoms work identically. Grainy granite/sandstone will not knap.\n"
            "• STRIKE: hold the core, hit near the edge with a palm-sized HAMMERSTONE at a glancing "
            "~60–80° blow — a flake pops off the underside. A fresh flake is scalpel-sharp as-is: "
            "skinning, cutting cordage fiber, shaving wood. Brittle — treat as disposable box cutters.\n"
            "• SAFETY: flakes fly — work downwind, on leather/bark over your leg, eyes averted or "
            "shielded; never knap barefoot.\n"
            "• REFINEMENT: an antler tine or hardwood pressure-flaker pushes small flakes off the edge "
            "to shape scrapers, drills, and points. Dull edges resharpen with one more flake.\n\n"
            "FIRE-HARDENING WOOD: hold a carved point (spear, digging stick, awl) ABOVE coals, rotating "
            "— you are baking the moisture out, NOT charring it. Slightly darkened = done; scrape and "
            "re-harden. Result is dramatically harder tips. The DIGGING STICK (fire-hardened chisel "
            "tip) is the most underrated tool in bushcraft: roots, tubers, post holes, clay digging.\n\n"
            "BURN BOWLS & SPOONS (coal burning): set a coal on a dry wood blank, feed it air through a "
            "hollow reed/tube, let it sink in, then SCRAPE the char out with a shell or stone flake; "
            "repeat burn-scrape cycles until the bowl is deep. Same method hollows spoons, cups, and "
            "canoe sections. Slow but nearly effortless attention-wise — tend it beside the fire.\n\n"
            "BARK CONTAINERS: birch, cedar, and tulip poplar bark peel in workable sheets (easiest in "
            "spring; take from downed/felled trees — stripping a ring of bark kills a live tree). Score "
            "and FOLD a rectangle of bark into a box/basket, pin the folds with split twigs, sew rims "
            "with SPRUCE ROOT, and seal seams with PINE PITCH (see adhesives doc) for a waterproof "
            "berry bucket or water carrier.\n\n"
            "BOILING WITHOUT A METAL POT — HOT ROCK BOILING: heat fist-sized DRY rocks from a DRY "
            "source in the fire (river-soaked rocks can explode from steam), lift with wood tongs, drop "
            "into a bark/wood/hide container of water; a few rock cycles brings a rolling boil for "
            "purification and cooking.\n\n"
            "CLAY POTTERY (where clay exists): find clay subsoil (moist ribbon that holds a bend "
            "without crumbling), clean it, TEMPER with ~20–30% sand or crushed shell (stops cracking), "
            "coil-build pots, smooth, dry SLOWLY for days, then pit-fire: pots nested in a fire built "
            "up gradually and burned hot for hours. Fired clay = cook pots and storage.\n\n"
            "BONE & ANTLER: awls and sewing needles (grind on abrasive stone, drill the eye with a "
            "stone flake spun between palms), fish HOOKS and gorges (a sharpened sliver tied "
            "mid-shank that toggles sideways when swallowed), antler pressure-flakers and wedges, "
            "scapula hoes. Boil bones first for easier working — and eat the marrow. "
            "RELATED: Bushcraft Adhesives (hafting/sealing); Bushcraft Cordage (bindings, bow-drill "
            "string); Bushcraft Construction; Water Purification in the Field (boiling); Wild Food "
            "Foraging Basics."
        ),
    },
    {
        "title": "Primitive Weapons & Hunting Tools: Selfbow, Arrows, Atlatl, Sling & Fishing Spear",
        "tags": "bushcraft,primitive,weapons,selfbow,bow making,arrow making,fletching,atlatl,sling,"
                "throwing stick,rabbit stick,fishing spear,gig,bow wood,tillering,make a bow,"
                "make arrows,hunting tools",
        "content": (
            "Effective hunting tools from raw wood, stone, and cordage. Check local hunting "
            "regulations before taking game with any of these; all of them demand PRACTICE before "
            "you rely on them for food.\n\n"
            "QUICKIE BOW (days) vs SELFBOW (weeks): a green-wood 'quickie' bow throws an arrow well "
            "enough for small game at short range and can be made in an afternoon; it loses power as "
            "it dries and 'follows the string'. A proper selfbow wants a SEASONED stave.\n"
            "• BOW WOODS (best first): osage orange, hickory, black locust, ash, elm, hard maple; "
            "in a pinch, any dense straight-grained hardwood sapling ~wrist thick.\n"
            "• LAYOUT: stave ~as tall as you; the BACK (side facing away from you) must be one "
            "unbroken growth ring — never cut through it or the bow explodes. All shaping is done on "
            "the BELLY (side facing you).\n"
            "• TILLERING (the whole art): remove belly wood a scrape at a time so both limbs bend in "
            "a smooth even arc, checking constantly against a notched stick. Rushed tillering = "
            "broken bow. Leave the handle section stiff.\n"
            "• STRING: sinew, rawhide lace, or reverse-wrapped dogbane/nettle (see cordage doc); "
            "never overdraw a natural-fiber string on a heavy bow.\n\n"
            "ARROWS matter more than the bow:\n"
            "• SHAFTS: straight shoots of red-osier dogwood, viburnum (arrowwood!), cane/bamboo, or "
            "split-out hardwood. Straighten by heating over coals and bending — sight down the shaft, "
            "repeat. Nock cut with a stone flake or knife.\n"
            "• FLETCHING: split feathers from any large bird, three per shaft, bound at both ends "
            "with fine sinew set in pitch glue. Even unfletched heavy arrows work at very short range.\n"
            "• POINTS: fire-hardened wood self-point (small game), knapped stone or bone broadhead "
            "hafted with sinew + pitch (see adhesives doc), or hammered metal scrap.\n\n"
            "ATLATL (spear-thrower): a 2 ft lever board with a hook/spur at the rear that engages a "
            "dimple in the back of a flexible 5–7 ft dart. The lever roughly doubles your throwing "
            "leverage — darts hit far harder than a hand-thrown spear. Easier to master than a bow "
            "and much faster to make: atlatl + 3 darts in a day.\n\n"
            "SLING: two ~24 in cords knotted to a diamond leather/woven pouch; smooth round stones. "
            "One cord looped on a finger, one held; one or two rotations and release toward the "
            "target. Devastating when practiced, wildly inaccurate before that — practice AWAY from "
            "camp.\n\n"
            "THROWING STICK (rabbit stick): a forearm-length curved hardwood club thrown "
            "side-arm to skim low through brush at rabbits/squirrels/birds. The simplest, most "
            "underrated meat-getter — always have one in hand while walking.\n\n"
            "FISHING SPEAR/GIG: split a green pole's end into 4 tines, spread with small twig "
            "wedges, fire-harden and sharpen each tine — the spread forgives bad aim. Spear fish in "
            "shallows at night by torchlight; aim BELOW where the fish appears (refraction). "
            "RELATED: Trapping, Snaring & Fishing (passive methods first — better calorie return); "
            "Primitive Tools & Containers (points/knapping); Bushcraft Cordage (strings); Bushcraft "
            "Adhesives (hafting)."
        ),
    },
    {
        "title": "Hide Tanning & Animal Materials: Rawhide, Brain-Tan Buckskin & Bark Tanning",
        "tags": "bushcraft,primitive,hide tanning,tanning,buckskin,brain tan,bark tan,rawhide,"
                "leather,deer hide,pelt,fleshing,smoking hide,sinew,tallow,use every part",
        "content": (
            "A deer hide is a sleeping mat, clothing, lashings, bag leather, and cordage stock — if "
            "you process it. Three end products, in order of effort:\n\n"
            "1. RAWHIDE (a day): FLESH the hide (scrape ALL fat/meat/membrane off the flesh side "
            "over a smooth log with a dull edge), optionally dehair (below), then lace it drum-tight "
            "in a frame or stake it out to dry. Result: stiff, hard, incredibly strong sheet — cut "
            "into lacing (babiche), bindings, containers (shrinks vice-tight as it dries), drum "
            "heads, shield/pack stiffener. Not for clothing; turns to slime when soaked.\n\n"
            "2. BRAIN-TAN BUCKSKIN (several days, the classic): \n"
            "• DEHAIR/GRAIN: soak the hide 2–4 days in wood-ash lye water (creamy — a handful of "
            "hardwood ash per gallon) until hair slips, then scrape off hair AND the thin outer "
            "grain layer. Rinse WELL (a day in clean water, wring repeatedly).\n"
            "• DRESS: every animal has enough brains to tan its own hide — simmer the brains into a "
            "warm slurry (or use egg yolks/rendered fat + a little soap as substitutes: it is the "
            "emulsified OILS that tan), and work it into the damp hide until saturated. Wring, "
            "repeat 2–3 times.\n"
            "• SOFTEN: the make-or-break step — stretch, pull, and buff the hide CONTINUOUSLY over "
            "a stake/cable as it dries (hours). If it dries unworked it reverts to rawhide; re-wet "
            "and try again.\n"
            "• SMOKE: sew the hide into a bag over a SMOLDERING punky-wood fire (smoke, never "
            "flame) until honey-colored both sides. Smoking sets the tan so the buckskin stays soft "
            "even after soakings. Unsmoked buckskin is ruined by its first rain.\n\n"
            "3. BARK-TAN LEATHER (weeks–months, real leather): steep crushed oak/hemlock/sumac bark "
            "in water for days to draw the TANNINS (same astringent chemistry as the oak-bark "
            "medicine doc), then submerge the dehaired hide, stirring daily and stepping up to "
            "stronger bark liquor over 2–8+ weeks until the tan has struck through (cut an edge to "
            "check). Work in oil/tallow while it dries. Result: firm water-resistant leather for "
            "soles, sheaths, straps.\n\n"
            "USE EVERY PART: SINEW from the backstrap/legs (bowstrings, sewing, hafting — see "
            "cordage doc); TALLOW rendered from fat (waterproofing, salve base, lamp fuel, pemmican); "
            "hooves/hide scraps simmered into HIDE GLUE (see adhesives doc); bones/antler into tools "
            "(see primitive tools doc); brains for the tan. "
            "RELATED: Trapping, Snaring & Fishing (field dressing); Bushcraft Cordage; Bushcraft "
            "Adhesives; Primitive Tools & Containers."
        ),
    },

    # ── HERBAL & NATURAL FIELD MEDICINE ──────────────────────────────────────
    {
        "title": "Medicinal Uses of Tree Tars, Resins & Barks: Birch Tar, Pine Tar, Resin Salve, Oak & Willow",
        "tags": "herbal,medicinal,natural remedies,birch tar,pine tar,tar medicinal,resin salve,"
                "spruce resin,oak bark,willow bark,salicin,tannins,astringent,skin conditions,"
                "traditional medicine,topical",
        "content": (
            "Tree tars, resins, and barks DO have real medicinal uses — topical ones, backed by "
            "centuries of practice and (for resin salve) modern clinical study. They are TOPICAL "
            "medicines: never taken internally, always patch-tested, and never a substitute for "
            "antibiotics or modern wound care when those are available.\n\n"
            "BIRCH TAR (dry-distilled birch bark — see the adhesives doc for making it): a "
            "traditional European skin medicine — antiseptic and anti-itch, long used on eczema, "
            "psoriasis, and stubborn scaly skin, and as a powerful insect repellent (a few drops on "
            "gear/clothing edges, not bare skin). ALWAYS DILUTE: pure tar irritates and sensitizes "
            "skin — blend a small amount (~5–10%) into tallow/oil/salve. Patch-test 24 h. Do not "
            "use on broken/raw skin or take internally.\n\n"
            "PINE TAR: same story — pine tar soap and weak pine tar ointments have a long history "
            "for itching, eczema, psoriasis, and dandruff, plus veterinary hoof/wound care. Same "
            "dilution and patch-test rules.\n\n"
            "CONIFER RESIN SALVE (spruce/pine/fir — the strongest evidence in this doc): fresh "
            "conifer resin is antimicrobial (resin acids), and ~10% spruce-resin salves have shown "
            "genuine wound-healing benefit in modern clinical studies on hard-to-heal wounds and "
            "pressure ulcers. Field recipe: melt 1 part clean resin into 4–5 parts tallow/oil with "
            "a little beeswax, strain, cool (see the field-medicine preparations doc). Use a thin "
            "layer on MINOR cuts, scrapes, cracked skin, and boils under a clean dressing. A pea of "
            "soft spruce gum pressed onto a small cut is the no-equipment version.\n\n"
            "OAK BARK (note: oak is NOT a tar tree — its medicine is TANNIN, not tar): simmer inner "
            "bark 15–20 min into a strong brown decoction. ASTRINGENT uses: cooled wash/compress "
            "for weeping rashes, poison-ivy blisters, and sweaty foot rot; gargle for sore "
            "throat/inflamed gums; and short-term (1–2 days) sips for diarrhea when nothing better "
            "exists — oral rehydration salts remain the priority (see sanitation doc). Long "
            "internal use irritates the gut.\n\n"
            "WILLOW BARK: contains salicin — chemistry aspirin was derived from. Decoction of "
            "spring inner bark (1–2 tsp per cup, simmer 10–15 min, up to 2–3 cups/day) for pain, "
            "headache, fever, inflammation. Slower but longer-acting than aspirin. SAME CAUTIONS AS "
            "ASPIRIN: skip if aspirin-allergic, on blood thinners, pregnant, ulcer-prone — and "
            "never for children/teens with fever (Reye's syndrome risk).\n\n"
            "LIMITS: infection spreading past a wound (redness advancing, fever, pus, red streaks) "
            "is an ANTIBIOTIC problem — see Field Antibiotics doc. Deep wounds, burns, and puncture "
            "wounds need modern care first. RELATED: Field Herbal Medicine (the plants); Making "
            "Field Medicine (tinctures/salves how-to); Bushcraft Adhesives (making birch tar); "
            "Field Antibiotics & Common Medications; Wilderness First Aid."
        ),
    },
    {
        "title": "Field Herbal Medicine: Medicinal Plants of North America",
        "tags": "herbal,medicinal plants,natural remedies,wild medicine,yarrow,plantain,jewelweed,"
                "mullein,elderberry,usnea,pine needle tea,cattail,goldenrod,herbs,plant identification,"
                "poison lookalikes",
        "content": (
            "Common, widespread North American plants with genuine traditional medicinal use. RULES "
            "FIRST: (1) 100% identification or DON'T — a field guide in the bug-out bag is medical "
            "gear; (2) herbs COMPLEMENT the medications doc, they don't replace it — infection, "
            "sepsis, and serious illness need real medicine; (3) pregnancy, children, and anyone on "
            "blood thinners/heart/psych meds: skip internal herbs unless you know the interaction.\n\n"
            "THE BIG FIVE (learn these first — most are lawn/roadside weeds):\n"
            "• PLANTAIN (Plantago — the flat lawn weed, not the banana): chew/crush a leaf and "
            "apply to bee stings, insect bites, splinters, nettle burn — noticeably draws and "
            "soothes. The all-purpose poultice leaf.\n"
            "• YARROW (feathery leaves, flat white flower cluster): crushed leaf packed on a minor "
            "cut is a classic styptic (slows bleeding); hot yarrow tea promotes sweating in early "
            "fever/chills. CAUTION: white umbrella-flowered LOOKALIKES include deadly poison "
            "hemlock — yarrow's fern-like feathery leaves and non-hollow stem are distinctive; if "
            "any doubt, don't.\n"
            "• JEWELWEED (orange snapdragon-like flower, succulent translucent stem, grows in wet "
            "ground — often right beside poison ivy and nettle): split the stem and rub the juice "
            "on fresh poison-ivy contact and stings.\n"
            "• MULLEIN (giant flannel-soft leaf rosette, tall yellow flower spike): leaf tea for "
            "coughs and chest congestion — STRAIN THROUGH CLOTH (the fine hairs irritate the "
            "throat). Flower-infused oil is the traditional earache drop (never into a possibly "
            "ruptured eardrum). The soft leaves are also the wilderness toilet paper.\n"
            "• WILLOW BARK: the field aspirin — full details and cautions in the tars/barks doc.\n\n"
            "ALSO WORTH KNOWING:\n"
            "• PINE NEEDLE TEA: rich in vitamin C (scurvy prevention on long grid-down winters) — "
            "chop fresh needles, steep (don't boil hard) 10 min. DEADLY LOOKALIKE WARNING: YEW "
            "(flat dark needles, red berry-like arils) kills — needle tea only from a POSITIVELY "
            "identified pine/spruce/fir. Pregnant women should avoid ponderosa-pine needles "
            "entirely.\n"
            "• ELDERBERRY: COOKED berry syrup is the traditional immune/flu remedy. Raw berries, "
            "and ALL leaves/stems/roots, contain cyanide-producing compounds — always cook berries; "
            "never tea the leaves.\n"
            "• USNEA ('old man's beard' — the pale green hair lichen with a white elastic core "
            "thread when gently pulled apart): antimicrobial (usnic acid); the classic use is dry "
            "wound dust or a tincture for skin infections.\n"
            "• CATTAIL: the clear gel between young inner leaves soothes minor burns and scrapes "
            "(plus the plant is a food/cordage/tinder powerhouse — see foraging doc).\n"
            "• GOLDENROD: leaf/flower tea as a mild urinary-tract flush and for seasonal-allergy "
            "season (it is NOT the ragweed causing the allergy).\n\n"
            "ABSOLUTE NO-GO FAMILY: wild parsley/carrot-family (umbrella flower clusters, hollow "
            "stems) — WATER HEMLOCK and POISON HEMLOCK are North America's deadliest plants and "
            "kill foragers every year who thought they had something edible or medicinal. No "
            "remedy in this doc comes from that family. "
            "RELATED: Medicinal Tars/Resins & Barks; Making Field Medicine (preparation methods); "
            "Wild Food Foraging Basics; Field Antibiotics & Common Medications; Wilderness First Aid."
        ),
    },
    {
        "title": "Making Field Medicine: Tinctures, Infusions, Decoctions, Poultices, Salves & Honey Dressings",
        "tags": "herbal,tincture,tinctures,salve,salves,poultice,infusion,decoction,infused oil,"
                "natural remedies,herbal preparation,honey dressing,resin salve,make medicine,"
                "beeswax,dosage",
        "content": (
            "How to turn identified medicinal plants into usable medicine with camp equipment. "
            "Potency of wild plants VARIES WILDLY — start with small doses, one herb at a time, and "
            "label everything (plant, part, date, solvent).\n\n"
            "INFUSION (= medicinal tea — for LEAVES & FLOWERS): 1–2 tsp dried (or 2× fresh) herb "
            "per cup of just-boiled water, COVERED (keeps volatile oils in), steep 10–15 min, "
            "strain. Drink warm; keeps ~1 day.\n\n"
            "DECOCTION (for ROOTS, BARKS & seeds — tougher material needs simmering): 1 tbsp cut "
            "root/bark per 2 cups cold water, bring up and SIMMER gently 15–20 min, strain. This is "
            "the method for willow bark and oak bark.\n\n"
            "TINCTURE (alcohol extract — strongest and keeps for YEARS, the bug-out format): fill a "
            "jar loosely with chopped fresh herb (halfway if dried), cover completely with 80–100 "
            "proof spirits (vodka/everclear-diluted), cap, shake daily, steep 4–6 weeks in the "
            "dark, strain into a dropper bottle. Typical folk adult dose: 1 dropperful (~1 ml) in "
            "water, 1–3×/day — START WITH LESS. No alcohol? Apple-cider vinegar works (weaker, "
            "6-month shelf life) — also the base for oxymel (vinegar + honey).\n\n"
            "POULTICE (fastest field medicine): crush, bruise, or literally chew the fresh clean "
            "leaf (plantain, yarrow) into a paste, pack it on the sting/bite/splinter/boil, cover "
            "with a cloth strip, refresh every few hours.\n\n"
            "INFUSED OIL → SALVE: use DRIED herb only (fresh-plant moisture turns oil rancid and "
            "risks botulism in anaerobic storage). Cover dried herb with olive/rendered oil; either "
            "sun-steep 2–4 weeks or hold in a double boiler on LOW 4–8 h; strain. For salve: melt "
            "~1 part beeswax into 4–5 parts infused oil, pour into tins. Add ~10–20% clean conifer "
            "resin at the melt stage for the antiseptic RESIN SALVE (see tars/resins doc). Tallow "
            "works as the fat base where there's no plant oil.\n\n"
            "HONEY DRESSING: honey is genuinely antibacterial (osmotic + peroxide activity) — a "
            "thin layer on a CLEANED shallow wound or burn under a dressing, changed daily, is one "
            "of the best-evidenced natural wound treatments there is. Never on deep punctures. "
            "Never feed honey to infants under 1 year.\n\n"
            "CHARCOAL: ACTIVATED charcoal (pharmacy) adsorbs many swallowed poisons — use only "
            "with poison-control/medical guidance; it does nothing for acids, alkalis, alcohols, "
            "or metals. Plain fire charcoal is far weaker — a crushed-charcoal slurry is a "
            "last-ditch measure only.\n\n"
            "WHEN HERBS ARE THE WRONG ANSWER: spreading infection/fever/red streaks (antibiotics "
            "doc), serious bleeding (trauma doc), dehydration from diarrhea (ORS — sanitation doc), "
            "anything abdominal-rigid, crushing chest pain, or airway trouble. Field medicine buys "
            "comfort and time; it does not replace definitive care. "
            "RELATED: Field Herbal Medicine (which plants); Medicinal Tars, Resins & Barks; Field "
            "Antibiotics & Common Medications; Wilderness First Aid; Sanitation & Disease Prevention."
        ),
    },
    {
        "title": "Herbal Remedies for Colds, Flu, Sore Throat, Congestion & Fever",
        "tags": "herbal,cold,flu,influenza,sore throat,stuffy nose,congestion,cough,fever,chills,"
                "headache,runny nose,sinus,tea for a cold,what tea,elderberry,yarrow,mullein,"
                "willow bark,thyme,ginger,honey,gargle,steam inhalation,natural remedies",
        "content": (
            "A symptom-by-symptom guide to comfort teas and remedies for the common cold and flu "
            "(headache, chills, fever, stuffy/runny nose, sinus pressure, sore throat, cough) using "
            "what you can gather or store. These EASE symptoms; they don't cure a virus. Above all: "
            "REST, and hydrate hard — warm fluids themselves thin mucus and soothe a throat. Read "
            "the RED FLAGS at the end.\n\n"
            "AN ALL-PURPOSE COLD & FLU TEA (safe base): steep grated fresh GINGER (warming, "
            "anti-nausea, eases aches) with a good squeeze of any citrus if you have it; off the "
            "boil, stir in HONEY (coats the throat, calms cough — genuinely evidence-backed) and a "
            "clove of crushed garlic if you can stand it. Drink hot, several times a day. This is "
            "the one to start with — no salicin, kid-safe over age 1.\n\n"
            "HEADACHE, BODY ACHES & FEVER — WILLOW BARK (nature's aspirin): decoction of the inner "
            "bark — 1–2 tsp dried per cup, simmer 10–15 min; up to ~2–3 cups/day. Slower and gentler "
            "than a pill but the same salicin chemistry. **SALICIN CAUTIONS (do not skip):** do NOT "
            "use willow bark (or meadowsweet) if you are allergic to aspirin/NSAIDs, on blood "
            "thinners, ulcer- or bleeding-prone, in the last trimester of pregnancy — and NEVER give "
            "it to a child or teenager with a fever (aspirin-type compounds are linked to Reye's "
            "syndrome). In those cases use ginger/elderberry teas and physical cooling instead. "
            "YARROW tea is the traditional alternative for a feverish chill: hot yarrow makes you "
            "sweat and ride out the fever — sip it under a blanket (positively ID it against poison "
            "hemlock first — see the lookalikes doc; avoid in pregnancy).\n\n"
            "STUFFY NOSE, SINUS & CHEST CONGESTION: the fastest relief is STEAM — lean over a bowl "
            "of just-boiled water with a towel tent and breathe 5–10 min (add pine/spruce tips, "
            "thyme, or crushed peppermint if handy); repeat as needed. THYME tea (strong "
            "decongestant/antiseptic, also a cough tea) and PINE-NEEDLE tea (vitamin C, opens "
            "sinuses — positively ID a true pine/spruce/fir, NEVER yew: lookalikes doc) both help. "
            "MULLEIN-leaf tea loosens chest congestion and calms a dry cough — STRAIN THROUGH CLOTH "
            "so the leaf hairs don't scratch the throat. Warm fluids + steam beat any single herb.\n\n"
            "SORE THROAT: GARGLE first — warm salt water (½ tsp salt/cup) several times a day is the "
            "most reliable remedy; a cooled, strong OAK-BARK or plain black-tea decoction (astringent "
            "tannins) tightens and soothes inflamed tissue; sage tea is a traditional throat gargle. "
            "Then sip honey in warm water or any of the teas above. A spoon of straight honey coats "
            "and quiets a cough at bedtime.\n\n"
            "IMMUNE SUPPORT / EARLY FLU: ELDERBERRY syrup or tea (COOKED berries only — raw berries "
            "and all green parts are toxic; see plant-ID doc) is the classic remedy taken at the "
            "first sign of flu. Rosehip and pine-needle teas add vitamin C.\n\n"
            "GENERAL CAUTIONS: pregnancy, nursing, young children, and anyone on prescription meds "
            "(blood thinners, heart, blood pressure, diabetes) should check each herb individually — "
            "'natural' is not 'harmless'. One herb at a time, modest amounts. Never give honey to an "
            "infant under 1 year.\n\n"
            "RED FLAGS — STOP HOME CARE, GET REAL MEDICAL HELP: trouble breathing or shortness of "
            "breath; chest pain; a stiff neck with headache and fever; confusion; a fever over ~103°F "
            "(39.4°C) or any fever past ~3–4 days; symptoms that improve then suddenly worsen "
            "(possible bacterial pneumonia/sinus/ear infection — an antibiotics question, see that "
            "doc); severe throat pain with drooling or inability to swallow; dehydration. "
            "RELATED: Field Herbal Medicine; Making Field Medicine (how to brew each form); "
            "Medicinal Tars, Resins & Barks (willow salicin detail); Deadly Lookalikes (yew/hemlock "
            "safety); Field Antibiotics & Common Medications; Wilderness First Aid."
        ),
    },

    # ── SPECIES-LEVEL IDENTIFICATION ─────────────────────────────────────────
    {
        "title": "Tree Identification by Bark, Leaf & Silhouette: Exact North American Species",
        "tags": "tree,tree identification,identify a tree,species,bark,leaf,needles,oak,maple,"
                "hickory,birch,pine,spruce,fir,hemlock,cedar,willow,basswood,black locust,"
                "osage orange,walnut,beech,ash,cottonwood,tulip poplar,what tree",
        "content": (
            "Field marks for the most USEFUL North American trees — each entry: how to know it, "
            "then why it matters. (Regional availability: see the Native Trees by US Region doc.)\n\n"
            "OAKS (Quercus): alternate branching, acorns. WHITE OAK (Q. alba): light ash-gray bark "
            "in loose vertical plates, leaves with ROUNDED lobes (no bristle tips) — sweeter acorns "
            "(still leach), premium tool/bow wood, tannin bark for hide tanning and astringent "
            "medicine. NORTHERN RED OAK (Q. rubra): darker bark with long shiny 'ski-trail' "
            "stripes, leaves with POINTED bristle-tipped lobes — bitterer acorns, same tannin uses.\n\n"
            "SUGAR MAPLE (Acer saccharum): OPPOSITE branching, 5-lobed leaf with SMOOTH margins "
            "and U-shaped notches, gray irregularly-plated bark — tap late winter for syrup "
            "(any maple works; boxelder is the weedy compound-leaf maple, also tappable).\n\n"
            "HICKORIES (Carya): alternate, compound leaves of 5–9 leaflets, hard round nuts. "
            "SHAGBARK (C. ovata) is unmistakable: bark in long peeling vertical strips. Best "
            "tool-handle/bow wood after osage; top firewood; edible nuts; green-wood smoke for meat.\n\n"
            "BLACK WALNUT (Juglans nigra): dark deeply-furrowed diamond-pattern bark, huge pinnate "
            "leaves (15–23 leaflets), tennis-ball green-husked nuts, twig pith CHAMBERED when split "
            "lengthwise — edible nuts, husk dye, prized wood.\n\n"
            "AMERICAN BEECH (Fagus grandifolia): smooth silver-gray 'elephant skin' bark at any "
            "age; papery toothed leaves that often cling all winter — edible beechnuts, dry-leaf "
            "bedding.\n\n"
            "CONIFER QUICK KEY — count and feel the needles:\n"
            "• PINES (Pinus): needles in BUNDLES. Eastern white pine (P. strobus) = 5 soft "
            "blue-green needles per bundle; red pine = 2 long brittle; pitch/loblolly = 3. All "
            "pines: resin, fatwood, needle tea (vit C), edible inner bark.\n"
            "• SPRUCE (Picea): SINGLE needles, SHARP and SQUARE — they roll between fingers; scaly "
            "gray-brown bark; cones hang DOWN. Resin salve, watap roots, shelter boughs.\n"
            "• FIR (Abies): single needles, FLAT and FRIENDLY (soft, won't roll), white stripes "
            "beneath; smooth bark with RESIN BLISTERS you can pop for clean pitch; cones stand UP.\n"
            "• EASTERN HEMLOCK (Tsuga canadensis — the TREE; unrelated to the deadly poison-hemlock "
            "PLANT): tiny flat needles with two white lines below, on little stalks; thumbnail-size "
            "cones; deeply furrowed cinnamon bark = the classic bark-tanning tannin source.\n"
            "• EASTERN RED CEDAR (Juniperus virginiana): scale-like foliage, stringy red-brown "
            "bark (tinder/cordage), blue juniper 'berries', rot-proof purple heartwood — posts, "
            "bow staves in the plains.\n\n"
            "BIRCHES (Betula): PAPER BIRCH (B. papyrifera): white bark peeling in papery sheets — "
            "containers, tar, fire-starting champion (burns wet). BLACK/SWEET BIRCH (B. lenta): "
            "dark non-peeling bark, broken twigs smell of WINTERGREEN — sap and twig tea.\n\n"
            "WORKHORSE SOFT & FIBER TREES:\n"
            "• BASSWOOD/LINDEN (Tilia americana): big HEART-SHAPED, lopsided (uneven-based) leaves; "
            "often several trunks sprouting in a clump; fragrant summer flowers hanging from a "
            "strap-like winged bract — THE inner-bark cordage tree, easiest friction-fire and "
            "carving wood, edible young leaves.\n"
            "• TULIP POPLAR (Liriodendron tulipifera): dead-straight trunk, unique 4-lobed "
            "'tulip-silhouette' leaf — bark containers, friction fire.\n"
            "• COTTONWOOD (Populus deltoides): triangular coarse-toothed leaves on flattened stalks "
            "(they shimmer), deep gray furrows, summer cotton fluff (tinder) — dry root is the "
            "friction-fire classic.\n\n"
            "WILLOW (SALIX) — KNOW IT ON SIGHT:\n"
            "Always at water — streambanks, pond edges, ditches. LEAVES: long and NARROW, "
            "lance/strap-shaped (many times longer than wide), finely toothed, usually pale or "
            "silvery beneath, on very short stalks. TWIGS: slender and extremely FLEXIBLE (a shoot "
            "bends into a hoop without snapping), often yellow/orange/reddish; each bud pressed "
            "flat to the twig under a SINGLE smooth cap-like scale — no other common tree has "
            "one-scale buds. FORM: native black willow (S. nigra) is a leaning, often "
            "multi-trunked streambank tree with dark blocky furrowed bark; planted weeping willow "
            "drapes twigs to the ground; many willows are just dense thickets of straight shoots. "
            "IF THE LEAF IS WIDE, OVAL, OR HEART-SHAPED IT IS NOT A WILLOW — that's basswood or "
            "cottonwood (above), which look nothing alike. USES: inner bark = salicin, the field "
            "aspirin (dose/cautions in the tars & barks doc); shoots = withies for lashing and "
            "baskets; light wood = friction-fire sets; cut stakes root where pushed into wet "
            "ground.\n\n"
            "TOOL & WEAPON WOODS: OSAGE ORANGE (Maclura pomifera): furrowed orange-tinged bark, "
            "thorny twigs, softball-size wrinkled green 'brain' fruit — the king bow wood. BLACK "
            "LOCUST (Robinia pseudoacacia): deeply furrowed ropy bark, PAIRED spines at leaf "
            "bases, hanging pea-family pods — rot-proof posts and excellent bows (seeds/inner "
            "bark toxic — tool tree, not food tree). WHITE ASH (Fraxinus americana): OPPOSITE "
            "compound leaves, tight diamond-furrowed bark — handles, bows, pack frames, and "
            "pound-to-split basket splints. "
            "RELATED: Native Trees: Best Species by US Region; Deadly Lookalikes (yew warning); "
            "Bushcraft Cordage; Bushcraft Adhesives (resins); Primitive Weapons (bow woods); "
            "Hide Tanning (tannin barks); Wild Food Foraging Basics."
        ),
    },
    {
        "title": "Medicinal & Useful Plant ID: Exact Species and What They Look Like",
        "tags": "herbal,medicinal plants,plant identification,identify a plant,species,what plant,"
                "yarrow,plantain,jewelweed,mullein,nettle,dogbane,milkweed,cattail,elderberry,"
                "usnea,goldenrod,field marks,botany",
        "content": (
            "Exact species and head-to-toe descriptions for the medicine and fiber plants in the "
            "herbal docs. Confirm EVERY field mark, not just one — and read the Deadly Lookalikes "
            "doc before using anything here.\n\n"
            "YARROW (Achillea millefolium), 1–3 ft: leaves soft, feathery, divided into hundreds "
            "of tiny segments ('millefolium' = thousand-leaf), spirally up a fuzzy, PITH-FILLED "
            "(not hollow) stem; flat-topped cluster of many small white (sometimes pink) flowers, "
            "each with ~5 tiny ray petals; strong pleasant-medicinal smell when crushed. Sunny "
            "fields/roadsides. The white-umbrella HEMLOCKS it must never be confused with have "
            "HOLLOW, smooth, often purple-marked stems, broader carrot-like leaflets, and a musty "
            "smell — see lookalikes doc.\n\n"
            "BROADLEAF PLANTAIN (Plantago major): ground-hugging rosette; broad oval leaves with "
            "3–7 strongly PARALLEL veins; tear a leaf slowly and stringy vein threads stretch "
            "between the halves; leafless 'rat-tail' seed spikes from the center. Narrowleaf "
            "plantain (P. lanceolata): same veins/strings on lance-shaped leaves, short dark cone "
            "head on a tall stalk. Lawns, paths, compacted soil everywhere.\n\n"
            "JEWELWEED (Impatiens capensis), 2–5 ft: succulent TRANSLUCENT stem, swollen at the "
            "joints, juicy when crushed; leaves shed water in silver beads (dunked leaves look "
            "silvered); orange trumpet flowers with red freckles dangle on threads (pale jewelweed "
            "I. pallida = yellow); ripe pods SNAP when touched. Shady wet ground — usually near the "
            "nettle and poison ivy it treats.\n\n"
            "COMMON MULLEIN (Verbascum thapsus): year 1 = rosette of huge gray-green leaves soft "
            "as flannel; year 2 = single stout 3–7 ft stalk with a spike of 5-petal yellow "
            "flowers. Dry waste ground. Nothing else feels like it.\n\n"
            "STINGING NETTLE (Urtica dioica), 2–6 ft: SQUARE-ish stem, OPPOSITE heart-to-lance "
            "coarsely-toothed leaves, drooping green thread-flowers from leaf joints, and fine "
            "stinging hairs throughout — the sting is the confirmation. Rich moist soil. Medicine "
            "tea, cooked green, and premier cordage fiber.\n\n"
            "FIBER TWINS — TELL THEM APART (both have MILKY SAP):\n"
            "• DOGBANE/INDIAN HEMP (Apocynum cannabinum), 2–5 ft: slender SMOOTH REDDISH stems, "
            "branching near the top, opposite willow-like leaves, tiny white-green flowers, thin "
            "paired pencil pods; stem stays standing dead all winter = harvest time. TOXIC "
            "internally — fiber/medicine-external only.\n"
            "• COMMON MILKWEED (Asclepias syriaca), 3–5 ft: THICK FUZZY UNBRANCHED stem, big oval "
            "velvety leaves, drooping pink ball-clusters of flowers, fat WARTY pods full of silk "
            "(tinder/insulation). Fiber is weaker than dogbane.\n\n"
            "CATTAIL (Typha latifolia): sword leaves in a flat fan from a spongy base, the "
            "hot-dog brown seed head confirms it. CAUTION before heads form: iris/sweetflag share "
            "the habitat — cattail's leaf base is spongy-white with clear mucilage gel (the burn "
            "gel) and no iris's flattened rainbow fan.\n\n"
            "ELDERBERRY (Sambucus canadensis): a multi-stem woody SHRUB; OPPOSITE compound leaves "
            "of 5–11 toothed leaflets; young bark studded with corky warts (lenticels); dinner-"
            "plate flat cream flower clusters → drooping masses of BB-size purple-black berries "
            "on red-tinged stems. The imposter POKEWEED is a non-woody magenta-stemmed herb with "
            "SIMPLE leaves and berries in a single column — see lookalikes doc.\n\n"
            "USNEA (Usnea spp., 'old man's beard'): pale gray-green tufted hair-lichen on dead "
            "branches; gently stretch a damp strand — a white ELASTIC CORE thread inside the "
            "green sheath is the definitive test (imposter lichens snap clean).\n\n"
            "GOLDENROD (Solidago canadensis and kin), 2–5 ft: alternate lance leaves, often "
            "3-veined; arching one-sided plumes of tiny GOLDEN flowers in late summer. Insect-"
            "pollinated (sticky pollen) — ragweed, wind-pollinated with green nothing-flowers, is "
            "the allergy culprit blooming beside it. "
            "RELATED: Field Herbal Medicine (uses/dosing); Deadly Lookalikes; Bushcraft Cordage "
            "(dogbane/nettle processing); Wild Food Foraging Basics; Tree Identification."
        ),
    },
    {
        "title": "Deadly Lookalikes: Toxic Plant Identification — Exact Species to Never Confuse",
        "tags": "herbal,poison lookalikes,toxic plants,poisonous plant,plant identification,"
                "water hemlock,poison hemlock,yew,pokeweed,death camas,false hellebore,moonseed,"
                "poison ivy,poison oak,poison sumac,foraging safety,nightshade,baneberry",
        "content": (
            "The short list of North American plants that kill or injure foragers — learn THESE "
            "to make every other ID safer. Rule zero: NEVER eat or brew anything from the wild "
            "carrot/parsley family (umbrella-shaped flower clusters, hollow stems) — the family "
            "contains the continent's two deadliest plants.\n\n"
            "WATER HEMLOCK (Cicuta maculata) — deadliest plant in North America; a bite can kill "
            "in hours (violent seizures). 3–6 ft, wet ground; smooth hollow stem, often "
            "purple-streaked; leaves 2–3× divided into lance toothed leaflets whose VEINS RUN TO "
            "THE NOTCHES between teeth (not the tips) — the classic tell; white umbrella flower "
            "clusters; rootstock chambered with yellow oily sap (do NOT cut it to check "
            "barehanded). Mistaken for: wild parsnip, water parsley, 'wild carrots'.\n\n"
            "POISON HEMLOCK (Conium maculatum — the plant that killed Socrates; unrelated to the "
            "hemlock TREE): 3–8 ft; ferny, carrot-like leaves; smooth HOLLOW stem with PURPLE "
            "BLOTCHES and a whitish bloom; musty 'mousy' smell when crushed; white umbels. Even "
            "handling warrants a wash. Mistaken for: wild carrot/Queen Anne's lace (which is "
            "HAIRY-stemmed, no purple blotches — but leave the whole family alone).\n\n"
            "YEW (Taxus spp.): evergreen shrub/small tree; FLAT, glossy, dark needles in two "
            "rows, soft to touch, NO white lines beneath and NO cones — instead single seeds in "
            "fleshy red CUPS (arils). Needles/seeds/wood are cardiotoxic; a handful of needles "
            "can stop an adult heart, and 'pine-needle tea' errors with yew are the classic "
            "fatality. TRUE pines/spruces/firs/hemlock-tree all bear CONES; hemlock-tree needles "
            "have two white stripes below.\n\n"
            "DEATH CAMAS (Toxicoscordion/Zigadenus spp.) vs WILD ONION: grass-like leaves and a "
            "bulb, cream-white flower clusters — but NO ONION SMELL. The rule is absolute: no "
            "onion/garlic odor = not an onion = drop it.\n\n"
            "FALSE HELLEBORE (Veratrum viride) vs RAMPS: both emerge in spring woods. False "
            "hellebore = stout stalk of strongly PLEATED/accordion-ribbed leaves, no smell. Ramps "
            "= 1–3 smooth flat leaves rising straight from a bulb, strong garlic-onion smell.\n\n"
            "POKEWEED (Phytolacca americana) vs ELDERBERRY: pokeweed is a big non-woody herb with "
            "smooth MAGENTA stems, large simple leaves, shiny purple berries in a single hanging "
            "COLUMN — roots/mature plant/berries toxic (traditional cooked-poke-salad prep is "
            "expert-only). Elderberry is a woody shrub, opposite compound leaves, berries in flat "
            "sprays (see plant-ID doc).\n\n"
            "MOONSEED (Menispermum canadense) vs WILD GRAPE: moonseed's dull black 'grapes' hold "
            "ONE crescent-moon flat seed and the vine has NO tendrils; real grapes have 2–4 round "
            "pear-shaped seeds and forked TENDRILS. Also skip 'grape' vines with white berries "
            "(poison ivy) or porcelain-colored berries.\n\n"
            "NIGHTSHADES (Solanum spp.): star-shaped 5-petal flowers with a yellow beak, berries "
            "like tiny cherry-tomatoes (green→yellow or black) — never eat wild 'tomatoes'. "
            "BANEBERRY/DOLL'S-EYES (Actaea): white berries with a black pupil dot on thick red "
            "stalks — cardiotoxic, and exactly the kind of berry children grab.\n\n"
            "BUCKEYE (Aesculus) vs EDIBLE CHESTNUT: chestnut burs are sea-urchin SPINY with 2–3 "
            "flat-sided nuts; buckeye husks are smooth/lightly warty with ONE glossy round nut. "
            "Buckeyes are toxic raw.\n\n"
            "CONTACT POISONS: POISON IVY (Toxicodendron radicans): 'leaves of three', middle "
            "leaflet on a longer stalk, ALTERNATE on the vine, climbing vines shaggy with brown "
            "aerial-root hairs, white waxy berries. POISON OAK: same but oak-lobed leaflets, "
            "shrubby, mostly West/Southeast. POISON SUMAC: swamp shrub, 7–13 UNtoothed leaflets "
            "on RED leaf stems, hanging white berries — while edible staghorn sumac has fuzzy "
            "stems and UPRIGHT red berry cones (the 'sumac-ade' one). Wash exposed skin+tools "
            "with soap/cold water fast; jewelweed helps (plant-ID doc); NEVER burn any of them — "
            "the smoke blisters lungs.\n\n"
            "IF INGESTION HAPPENS: induce nothing; small sips of water, activated charcoal only "
            "per the field-medicine doc, evacuate to medical care with a SAMPLE of the plant. "
            "RELATED: Medicinal & Useful Plant ID; Wild Food Foraging Basics; Field Herbal "
            "Medicine; Tree Identification (conifer key); US Wildlife: Encounter Protocols "
            "(bite/sting first aid)."
        ),
    },

    # ── NAVIGATION: COORDINATE SYSTEMS ───────────────────────────────────────
    {
        "title": "Map Coordinate Systems: MGRS, UTM, Lat/Long & Pace Count",
        "tags": "map,coordinates,mgrs,utm,latitude,longitude,grid reference,grid square,pace count,"
                "land navigation,topographic,declination,easting,northing,plot,resection,navigation,survival",
        "content": (
            "A coordinate system turns a position into numbers you can speak over the radio and plot on a "
            "map. Know how to read and report at least one precisely.\n\n"
            "LATITUDE / LONGITUDE (global, what GPS reports natively):\n"
            "• LATITUDE = degrees N/S of the equator (0 to 90); LONGITUDE = degrees E/W of the prime "
            "meridian (0 to 180). Always state lat first, then lon, with hemisphere.\n"
            "• Formats: degrees-minutes-seconds (DMS) 38 57 30 N; degrees-decimal-minutes (DDM) 38 57.5 N — "
            "the maritime/aviation standard; or decimal degrees (DD) 38.9583 — easiest for devices. 1 minute "
            "of latitude = 1 nautical mile (~1.15 statute mi) anywhere on earth.\n\n"
            "UTM / MGRS (military & topo — a flat metric grid that is easy to measure on a paper map):\n"
            "• UTM divides the world into 60 zones 6 degrees wide; position is an EASTING (meters east "
            "within the zone) and a NORTHING (meters north). MGRS adds a letter band and a 100,000 m square "
            "ID, e.g. 18S UJ 2348 0647.\n"
            "• READ RIGHT, THEN UP: read the easting (left-to-right) FIRST, then the northing (bottom-to-"
            "top). 'Right and Up.' More digits = more precision: a 4-digit grid (e.g. 2306) locates a 1000 m "
            "square, 6-digit (234064) a 100 m square, 8-digit a 10 m point.\n"
            "• Use a map's grid lines and a protractor/romer scale to interpolate the extra digits between "
            "printed gridlines. MGRS squares are perfect for a quick, terse radio report.\n\n"
            "DECLINATION (the must-know correction): a map is drawn to TRUE/grid north, but a compass points "
            "to MAGNETIC north. The angle between them (declination/grid-magnetic angle) varies by location "
            "and year — printed in the map margin. To convert a MAP bearing to a COMPASS bearing in an area "
            "of WEST declination, ADD it; for EAST declination, SUBTRACT (mnemonics: 'East is least, West is "
            "best' for magnetic-to-true; reverse for true-to-magnetic). Set the adjustment on the compass if "
            "it has one, and your bearings will match the ground.\n\n"
            "PACE COUNT (dead-reckoning distance without GPS): count every time the SAME foot strikes the "
            "ground = 1 pace. On flat ground most adults walk ~60-65 paces per 100 m — calibrate yours over "
            "a measured 100 m. Add paces on slopes, rough ground, and when tired. Use beads/knots ('ranger "
            "beads') to track each 100 m so you can estimate how far you've traveled on a bearing.\n\n"
            "PLOTTING & RESECTION: to plot a coordinate, find the gridlines and measure in. To find YOUR "
            "location without GPS (resection): take compass bearings to two or three known terrain features, "
            "convert to map bearings, draw the BACK-bearings on the map; where they cross is your position. "
            "RELATED: land-navigation, signaling, and Atlas GPS docs."
        ),
    },

    # ── SURVIVAL PSYCHOLOGY ──────────────────────────────────────────────────
    {
        "title": "Survival Psychology & Mental Resilience",
        "tags": "psychology,mental,stress,fear,morale,STOP,survival mindset,panic,will to survive,"
                "group dynamics,leadership,resilience,decision making,positive mental attitude,survival",
        "content": (
            "The mind is the most important survival tool. Studies of survivors show that WILL and a clear "
            "head matter more than gear or fitness — people with little equipment but a strong mindset "
            "routinely outlast better-equipped people who panic. Skills give confidence; confidence holds "
            "off panic; calm enables good decisions.\n\n"
            "S.T.O.P. — the first thing to do when lost or in crisis (fight the urge to bolt):\n"
            "• STOP — halt, sit down, do not run. Most fatal mistakes happen in the first panicked minutes.\n"
            "• THINK — what is the real, immediate threat? What do I have? What are my priorities?\n"
            "• OBSERVE — assess surroundings, weather, injuries, resources, your map/position.\n"
            "• PLAN — choose deliberate actions in priority order, then act calmly.\n\n"
            "PANIC is the enemy: it wastes energy, causes injury, and drives bad choices (running, ditching "
            "gear, drinking bad water). Counter it with controlled BREATHING (slow 4-count in, 4-count hold, "
            "4-count out — 'box breathing'), with ACTION (doing a known task restores a sense of control), "
            "and by breaking the situation into small solvable steps.\n\n"
            "SURVIVAL PRIORITIES keep the mind anchored — work the 'Rule of 3s': you can survive roughly "
            "3 minutes without air/severe bleeding controlled, 3 hours without shelter in harsh weather, "
            "3 days without water, 3 weeks without food. This orders your decisions when everything feels "
            "urgent: air/bleeding → shelter/warmth → water → food → signaling/rescue.\n\n"
            "STAGES OF A CRISIS RESPONSE: initial denial/shock, then deliberation, then the decisive moment "
            "to act. Survivors move THROUGH denial quickly, accept the new reality ('this is really "
            "happening'), and take ownership instead of waiting to be saved. They keep a POSITIVE MENTAL "
            "ATTITUDE, find small wins, use humor, and hold a reason to live (family, a task, faith).\n\n"
            "FEAR vs. DANGER: fear is useful only when it sharpens caution; uncontrolled fear is itself a "
            "danger. Name it, accept it, and act anyway. Boredom, loneliness, and despair on long survivals "
            "are real threats — keep a routine, set daily goals, maintain hygiene and gear (it preserves "
            "morale), and mark the passage of time.\n\n"
            "GROUP DYNAMICS & LEADERSHIP: in a group, assign clear roles and a decision-maker; shared tasks "
            "and a plan prevent paralysis and conflict. Care for the weakest member — morale is contagious "
            "both ways. Communicate honestly, ration fairly, and keep everyone busy with purpose. A calm "
            "leader who projects competence steadies the whole group. RELATED: situational-awareness, "
            "grid-down, and first-aid docs."
        ),
    },

    # ── EMERGENCY WATER PROCUREMENT ──────────────────────────────────────────
    {
        "title": "Emergency Water Procurement: Solar Still, Transpiration Bag & Condensation",
        "tags": "water,procurement,solar still,transpiration bag,vegetation bag,condensation,dew,"
                "rain catchment,snow,distillation,desert,hydration,survival",
        "content": (
            "When you cannot FIND water, you may be able to PRODUCE it. These methods are last resorts — "
            "most yield little, and some cost more sweat than they return. Treat anything from open ground "
            "(except distilled/condensed output) before drinking; see the water-purification doc.\n\n"
            "TRANSPIRATION BAG (best effort-to-yield — start here):\n"
            "Tie a clear plastic bag over a leafy, living branch of a NON-toxic tree/shrub in full sun; "
            "weight a low corner so condensation pools there. The plant pulls up groundwater and 'sweats' it "
            "out; it condenses inside the bag. Yields ~0.5-1 cup per bag per day for almost no labor — run "
            "MANY bags and rotate to fresh branches as leaves dry out. AVOID poisonous plants (oleander, "
            "yew, etc.) — some toxins carry into the water. The water is clean and drinkable as-is.\n\n"
            "VEGETATION/EVAPORATION BAG: a bag stuffed with cut, non-toxic green vegetation laid in the sun "
            "works the same way; faster to fill but the cut plants stop transpiring sooner.\n\n"
            "SOLAR STILL (below-ground — low yield, useful for FOUL water/urine/seawater):\n"
            "Dig a bowl-shaped hole ~3 ft wide and ~1.5-2 ft deep where soil is damp and sun is strong. "
            "Set a clean container in the center. Pile green vegetation (or pour foul/salt/urine water) into "
            "the hole AROUND — not in — the container. Cover the hole with a plastic sheet, seal the edges "
            "with soil/rocks, and set one small stone in the center so the sheet dips into a cone right over "
            "the container. Sun evaporates moisture; it condenses on the underside, runs to the low point, "
            "and DRIPS in — leaving salt, urine, and contaminants behind (it is distilled, so safe). Run a "
            "drinking tube out so you can sip without breaking the seal. Yield is only ~0.5-1 qt/day in good "
            "conditions and can be NET-NEGATIVE in dry soil — you may sweat out more digging it than you gain.\n\n"
            "DEW & RAIN:\n"
            "• DEW: at dawn, tie clean absorbent cloth/socks around your ankles and walk through dew-laden "
            "grass, or wipe non-toxic foliage with a cloth, then wring into a container. Surprisingly "
            "productive on cool clear mornings.\n"
            "• RAIN: the best source — funnel a tarp/poncho/sheet into containers; generally safe to drink "
            "untreated unless it ran over a dirty surface first. Stage catchment BEFORE the rain.\n\n"
            "SNOW & ICE: MELT before consuming — eating snow drops your core temperature and burns energy "
            "you can't spare. Melt with fire, a dark container in sun, or body heat in a water bag inside "
            "your jacket. Clear ice holds more water than fluffy snow; old compacted snow beats fresh.\n\n"
            "PLANT & GROUND SOURCES: green bamboo segments often hold drinkable water (shake to hear it); "
            "in a dry riverbed dig at the OUTSIDE of the lowest bend to reach subsurface water. MYTH/WARNING: "
            "do NOT rely on cactus — most cacti (including the barrel cactus) hold bitter, toxic fluid that "
            "causes vomiting and worsens dehydration. NEVER drink seawater, urine, blood, or alcohol "
            "straight (all accelerate dehydration) — though a solar still CAN reclaim clean water from them. "
            "RELATED: see the water-purification and emergency-shelter docs."
        ),
    },

    # ── DENTAL EMERGENCIES ───────────────────────────────────────────────────
    {
        "title": "Dental Emergencies in the Field",
        "tags": "dental,tooth,toothache,abscess,knocked-out tooth,avulsion,broken tooth,filling,crown,"
                "clove oil,dry socket,medical,first aid,grid-down,survival",
        "content": (
            "With no dentist, a tooth problem can sideline or even kill you (a spreading dental infection "
            "is dangerous). Stock a small dental kit: clove oil (eugenol), temporary dental cement (e.g. "
            "Dentemp), dental wax, gauze, floss, and OTC pain relievers. NOT a substitute for a dentist.\n\n"
            "TOOTHACHE / PULPITIS:\n"
            "Floss and rinse with warm SALT WATER to clear trapped debris; this alone often helps. Control "
            "pain with ibuprofen + acetaminophen together (multimodal). Dab CLOVE OIL on a cotton pellet "
            "against the tooth — eugenol numbs and is mildly antibacterial. Avoid hot/cold/sugary triggers. "
            "Throbbing that wakes you, with swelling or fever, signals an abscess.\n\n"
            "DENTAL ABSCESS (infection — take seriously):\n"
            "Swelling, severe pain, foul taste, fever. Warm salt-water rinses; pain control. A dental "
            "abscess is a classic grid-down indication for ANTIBIOTICS (amoxicillin first-line; "
            "metronidazole or clindamycin alternatives — see the Field Antibiotics doc). EMERGENCY signs: "
            "swelling spreading to the eye, jaw, or neck, or trouble swallowing/breathing (Ludwig's angina) "
            "— this can be fatal and needs definitive care. Drainage relieves pressure but is not a cure.\n\n"
            "KNOCKED-OUT TOOTH (avulsion — time-critical, best reimplanted within 30-60 min):\n"
            "Pick the tooth up by the CROWN only — NEVER touch the root. Gently rinse off dirt (do NOT scrub "
            "or remove attached tissue). If you can, REIMPLANT it into the socket the right way round and "
            "bite gently on gauze to hold it. If you cannot reimplant, STORE it in milk, in saline, or in "
            "the cheek pouch (saliva) — NEVER in plain water and never let it dry out. Seek a dentist ASAP. "
            "Do NOT reimplant a baby tooth.\n\n"
            "BROKEN / CHIPPED TOOTH: rinse; save any fragments in milk; cover a sharp edge with dental wax "
            "or sugarless gum; control pain; avoid chewing on that side.\n\n"
            "LOST FILLING / CROWN: clean and dry the cavity; pack with temporary dental cement, or sugarless "
            "gum/wax as a stopgap, to protect it from air/food. Re-seat a crown with temporary dental cement "
            "— NEVER use superglue or household adhesives inside the mouth.\n\n"
            "BLEEDING AFTER EXTRACTION/INJURY: bite firmly on rolled gauze (or a moist black TEA BAG — "
            "tannins aid clotting) for 30-45 min. For the next 24 hr do NOT spit forcefully, rinse hard, or "
            "use straws — that dislodges the clot and causes a painful 'dry socket.' RELATED: see the Field "
            "Antibiotics & Medications and wilderness first-aid docs."
        ),
    },

    # ── EMERGENCY CHILDBIRTH ─────────────────────────────────────────────────
    {
        "title": "Emergency Childbirth and Newborn Care",
        "tags": "childbirth,labor,delivery,newborn,baby,umbilical cord,placenta,postpartum hemorrhage,"
                "breech,obstetric,midwife,medical,first aid,survival",
        "content": (
            "Most births proceed normally and the mother's body does the work — your job is to stay calm, "
            "keep things clean and warm, and not interfere unless needed. Get trained help whenever "
            "possible. PREP CLEAN SUPPLIES: clean towels/sheets, two clean shoelaces or strings (cord ties), "
            "a blade/scissors sterilized by boiling, gloves, a bulb syringe, and blankets to keep the baby "
            "warm.\n\n"
            "STAGES OF LABOR:\n"
            "1. LABOR: contractions become regular and stronger; the water (amniotic sac) may break. Time "
            "them — when contractions last ~1 minute, come ~5 minutes apart, and have done so for ~1 hour "
            "('5-1-1'), birth is near.\n"
            "2. DELIVERY: a strong urge to push, then 'crowning' (the head appears). SUPPORT the head, do "
            "NOT pull; let it turn on its own, then gently guide the top shoulder out, then the bottom one; "
            "the slippery body follows fast — be ready to catch it.\n"
            "3. AFTERBIRTH: the placenta delivers on its own ~5-30 min later. Do NOT pull on the cord.\n\n"
            "DURING DELIVERY: position the mother however she is comfortable (semi-sitting or squatting uses "
            "gravity). If the membrane covers the baby's face, tear it. Check whether the cord is looped "
            "around the neck — if so, gently slip it over the head.\n\n"
            "NEWBORN CARE (the critical first minute):\n"
            "DRY the baby vigorously with a towel and keep it WARM — skin-to-skin on the mother's bare "
            "chest, both covered (newborns chill dangerously fast). Clear the MOUTH then the NOSE with the "
            "bulb syringe only if needed. The baby should cry and breathe within seconds; if not, rub the "
            "back and flick the soles of the feet, and begin newborn rescue breathing if it does not start "
            "breathing.\n\n"
            "UMBILICAL CORD: do NOT rush. Wait until the cord stops pulsing (~1-3 min). Tie it off tightly "
            "with clean string about 2 in (4 finger-widths) from the baby, tie again ~2 in further out, and "
            "cut between the two ties with a sterilized blade.\n\n"
            "PLACENTA & BLEEDING: let the placenta deliver naturally; never yank the cord. Then firmly "
            "MASSAGE the uterus (a firm grapefruit-sized mass below the navel) and put the baby to the "
            "breast — both make the uterus clamp down and control bleeding. POSTPARTUM HEMORRHAGE is the "
            "number-one killer: uterine massage + breastfeeding + (if available) misoprostol.\n\n"
            "DANGER SIGNS — get skilled help NOW: heavy bleeding before or after birth; a baby that will not "
            "breathe; BREECH presentation (feet/buttocks first) or a hand or the CORD coming first (cord "
            "prolapse — a true emergency); labor that stalls/obstructs for hours; or maternal seizures "
            "(eclampsia). RELATED: see the wilderness first-aid, hemorrhage-control, and Field Antibiotics docs."
        ),
    },

    # ── SURVIVAL FUNDAMENTALS (focused gap-fill docs) ────────────────────────
    {
        "title": "Human Water & Hydration Requirements and the Dehydration Timeline",
        "tags": "water,hydration,dehydration,drinking water,daily water,how much water,thirst,fluid,"
                "electrolytes,hyponatremia,urine color,water ration,rule of three,heat,survival",
        "content": (
            "TWO DIFFERENT NUMBERS — do not confuse them (this is the #1 mix-up):\n"
            "• DRINKING need (to stay healthy): an adult at rest in a temperate climate needs about "
            "2-3 LITERS of water per day from fluids (food supplies some of the rest). Bare short-term "
            "survival is possible on ~1 L/day, but with steadily declining strength and judgment.\n"
            "• PLANNING / STORAGE figure: about 1 GALLON (3.8 L) per person per day — this is the "
            "FEMA/Red Cross number and it includes drinking PLUS cooking and basic hygiene, not pure "
            "drinking. Store a minimum of 3 days, ideally 2+ weeks. So '1 gallon to stay alive' overstates "
            "the drinking minimum — the gallon is a household planning ration.\n\n"
            "HEAT & EXERTION SCALING (you cannot train your body to need less):\n"
            "Hard work in heat can demand 4-6+ liters/day; a person sweating heavily can lose up to ~1-1.5 "
            "L PER HOUR. That water must be replaced — there is no adaptation that removes the need.\n\n"
            "DEHYDRATION TIMELINE (the '3 days without water' rule of thumb):\n"
            "A person survives roughly 3 days without water — far less in heat or with exertion, somewhat "
            "more at rest in cool shade. Mental and physical performance degrade long before collapse.\n\n"
            "SIGNS OF DEHYDRATION — catch it early (best field gauge is URINE color & output):\n"
            "• Mild (~1-2% body weight lost): thirst, dark-yellow urine, dry mouth, headache, less urine.\n"
            "• Moderate (~3-5%): little/no urine, dizziness, fast pulse, irritability, lethargy, sunken eyes.\n"
            "• Severe (>6%): confusion, no urine, no tears, weak racing pulse, fainting — life-threatening.\n"
            "Pale-straw urine = hydrated; dark amber = drink now.\n\n"
            "ELECTROLYTES & OVER-DRINKING: with heavy sweating, water ALONE is not enough — replace salt "
            "or you risk HYPONATREMIA (drinking large volumes of plain water dilutes blood sodium → nausea, "
            "confusion, seizures). Add ~1/4-1/2 tsp salt per liter, or use oral rehydration salts, when "
            "sweating hard or treating diarrhea.\n\n"
            "THE RATIONING MISTAKE: do NOT 'save' water by sipping tiny amounts while you have it. Drink to "
            "thirst and stay functional; RATION YOUR SWEAT instead — work in the cool hours, rest in shade, "
            "cover skin, slow down. Water does more good inside you than in the bottle.\n\n"
            "NEVER drink seawater (2x dehydration rate), urine, or alcohol — all accelerate dehydration. "
            "RELATED: see the water-purification, Emergency Water Procurement, Cold & Heat Injuries, and "
            "Survival Priorities (Rule of 3s) docs."
        ),
    },
    {
        "title": "Caloric Needs, Food Rationing & the Starvation Timeline",
        "tags": "food,calorie,calories,nutrition,ration,rationing,starvation,fasting,macronutrient,"
                "protein,fat,carbohydrate,refeeding,rabbit starvation,survival,how long without food",
        "content": (
            "DAILY CALORIE NEEDS:\n"
            "• Sedentary adult: ~1,800-2,400 kcal/day (lower for small/older, higher for large/male).\n"
            "• Survival labor (chopping wood, rucking, building): 3,000-4,500+ kcal/day. COLD weather alone "
            "can raise needs 25-50% as the body burns fuel to stay warm.\n"
            "• Survival ration floor: ~1,200 kcal/day keeps you functional short-term; below that you lose "
            "weight, strength, and warmth steadily.\n\n"
            "MACRONUTRIENTS (you need all three — not just meat):\n"
            "• Carbohydrate (4 kcal/g): quick, easy energy. • Protein (4 kcal/g): tissue repair, ~0.8-1 g "
            "per kg body weight, more under stress. • Fat (9 kcal/g): most calorie-dense, critical for cold "
            "and long-term energy.\n"
            "WARNING — 'RABBIT STARVATION' (protein poisoning): living on very lean meat (rabbit, squirrel) "
            "with no fat or carbs causes nausea, diarrhea, weakness, and can kill even while you 'eat.' You "
            "MUST get fat and/or carbohydrate, not protein alone.\n\n"
            "STARVATION TIMELINE (the '3 weeks without food' rule — highly variable, weeks to ~2 months "
            "depending on body fat, hydration, cold, and exertion):\n"
            "• Hours 0-72: body burns stored glucose/glycogen then shifts to fat (ketosis); hunger pangs "
            "peak around days 1-3, then fade noticeably.\n"
            "• Week 1-3: running on body fat; energy, strength, warmth, and healing all decline, but you "
            "stay functional IF hydrated.\n"
            "• 3+ weeks: fat runs low, the body consumes muscle and organ tissue, the immune system fails; "
            "death follows once reserves are exhausted.\n"
            "KEY POINT: WATER outranks food. Dehydration kills in days; you have weeks on food. Never burn "
            "more calories hunting/foraging than the catch returns.\n\n"
            "RATIONING STRATEGY: inventory everything on day one; cut to ~75% of normal intake IMMEDIATELY "
            "(don't wait until stores run low); eat perishables first; favor calorie-dense, shelf-stable "
            "foods; keep a little 'morale food' (sugar, coffee) for the psychological lift.\n\n"
            "REFEEDING WARNING: after prolonged starvation, reintroduce food SLOWLY over days — a sudden "
            "large meal can trigger fatal 'refeeding syndrome' (an electrolyte/fluid collapse). Start with "
            "small, frequent portions. RELATED: see the Survival Gardening, Food Preservation, foraging, "
            "Trapping/Fishing, and Water & Hydration docs."
        ),
    },
    {
        "title": "Survival Priorities and the Rule of 3s",
        "tags": "survival priorities,rule of three,rule of 3s,rule of threes,how long can you survive,"
                "triage,exposure,survival fundamentals,first 24 hours,priorities,grid-down,STOP,survival",
        "content": (
            "THE RULE OF 3s — the master triage for ANY survival situation. The exact times are "
            "approximate, but the ORDER is the point: fix what kills you soonest, first.\n"
            "• ~3 MINUTES without AIR — or with an uncontrolled airway / severe bleeding.\n"
            "• ~3 HOURS without SHELTER in harsh weather — exposure (hypothermia or heat) kills faster than "
            "thirst and is the #1 wilderness killer.\n"
            "• ~3 DAYS without WATER.\n"
            "• ~3 WEEKS without FOOD.\n\n"
            "FIRST MINUTES — S.T.O.P. (do not panic-run): STOP, THINK, OBSERVE, PLAN. Most fatal mistakes "
            "happen in the first panicked minutes. Treat immediate life threats, then assess calmly.\n\n"
            "PRIORITY SEQUENCE IN PRACTICE:\n"
            "1. LIFE THREATS / SAFETY: stop major bleeding, open the airway, get clear of immediate danger "
            "(fire, water, traffic, falling hazards).\n"
            "2. SHELTER / THERMAL REGULATION: get out of wind, wet, and cold — or out of sun and heat; "
            "INSULATE FROM THE GROUND. Protecting core temperature usually outranks water.\n"
            "3. WATER: locate, procure, and treat water within the first day.\n"
            "4. FIRE / SIGNAL: warmth, water treatment, morale, cooking, and rescue signaling.\n"
            "5. FOOD: last priority — you have weeks; don't spend scarce early energy chasing calories.\n"
            "6. NAVIGATION / RESCUE: if lost, STAY PUT (you're easier to find) and signal; otherwise "
            "self-rescue deliberately, not in a panic.\n\n"
            "CAVEATS: the numbers shift hard with conditions — immersion in cold water cuts the '3 hours' to "
            "minutes; intense heat/exertion cuts the '3 days' of water to one. Treat the Rule of 3s as an "
            "ORDER OF OPERATIONS, not a guarantee.\n\n"
            "COMMON MISTAKE: people obsess over food and weapons and neglect SHELTER and WATER — the things "
            "that actually kill first. Build your skills and kit around the TOP of this list. RELATED: see "
            "the Water & Hydration, Caloric Needs, Emergency Shelter, Cold & Heat Injuries, Survival "
            "Psychology, and Signaling & Rescue docs."
        ),
    },
    {
        "title": "Bug-Out Bag, Get-Home Bag & Everyday Carry (EDC) Kit Contents",
        "tags": "bug-out bag,bug out bag,go bag,get-home bag,get home bag,edc,everyday carry,survival kit,"
                "72-hour kit,72 hour kit,preparedness,gear,packing list,go-bag,grid-down,survival",
        "content": (
            "BUILD EVERY KIT AROUND THE RULE OF 3s — shelter, water, fire, and first aid come before food "
            "and gadgets. Gear without the skill to use it is dead weight.\n\n"
            "THREE KIT TIERS:\n"
            "• EDC (Everyday Carry) — on your person daily: folding knife/multitool, lighter or ferro rod, "
            "small flashlight, phone + battery bank, cash, ID, pen, mini first-aid + a tourniquet if "
            "trained, water bottle, a few feet of cordage, whistle.\n"
            "• GET-HOME BAG — in your vehicle or at work, sized to WALK home (1-2 days): sturdy shoes/socks, "
            "water + filter, calorie-dense snacks, weather/rain layer, headlamp, compact first aid, paper "
            "map, multitool, fire, dust mask, gloves, a charged handheld radio.\n"
            "• BUG-OUT BAG (BOB) — a 72-hour pack to leave home fast, carrying the full load below.\n\n"
            "BUG-OUT BAG CONTENTS (prioritized by the Rule of 3s):\n"
            "• SHELTER/WARMTH: tarp or bivy, emergency space blanket, dry insulating layers, hat + gloves, "
            "rain shell, sleeping/ground pad.\n"
            "• WATER: 2-3 L carry capacity + a filter (Sawyer/LifeStraw) + chemical tablets (redundancy).\n"
            "• FIRE: two lighters + a ferro rod + dry tinder.\n"
            "• FIRST AID / MEDS: an IFAK (tourniquet, hemostatic gauze, pressure bandage) + a minor-injury "
            "kit + personal prescriptions + OTCs (pain, antihistamine, anti-diarrheal).\n"
            "• FOOD: 72 hours of calorie-dense, no-cook food (bars, jerky, nut butter); a metal cup/stove "
            "if weight allows.\n"
            "• LIGHT / COMMS / NAV: headlamp + spare batteries, handheld radio, whistle + signal mirror, "
            "map + compass, charged power bank.\n"
            "• TOOLS: a fixed-blade knife, multitool, paracord, duct tape, work gloves.\n"
            "• DOCS / CASH: copies of ID and key documents, cash in small bills, a contact list.\n"
            "• HYGIENE: hand sanitizer, wipes, a trowel, toilet paper, any personal items.\n\n"
            "PACKING PRINCIPLES:\n"
            "• Keep it LIGHT — aim for no more than ~20-25% of your body weight; you must be able to move "
            "and walk far with it.\n"
            "• 'Two is one, one is none' — carry redundancy on the things that kill you fastest (fire, water).\n"
            "• Tailor to your climate, season, route, and skill level; a desert kit and a winter kit differ.\n"
            "• Rotate consumables (water, food, meds, batteries) every few months so nothing is expired.\n"
            "• A get-home bag you actually carry beats a giant bug-out bag that stays in the closet. "
            "RELATED: see the Emergency Shelter, Water & Hydration, Signaling & Rescue, Field Trauma (IFAK), "
            "and Survival Priorities docs."
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
            "CROSS-CHECKING RAY (/explain):\n"
            "After any answer, type '/explain' or click the 🔍 explain button beneath it. Ray then shows "
            "exactly how and why it produced that answer: the path taken (knowledge base, live data, "
            "deterministic ballistic calculator, or training only), the source passages it retrieved with "
            "their similarity scores, the live context injected, the confidence tier, and the generation "
            "settings. This explanation is built deterministically from Ray's recorded pipeline — no AI "
            "call is made — so it cannot itself hallucinate, letting you verify the answer against its real "
            "sources. Use '/explain <message-id>' to explain a specific earlier answer.\n\n"
            "KNOWLEDGE BASE (RAG):\n"
            "Ray has access to curated reference documents embedded with the qwen3-embedding:0.6b model. "
            "When you ask a question, the top-matching documents are retrieved and injected into the prompt. "
            "Documents cover: survival skills, radio comms, ballistics, first aid, field craft, "
            "long-term sustainability, and Atlas Control app usage (this guide).\n\n"
            "AI SETTINGS (in Settings page → Settings tab → AI Settings section):\n"
            "• Model          — Ollama model to use (default qwen2.5:3b)\n"
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
            "• The Jetson has a dedicated SparkFun NEO-M8U GPS/IMU receiver. It connects either over USB "
            "or over the 40-pin GPIO header — as I2C/Qwiic (the u-blox DDC interface, /dev/i2c-7 at address "
            "0x42) or as a UART (/dev/ttyTHS1). Atlas auto-detects all of these; the active port (e.g. "
            "'i2c:7:0x42') is shown in GPS Status.\n"
            "• The host GPS fix is attributed to this Atlas device's own node ('Atlas Control'), not a "
            "separate entry, so the device shows as a single node on the map and node list.\n"
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
        "1. LANGUAGE CORE — my reasoning engine is a local qwen2.5:3b model (configurable) served "
        "by Ollama, kept warm in VRAM with hybrid-thinking disabled for instant responses.\n"
        "2. INDEXING (how I learn) — my knowledge base is a set of curated reference documents "
        "(survival, comms, ballistics, first aid, Atlas Control usage, and this self-description) "
        "stored in SQLite. At startup I split each document into section-sized PASSAGES (a new "
        "passage starts at a header-like line) and embed each passage separately as 'title + passage' "
        "with qwen3-embedding:0.6b, producing a 1024-dim vector per passage. This passage-level "
        "indexing means a long multi-topic doc exposes each section on its own — so the right "
        "paragraph can be retrieved instead of one averaged whole-doc blur. The embedder runs on the "
        "GPU alongside my chat model. Edited or cleared docs are automatically re-chunked and "
        "re-embedded in a background thread. A parallel FTS5 full-text index (ai_documents_fts) "
        "enables BM25 keyword search across all docs.\n"
        "3. ROUTING (how I classify your question) — before I generate anything, fast keyword "
        "scanners decide what your message needs: host telemetry, live mesh state, GPS/location "
        "grounding, math, physics/ballistics, or knowledge-base retrieval. Routing costs near-zero "
        "time and decides which of my subsystems wake up — including whether to pull in my live "
        "senses at all. If a question requires specific parameters I do not have (barrel twist "
        "rate, zero distance, custom load data), I answer with best available defaults and ask for "
        "the missing detail.\n"
        "4. RETRIEVAL (RAG, passage-level hybrid BM25 + cosine) — for knowledge questions, your "
        "message is embedded with a query-instruction prefix and compared to every passage by cosine "
        "similarity; each document is scored by its single best-matching passage. In parallel, a BM25 "
        "keyword search runs against a full-text index (title weighted 10×, tags 5×, content 1×). The "
        "hybrid score is max(v, 0.6·v + 0.4·bm25_norm) — but only for passages whose cosine similarity "
        "is ≥ 0.35 (the semantic plausibility gate). BM25 can only boost near-miss candidates; it "
        "cannot surface an unrelated doc on keyword coincidence. A topic router classifies the query "
        "(wildlife, medical, ballistics, etc.) and applies a +8% boost to docs whose tags match. When "
        "your question is location-scoped ('near me'), I append your resolved region to the query and "
        "add a +15% boost to passages that name your state/region — so 'dangerous animals near me' in "
        "Virginia returns copperheads and black bears, not far-away alligators. The best passage of "
        "each top-5 doc (hybrid ≥ 0.45) is pasted into my context, capped at a character budget so the "
        "highest-ranked sections are never truncated out of the window. The confidence footer uses the "
        "raw pre-boost cosine score, so it cannot be inflated. Live-data questions skip RAG because the "
        "answer is already injected fresh.\n"
        "5. CONTEXT ASSEMBLY — my working memory for each reply always includes my GPS fix "
        "reverse-geocoded to the nearest city (offline, from 41k US ZIP centroids + 68k world "
        "cities) for grounding, any retrieved knowledge docs, and the last 8 messages of our "
        "conversation. My live SENSES — host telemetry (CPU/GPU/RAM/temps/power) and live mesh "
        "state (nodes, channels, recent messages, telemetry, topology, alerts) — are NOT loaded "
        "into every prompt. They are a retrieved source, pulled in only when your message actually "
        "asks about them (routing in stage 3). Holding them out by default keeps me from narrating "
        "idle hardware or mesh numbers into answers that have nothing to do with them.\n"
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
        "or Training Knowledge). It is computed from what was really injected, not from my opinion.\n"
        "9. EXPLAIN / CROSS-CHECK — as I answer, I record a provenance trace of every decision: which "
        "path ran, which knowledge-base passages were retrieved at what similarity score (with the "
        "passage text), what live context was injected, the confidence tier, and my generation "
        "settings. Send '/explain' (or click the 🔍 explain button under any answer) and I replay that "
        "trace deterministically — NO language-model call is made, so the explanation itself cannot "
        "hallucinate. You can read the exact sources I drew on and confirm my answer matches them; if "
        "a claim isn't backed by a quoted passage and confidence is LOW, treat it as unverified. "
        "'/explain <id>' explains a specific earlier answer.\n\n"
        "KNOWLEDGE MAP — the Ray AI → Settings tab shows an interactive SVG visualization of my "
        "knowledge documents. Nodes are colored by topic cluster; edges connect docs whose "
        "cosine similarity is ≥ 0.55 (up to 6 per node, edge color shifts slate→amber with "
        "similarity). Click a node to highlight its connections, see a ranked 'Related' list, or "
        "switch to the 'Read' tab to view the full document text. Nodes can be dragged to reposition.\n\n"
        "MY MEMORY: conversations live in SQLite; I see the last 8 messages of the active chat. "
        "Document embeddings are cached in RAM for 120 s. I have no memory across separate chats.\n\n"
        "MY LIMITS: routing is keyword-based, so oddly-phrased questions can take the wrong path; "
        "documents are chunked by a header/length heuristic, so an unusually formatted doc can split "
        "mid-topic; the region boost only fires when a passage literally names your state/region; "
        "anything outside my knowledge base comes from my training data, which ends at my model's "
        "cutoff and is marked LOW confidence."
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


def _parse_explain_command(msg: str):
    """Detect the /explain cross-check command.

    Returns None when the message is not an /explain command, otherwise a dict
    {"message_id": int | None}.  Bare '/explain' targets the most recent answer;
    '/explain <id>' targets a specific assistant message.  Accepts a leading
    slash or bang so it works however the UI/user types it.
    """
    s = (msg or "").strip()
    if not s:
        return None
    low = s.lower()
    for prefix in ("/explain", "!explain", "explain this", "/why", "explain your answer"):
        if low == prefix:
            return {"message_id": None}
        if low.startswith(prefix + " "):
            arg = s[len(prefix):].strip()
            return {"message_id": int(arg)} if arg.isdigit() else {"message_id": None}
    return None


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
# Cosine similarity threshold — discard retrieved passages below this score.
# Recalibrated for qwen3-embedding:0.6b with embedding format v3 (passage-level
# chunks, title-prefixed): on-topic passages score ~0.45–0.67, off-topic noise
# stays ≤0.40 (measured: water query 0.64 vs wildlife distractor 0.38). 0.45 sits
# above the noise floor; the +8% topic-router and +15% region boosts lift
# borderline-relevant passages (0.43–0.46) over same-band irrelevant docs. The
# confidence footer uses the raw pre-boost cosine, so boosts can't inflate it.
# (nomic's old value was 0.30; do NOT reuse it — at 0.30 every query retrieves docs.)
_RAG_MIN_SCORE = 0.45

# Minimum vector similarity before BM25 is allowed to contribute. Below this
# the doc is semantically unrelated to the query; keyword overlap is coincidental
# and the BM25 boost would manufacture a false positive.
_BM25_GATE = 0.35

# Upper bound (chars) on the total KNOWLEDGE BASE block injected into a prompt.
# ~4 chars/token, so ~7000 chars ≈ 1750 tokens — leaves room for the system
# prompt, GPS/mesh/stats context, and chat history inside num_ctx=4096. Before
# passage-level retrieval, five whole multi-topic docs (~3700 tokens) overflowed
# the window and the lowest-ranked (often most relevant) docs were truncated out.
_RAG_CONTEXT_CHAR_BUDGET = 7000

# A single doc can answer a broad "how do I X" query with several sibling
# passages (e.g. "Emergency Shelter Construction" has LEAN-TO, QUINZHEE and a
# ground-conduction rule). Injecting only the single highest-cosine chunk per doc
# meant a near-tie picked ONE method and dropped the rest — "build a shelter" got
# the QUINZHEE snow chunk (0.5915) over the 3-season LEAN-TO (0.5892), so Ray
# answered with a snow shelter in June. Inject up to this many sibling passages
# per doc whose cosine is within _SIBLING_MARGIN of that doc's best chunk so the
# model sees all the relevant methods and can pick the situation-appropriate one.
_MAX_PASSAGES_PER_DOC = 3
_SIBLING_MARGIN = 0.12

# Phrases that mark a query as location-sensitive. When present, rag_search()
# augments the embedding query with the user's resolved region so "near me"
# pulls the regional passage instead of generically-dangerous far-away species.
_LOCATION_INTENT_KEYS = (
    "near me", "nearby", "near by", "around here", "around me", "in my area",
    "in the area", "in my region", "my area", "my region", "my location",
    "where i am", "close to me", "closest", "near here", "this area",
    "this region", "around my", "local wildlife", "locally",
)

# US state → wildlife-distribution region, matching the section headers in the
# "US Wildlife: Regional Species Distribution by US Region" knowledge-base doc.
_STATE_REGION = {
    "Maine": "Northeast", "New Hampshire": "Northeast", "Vermont": "Northeast",
    "Massachusetts": "Northeast", "Rhode Island": "Northeast", "Connecticut": "Northeast",
    "New York": "Northeast", "Pennsylvania": "Northeast", "New Jersey": "Northeast",
    "Delaware": "Northeast",
    "Virginia": "Mid-Atlantic", "West Virginia": "Mid-Atlantic", "Maryland": "Mid-Atlantic",
    "District of Columbia": "Mid-Atlantic", "North Carolina": "Mid-Atlantic",
    "Florida": "Southeast", "Georgia": "Southeast", "South Carolina": "Southeast",
    "Alabama": "Southeast", "Mississippi": "Southeast", "Louisiana": "Southeast",
    "Arkansas": "Southeast", "Tennessee": "Southeast",
    "Ohio": "Midwest", "Indiana": "Midwest", "Illinois": "Midwest", "Missouri": "Midwest",
    "Iowa": "Midwest", "Minnesota": "Midwest", "Wisconsin": "Midwest", "Michigan": "Midwest",
    "Kansas": "Midwest", "Nebraska": "Midwest", "North Dakota": "Midwest", "South Dakota": "Midwest",
    "Texas": "Southwest", "New Mexico": "Southwest", "Arizona": "Southwest", "Oklahoma": "Southwest",
    "Nevada": "Southwest", "Utah": "Southwest",
    "Montana": "Rocky Mountain", "Idaho": "Rocky Mountain", "Wyoming": "Rocky Mountain",
    "Colorado": "Rocky Mountain",
    "California": "Pacific Coast", "Oregon": "Pacific Coast", "Washington": "Pacific Coast",
    "Alaska": "Alaska", "Hawaii": "Hawaii",
}


def _region_for_state(state_name):
    """Return the wildlife-distribution region for a US state name, or None."""
    return _STATE_REGION.get((state_name or "").strip())


# Full state names sorted longest-first so "West Virginia" wins over "Virginia"
# and "Washington DC" over "Washington" when scanning a query.
_STATE_NAMES_BY_LEN = sorted(set(_US_STATE.values()), key=len, reverse=True)
_ABBR_TOKEN_RE = re.compile(r"\b([A-Z]{2})\b")


def _state_from_query(msg):
    """Return the full US state name explicitly mentioned in a query, or None.

    Matches a full state name in ANY case ("virginia", "North Carolina") or a
    2-letter abbreviation — but only when UPPERCASE ("VA", "NC"). Lowercase
    abbreviations are ignored on purpose: "in", "or", "me", "ok", "hi", "pa",
    "de", "la", "ma" are all common English words and would fire constantly.
    """
    if not msg:
        return None
    ml = msg.lower()
    for full in _STATE_NAMES_BY_LEN:
        if re.search(r"\b" + re.escape(full.lower()) + r"\b", ml):
            return full
    for m in _ABBR_TOKEN_RE.finditer(msg):
        full = _US_STATE.get(m.group(1))
        if full:
            return full
    return None


def _names_location(text, terms):
    """True if `text` names any location term as a WHOLE WORD (not a substring).

    Word-boundary matching is essential: a substring test lets the state term
    "virginia" match inside "Quercus virginiana" (the Live Oak species name),
    which wrongly handed the Southeast passage the regional boost for a Virginia
    query and surfaced Longleaf Pine instead of the Mid-Atlantic White Oak.
    """
    if not text or not terms:
        return False
    low = text.lower()
    return any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in terms)


# Cues that a question is actually about the user's surroundings. Only these
# queries get the location string folded into their embedding (see rag_search);
# topic questions like "how to clean water" must embed plainly so geography does
# not deflate the correct passage's cosine.
_LOCATION_SCOPED_CUES = (
    "near me", "around me", "near here", "near my", "nearby",
    "my area", "my region", "my location", "where i am", "where i'm",
    "in my", "around here", "this area", "this region", "close to me",
    "local to me", "locally",
)


def _is_location_scoped_query(query: str) -> bool:
    """True when the query explicitly asks about the user's surroundings.

    A NAMED place ('best tree to plant in Texas') is NOT location-scoped to the
    device — _resolve_location_hint already resolves the named place, and the
    structured region boost handles ranking, so it needs no embed pollution.
    """
    if not query:
        return False
    q = query.lower()
    return any(cue in q for cue in _LOCATION_SCOPED_CUES)


_WINTER_ONLY_CUES = ("winter", "snow", "quinzhee", "igloo", "ice ", "subzero", "sub-zero")


def _passage_off_season(text: str, season: str) -> bool:
    """True when a passage describes a winter-only technique out of winter.

    Lets the injector sink (not drop) a 'QUINZHEE (winter, snow)' passage below the
    3-season 'LEAN-TO' one in summer, so the small model leads with the method that
    actually fits the date instead of whichever chunk won the cosine by 0.003.
    """
    if season == "winter" or not text:
        return False
    return any(c in text.lower() for c in _WINTER_ONLY_CUES)


def _season_for(month: int) -> str:
    """Meteorological season for a month (Northern Hemisphere).

    Used purely to ground time-sensitive advice — June → 'summer' tells the model
    not to suggest a snow shelter. Meteorological (not astronomical) boundaries
    keep it unambiguous for the LLM.
    """
    return {
        12: "winter", 1: "winter", 2: "winter",
        3: "spring", 4: "spring", 5: "spring",
        6: "summer", 7: "summer", 8: "summer",
        9: "fall", 10: "fall", 11: "fall",
    }.get(month, "")


def _resolve_location_hint(user_loc, gps_fix):
    """Return a short 'City, State, Region US' hint for RAG query augmentation.

    An explicitly NAMED place in the query takes priority over ambient GPS —
    "best tree to plant in Texas" resolves to Texas even if the device is sitting
    in Virginia. Otherwise falls back to user-provided coords, then the device GPS
    fix. Returns None when no usable position is available.
    """
    if user_loc and user_loc.get("type") == "named":
        name = user_loc.get("name")
        if not name:
            return None
        # Normalize a 2-letter abbreviation ("VA") to the full state name so the
        # region lookup works and we never emit a 2-char location term, which would
        # substring-match unrelated words ("va" inside "nevada", "savanna").
        full = _US_STATE.get(name.strip().upper(), name)
        # Expand a known state to its region so location terms include e.g.
        # "mid-atlantic" — knowledge-base passages key their regional sections by
        # region name, not the abbreviation, so "virginia" alone never matches.
        region = _region_for_state(full)
        return f"{full}, {region} US" if region else full
    lat = lon = None
    if user_loc and user_loc.get("type") == "coords":
        lat, lon = user_loc.get("lat"), user_loc.get("lon")
    elif gps_fix and gps_fix.get("latitude") is not None:
        lat, lon = gps_fix.get("latitude"), gps_fix.get("longitude")
    if lat is None:
        return None
    try:
        city = _reverse_geocode(lat, lon)  # 'Herndon, Virginia'
    except Exception:
        city = None
    if not city:
        return None
    state = city.split(",")[-1].strip()
    region = _region_for_state(state)
    return f"{city}, {region} US" if region else city


def _doc_passage_heading(passage):
    """First non-empty line of a passage, trimmed — used as a section label."""
    for ln in (passage or "").split("\n"):
        s = ln.strip()
        if s:
            return s[:70].rstrip(":")
    return ""


def _chunk_document_text(content, target=700, hard_max=1100, min_section=130, min_keep=110):
    """Split a knowledge-base doc body into section-sized passages for embedding.

    Each labeled section (a 'header' line — short, ends in ':' or written in ALL
    CAPS) becomes its own passage so a long, multi-topic doc no longer collapses
    into a single averaged vector (which buried the right section and, once
    injected whole, overflowed the context window).

    Split rule: start a new passage at a header line once the current passage
    already holds a header plus at least `min_section` chars of content. The
    "already holds a header" guard is what stops a short intro/preamble from
    swallowing the first real section — the original heuristic required the chunk
    to reach ~60% of target before any header could split, so a ~190-char intro
    absorbed the section that followed it (e.g. the "EASTERN US → White Oak"
    section was buried in the doc's intro chunk, so a Virginia query never matched
    a clean Mid-Atlantic passage). Anything over hard_max is force-split, and
    runt passages below `min_keep` are merged into a neighbour so we never emit a
    near-empty header-only vector.
    """
    lines = (content or "").split("\n")

    def _is_header(ln):
        s = ln.strip()
        if not s or len(s) > 90:
            return False
        if s.endswith(":"):
            return True
        letters = [c for c in s if c.isalpha()]
        return bool(letters) and sum(1 for c in letters if c.isupper()) / len(letters) >= 0.7

    chunks, cur, cur_len, cur_has_header = [], [], 0, False
    for ln in lines:
        is_h = _is_header(ln)
        if cur and is_h and cur_has_header and cur_len >= min_section:
            chunks.append("\n".join(cur).strip())
            cur, cur_len, cur_has_header = [], 0, False
        cur.append(ln)
        cur_len += len(ln) + 1
        cur_has_header = cur_has_header or is_h
        if cur_len >= hard_max:
            chunks.append("\n".join(cur).strip())
            cur, cur_len, cur_has_header = [], 0, False
    tail = "\n".join(cur).strip()
    if tail:
        chunks.append(tail)
    chunks = [c for c in chunks if c]

    # Merge runt passages (below min_keep) into the previous chunk — or the next
    # one for a leading runt — without pushing any chunk past hard_max.
    merged = []
    for c in chunks:
        if merged and len(c) < min_keep and len(merged[-1]) + len(c) + 2 <= hard_max:
            merged[-1] = merged[-1] + "\n\n" + c
        else:
            merged.append(c)
    if (len(merged) >= 2 and len(merged[0]) < min_keep
            and len(merged[0]) + len(merged[1]) + 2 <= hard_max):
        merged[1] = merged[0] + "\n\n" + merged[1]
        merged = merged[1:]

    if not merged and content and content.strip():
        return [content.strip()]
    return merged


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
        # Embed on GPU (num_gpu omitted = Ollama auto-places). With
        # OLLAMA_MAX_LOADED_MODELS=2 the chat model stays resident alongside the
        # 0.6B embedder, so GPU embedding is far faster (passage-level indexing
        # makes many embed calls) without evicting the chat model.
        payload = json.dumps({"model": embed_model, "input": "warmup"}).encode()
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
                "repeat_penalty": float(settings.get("repeat_penalty", DEFAULT_SETTINGS["repeat_penalty"])),
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
        """Embed any documents that don't yet have embeddings (chunked format v3)."""
        import database as db
        docs = db.ai_get_documents_with_embeddings()
        for doc in docs:
            if not doc.get("embedding"):
                try:
                    payload = self._build_doc_embedding(doc)
                    db.ai_update_document_embedding(doc["id"], json.dumps(payload))
                    logger.info(
                        "Embedded doc id=%s (%d chunk%s): %s",
                        doc["id"], len(payload["chunks"]),
                        "" if len(payload["chunks"]) == 1 else "s", doc["title"],
                    )
                except Exception as e:
                    logger.warning(f"Failed to embed doc {doc['id']}: {e}")

    def _build_doc_embedding(self, doc):
        """Return the chunked embedding payload {'v':3,'chunks':[{h,t,e}]} for a doc.

        Each section/passage is embedded on its own so retrieval can surface the
        one paragraph that answers the query instead of the whole multi-topic doc.
        The doc title is kept as a short prefix for category-level recall, but the
        tags (very generic, e.g. "wildlife,...") are deliberately dropped from the
        per-chunk text so sibling chunks stay distinguishable from one another.
        """
        title = doc.get("title") or ""
        passages = _chunk_document_text(doc.get("content") or "")
        chunks = []
        for p in passages:
            embed_text = f"{title}\n\n{p}" if title else p
            emb = self.get_embed(embed_text)
            chunks.append({"h": _doc_passage_heading(p), "t": p, "e": emb})
        return {"v": 3, "chunks": chunks}

    # ------------------------------------------------------------------
    # Qwen3-Embedding is instruction-aware: the recommended usage prefixes
    # QUERIES with a one-line task instruction while embedding DOCUMENTS plain.
    # This query/document asymmetry measurably improves retrieval. Tailored to
    # the Atlas knowledge base (survival / radio / nav / app usage).
    _EMBED_QUERY_INSTRUCT = (
        "Given a question, retrieve survival, bushcraft/primitive-technology, "
        "medical, herbal/natural-remedy, radiological/CBRN, weather, "
        "air-quality, radio, navigation, and Atlas Control reference passages "
        "that answer it"
    )

    # ── Topic router ───────────────────────────────────────────────────────────
    # Keyword substrings that signal a query belongs to each knowledge category.
    # Used by _classify_query_category() to give matching docs a small score
    # bonus in rag_search() so the right cluster surfaces first.
    _QUERY_CATEGORY_KEYS: dict = {
        "water":      ["water", "purif", "drink", "hydrat", "stream", "filter", "boil",
                       "solar still", "transpiration", "condensation", "collect water",
                       "find water", "procure water"],
        "fire":       ["fire", "tinder", "ignit", "spark", "flame", "campfire"],
        "shelter":    ["shelter", "bivouac", "tarp", "insul", "sleep", " tent"],
        "food":       ["food", "forag", "edible", "plant", "mushroom", "calor",
                       "garden", "crop", "livestock", "harvest", "berr",
                       "starvation", "starving", "fasting", "rationing",
                       "macronutrient", "how long without food", "nutrition"],
        "medical":    ["medic", "first aid", "wound", "bleed", "hemorrh", "tourni",
                       "cpr", "shock", "airway", "infect", "fractur", "burn",
                       "hypotherm", "trauma", "bite", "sting", "inject",
                       "dental", "tooth", "toothache", "abscess", "childbirth",
                       "giving birth", "in labor", "newborn", "umbilical",
                       "placenta", "breech", "delivering a baby"],
        "navigation": ["navigat", "compass", "bearing", "azimuth", "landmark", "orienteer",
                       "mgrs", "utm", "coordinate", "grid reference", "grid square",
                       "declination", "pace count", "latitude", "longitude", "easting",
                       "northing", "resection", "topographic", "map reading"],
        "comms":      ["mesh", "meshtastic", "radio", "frequen", "communic",
                       "gmrs", "amateur", "transmi", "antenna"],
        "power":      ["power", "batter", "solar", "generat", "watt", "volt", "charge"],
        "security":   ["security", "opsec", "patrol", "defense", "threat", "surveil"],
        "ballistics": ["ballistic", "moa", "mil", "bullet drop", "wind drift",
                       "scope click", "dope", "hold over"],
        "firearms":   ["firearm", "rifle", "pistol", "handgun", "malfunction",
                       "caliber", "ammunition", "trigger", "clean"],
        "grid_down":  ["grid-down", "grid down", "collapse", "shtf", "barter",
                       "trade goods", "sustain", "long-term survival", "community defense",
                       "bug-out", "bug out", "go bag", "go-bag", "get-home", "get home bag",
                       "edc", "everyday carry", "every day carry", "survival kit",
                       "72-hour", "72 hour", "rule of three", "rule of 3", "rule of threes",
                       "survival priorit", "how long can a human survive", "packing list",
                       "what to pack", "what should i pack"],
        "vehicles":   ["vehicle", "fuel", "oil change", "tire", "off-road", "recover",
                       "engine", "mainten"],
        "wildlife":   ["wildlife", "animal", "snake", "bear", "mountain lion", "wolf",
                       "spider", "scorpion", "tick", "rabies", "alligator", "shark",
                       "venomous", "predator", "encounter", "rattlesnake", "bitten"],
        # bare "hemlock"/"fir"/"ash"/"willow" deliberately absent: poison-hemlock
        # belongs to herbal, "fir" hides in "first", "ash" in "wash"/"flash",
        # willow-bark medicine outranks the tree.
        "trees":      ["tree", "native", "oak", "pine", "maple", "bark", "forest",
                       "tree identification", "identify a tree", "what tree",
                       "tree species", "kind of tree", "tree bark", "hickory",
                       "birch", "spruce", "cedar", "walnut", "beech", "basswood",
                       "cottonwood", "osage"],
        "nuclear":    ["nuclear", "radiation", "radioact", "fallout", "dosimet",
                       "geiger", "roentgen", "sievert", "cbrn", "nuke", "dirty bomb",
                       "rad ", "rem ", "potassium iodide", "iodide", "contaminat",
                       "acute radiation", "7-10 rule", "7 10 rule", "decontaminat",
                       # phrases that must beat the generic "shelter" category so a
                       # "build a fallout shelter" query surfaces the mass/earth doc,
                       # not the wilderness lean-to.
                       "fallout shelter", "radiation shelter", "nuclear shelter",
                       "blast shelter", "expedient shelter", "shelter from fallout",
                       "shelter from radiation", "shield from radiation",
                       "protection factor", "halving thickness"],
        # "emp" is intentionally matched only as a phrase, never as a bare
        # substring — "emp" lives inside "temperature", "attempt", etc.
        "emp":        ["emp attack", "emp event", "emp strike", "emp blast",
                       "an emp", "emp protect", "emp proof", "emp-proof",
                       "nuclear emp", "electromagnetic pulse", "cme", "solar storm",
                       "solar flare", "coronal mass", "faraday", "carrington",
                       "geomagnetic"],
        "weather":    ["weather", "tornado", "hurricane", "flood", "earthquake",
                       "wildfire", "lightning", "blizzard", "barometr", "forecast",
                       "storm", "cloud", "disaster", "evacuat"],
        # "smoke" is matched only as phrases — bare "smoke" would collide with
        # smoking meat (food preservation) and smoke signals. "aqi"/"hepa"/
        # "haze" are word-boundary tokens (see _WORD_BOUNDARY_KEYWORDS).
        "air_quality": ["air quality", "aqi", "wildfire smoke", "fire smoke",
                        "smoke inhalation", "canadian fire", "canada fire",
                        "canadian wildfire", "canadian smoke",
                        "smoky", "smoke outside", "smoke in the air", "smoke from",
                        "pm2.5", "pm 2.5", "particulate", "n95", "kn95", "p100",
                        "respirator", "hepa", "air purifier", "air filter",
                        "box fan", "corsi", "clean room", "clean air", "haze",
                        "smog", "bad air"],
        "cold_heat":  ["hypotherm", "frostbite", "frostnip", "heat stroke",
                       "heat exhaustion", "heat cramp", "trench foot", "rewarm",
                       "cold injury", "heat illness", "overheat"],
        "signaling":  ["signal", "rescue", "ground-to-air", "ground to air", "morse",
                       "sos", "whistle", "signal mirror", "heliograph", "distress",
                       "flare"],
        "knots":      ["knot", "rope", "lashing", "cordage", "hitch", "bowline",
                       "paracord", "prusik", "bend "],
        # "tar" is matched only as phrases — bare "tar" lives inside "start",
        # "target", "mortar". Same care for "sap" ("sapling") and "pitch"
        # ("pitch a tent", "pitch dark").
        "bushcraft":  ["bushcraft", "primitive", "pine pitch", "pitch glue",
                       "resin", "sap glue", "tree sap", "birch tar", "pine tar",
                       "make tar", "make a tar", "tar from", "natural glue",
                       "natural adhesive", "adhesive", "glue", "sealant",
                       "seal stuff", "waterproof", "from nature", "with nothing",
                       "plant fiber", "natural fiber", "natural cordage",
                       "make cordage", "make rope", "make string", "dogbane",
                       "milkweed", "nettle", "yucca", "sinew", "rawhide",
                       "reverse wrap", "withies", "wattle", "daub", "travois",
                       "knapping", "knap ", "stone tool", "flint", "obsidian",
                       "stone knife", "burn bowl", "bark container", "bark basket",
                       "fire harden", "fire-harden", "digging stick", "hot rock",
                       "bone tool", "hide glue", "clay pot", "pottery",
                       "selfbow", "self bow", "make a bow", "bow making",
                       "bow wood", "bow stave", "tillering",
                       "bow and arrow", "make arrows", "arrow making", "fletch",
                       "atlatl", "spear thrower", "throwing stick", "rabbit stick",
                       "fishing spear", "gig ", "tanning", "tan a hide",
                       "brain tan", "bark tan", "buckskin", "deer hide",
                       "tallow", "render fat"],
        "hunting":    ["trap", "snare", "deadfall", "fishing", "trotline", "gill net",
                       "field dress", "field-dress", "gut ", "snaring", "game meat"],
        "sanitation": ["sanitation", "latrine", "outhouse", "sewage", "cholera",
                       "dysentery", "feces", "human waste", "hygiene", "typhoid"],
        "medications":["antibiotic", "amoxicillin", "doxycycline", "cephalexin",
                       "metronidazole", "ciprofloxacin", "cipro", "azithromycin",
                       "bactrim", "fish antibiotic", "dosage", "painkiller",
                       "ibuprofen", "acetaminophen", "medication", "expired med"],
        # "salve"/"herb" are word-boundary tokens ("salvage", "herbivore").
        # "medicinal benefit(s)" outweighs the medical category's "medic"
        # substring hit on "medicinal" so tar/herb questions route here.
        "herbal":     ["herbal", "herb", "tincture", "salve", "poultice",
                       "decoction", "infusion", "infused oil", "medicinal",
                       "medicinal benefit", "medicinal plant", "medicinal use",
                       "natural remed", "home remed", "folk remed", "plant medicine",
                       "willow bark", "oak bark", "bark tea", "bark decoction",
                       "yarrow", "plantain", "jewelweed",
                       "mullein", "usnea", "elderberry", "goldenrod",
                       "pine needle tea", "resin salve", "pine tar soap",
                       "honey dressing", "honey on a wound", "oxymel",
                       "identify a plant", "plant identification", "what plant",
                       "poisonous plant", "toxic plant", "poison plant",
                       "lookalike", "look-alike", "look alike",
                       # bare "hemlock"/"willow" here (not in trees) so
                       # "water hemlock…"/"willow bark…" queries score 2 and
                       # beat the water/trees categories' 1-hit ties
                       "hemlock", "willow",
                       "water hemlock", "poison hemlock", "pokeweed",
                       "death camas", "nightshade", "moonseed", "baneberry",
                       "poison ivy", "poison oak", "poison sumac", "stinging nettle",
                       # cold/flu symptom-relief teas (focused symptom doc)
                       "cold remedy", "cold and flu", "flu remedy", "sore throat",
                       "stuffy nose", "runny nose", "congestion", "decongestant",
                       "chest congestion", "sinus", "cough remedy", "tea for a cold",
                       "tea to help", "make a tea", "elderberry", "ginger tea",
                       "gargle", "steam inhalation", "chills and fever",
                       "feel sick", "feeling sick", "coming down with"],
        "psychology": ["psychology", "mental", "morale", "mindset", "panic",
                       "will to survive", "s.t.o.p", "stop method", "fear",
                       "stress", "resilience"],
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
        "grid_down":  ["grid", "collapse", "barter", "sustain", "bug-out", "go bag",
                       "get-home", "edc", "everyday carry", "survival kit",
                       "preparedness", "rule of three", "priorities"],
        "vehicles":   ["vehicle", "fuel", "maintenance"],
        "wildlife":   ["wildlife", "snake", "bear", "spider", "venomous",
                       "rabies", "alligator"],
        "trees":      ["tree", "native", "species"],
        "nuclear":    ["nuclear", "radiation", "fallout", "cbrn", "geiger",
                       "roentgen", "potassium iodide", "decontamination", "radiological"],
        "emp":        ["faraday", "electromagnetic", "solar storm", "geomagnetic",
                       "coronal mass", "carrington"],
        "weather":    ["weather", "tornado", "hurricane", "flood", "earthquake",
                       "wildfire", "blizzard", "storm", "barometr"],
        "cold_heat":  ["hypotherm", "frostbite", "heat stroke", "heat exhaustion",
                       "cold injury", "trench foot", "rewarm"],
        "signaling":  ["signal", "rescue", "ground-to-air", "morse", "distress",
                       "heliograph", "whistle"],
        "knots":      ["knot", "rope", "cordage", "lashing", "hitch", "bowline",
                       "paracord", "prusik"],
        "air_quality":["air quality", "aqi", "wildfire smoke", "smoke inhalation",
                       "pm2.5", "respirator", "n95", "hepa", "air purifier"],
        "bushcraft":  ["bushcraft", "primitive", "pitch glue", "birch tar",
                       "knapping", "natural fiber", "bark container", "selfbow",
                       "hide tanning", "buckskin"],
        "herbal":     ["herbal", "medicinal", "tincture", "salve", "poultice",
                       "natural remedies", "wild medicine"],
        "hunting":    ["trap", "snare", "deadfall", "fishing", "trotline", "gill net",
                       "field dress", "procurement"],
        "sanitation": ["sanitation", "latrine", "sewage", "cholera", "dysentery",
                       "human waste", "hygiene", "typhoid"],
        "medications":["antibiotic", "amoxicillin", "doxycycline", "cephalexin",
                       "metronidazole", "ciprofloxacin", "medication", "pharmacy"],
        "psychology": ["psychology", "mental", "morale", "mindset", "resilience",
                       "survival mindset", "will to survive"],
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
            hits = sum(1 for kw in keywords if _kw_hit(kw, q))
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
            # Embed on GPU (num_gpu omitted = auto-place). OLLAMA_MAX_LOADED_MODELS=2
            # keeps the chat model resident, so the embedder runs on GPU without
            # the per-message reload thrash that the old CPU pin guarded against.
            payload = json.dumps({"model": embed_model, "input": text}).encode()
            req = urllib.request.Request(
                f"{self.ollama_base}/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=60)
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
        """Return cached list of (doc_dict, chunks) pairs; refreshed every 120 s.

        chunks is a list of {'h':heading,'t':text,'e':vector}. A legacy flat
        whole-doc vector (a bare JSON list) is wrapped as a single chunk so a
        store that is mid-migration to the chunked v3 format keeps working.
        """
        import database as db
        now = time.time()
        if self._doc_emb_cache is not None and (now - self._doc_emb_cache_ts) < _DOC_EMB_CACHE_TTL:
            return self._doc_emb_cache
        docs = db.ai_get_documents_with_embeddings()
        result = []
        for doc in docs:
            raw = doc.get("embedding")
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except Exception:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("chunks"), list):
                chunks = [c for c in parsed["chunks"] if c.get("e")]
            elif isinstance(parsed, list) and parsed:
                chunks = [{"h": "", "t": doc.get("content") or "", "e": parsed}]
            else:
                continue
            if chunks:
                result.append((doc, chunks))
        self._doc_emb_cache = result
        self._doc_emb_cache_ts = now
        return result

    # ------------------------------------------------------------------
    def _is_math_query(self, user_message):
        """Return True if the user message appears to require computation.

        Fires on an explicit compute/conversion verb, a bare arithmetic
        operator / math-function token, or anything _is_physics_query already
        recognises (strong physics, ballistics, or a unit noun WITH compute
        intent). A bare quantity noun alone no longer qualifies, so survival
        questions that merely mention a distance/depth don't get the [CALC:]
        hint injected."""
        msg_lower = user_message.lower()
        if any(kw in msg_lower for kw in _COMPUTE_INTENT_KEYWORDS):
            return True
        if any(_kw_hit(kw, msg_lower) for kw in _MATH_OPERATOR_KEYWORDS):
            return True
        return self._is_physics_query(user_message)

    def _is_physics_query(self, user_message):
        """Return True if the query involves physics/ballistics — triggers the agent loop.

        Three gates, in order of confidence:
          1. Ballistic queries ALWAYS qualify (a keyword gap here must never
             starve the compute path — this exact drift is what let "spin drift
             of a 9mm…" fall through to the bare model).
          2. A STRONG physics keyword names a computation on its own.
          3. A WEAK keyword (bare unit / quantity noun) only qualifies alongside
             explicit compute intent, so an ordinary survival question that
             merely mentions a distance ("within 30 miles of a blast") does not
             drag in the calculator agent and its fabricated expressions.
        """
        msg_lower = user_message.lower()
        if self._is_ballistic_query(user_message):
            return True
        if any(_kw_hit(kw, msg_lower) for kw in _PHYSICS_STRONG_KEYWORDS):
            return True
        if any(_kw_hit(kw, msg_lower) for kw in _PHYSICS_WEAK_KEYWORDS):
            return any(kw in msg_lower for kw in _COMPUTE_INTENT_KEYWORDS)
        return False

    def _is_ballistic_query(self, user_message):
        """Return True if the query is specifically about bullet trajectory / ballistic drop."""
        msg_lower = user_message.lower()
        return any(_kw_hit(kw, msg_lower) for kw in _BALLISTIC_SPECIFIC_KEYWORDS)

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
    def _generate_dope_card(self, v0, bc, desc, m_gr, d_in, l_in,
                             twist_in, twist_src, zero_m=100.0):
        """Generate a full DOPE table for a given round across 100–1000 m."""
        _MOA_CM = 2.90888
        ranges_m = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
        v0_fps  = _mps_to_fps(v0)
        zero_yd = round(zero_m * 1.09361)
        sg      = _miller_sg(m_gr, d_in, l_in, twist_in, v0)

        rows = []
        for r in ranges_m:
            drop_cm, tof_s = _ballistic_sim(r, zero_m, v0, bc)
            moa_f    = r * _MOA_CM / 100.0
            mrad_f   = r * 10.0   / 100.0
            drop_moa  = round(abs(drop_cm) / moa_f,  1)
            drop_mrad = round(abs(drop_cm) / mrad_f, 2)
            sd_in = 1.25 * sg * tof_s ** 1.83
            sd_cm = round(sd_in * 2.54, 1)
            rows.append((r, drop_cm, drop_moa, drop_mrad, tof_s, sd_cm))

        sep = "─" * 66
        lines = [
            f"=== DOPE CARD: {desc} ===",
            f"MV: {v0} m/s ({v0_fps} fps)  BC(G1): {bc}  Zero: {zero_m:.0f} m ({zero_yd} yd)",
            f"Barrel: 1:{twist_in:.0f}\" RH [{twist_src}]  Sea level · std atmosphere.",
            "",
            f"{'Range':>6} | {'Drop cm':>7} | {'In':>6} | {'MOA↑':>5} | {'mrad':>5} | {'TOF(s)':>6} | {'Drift cm':>8}",
            sep,
        ]
        for r, drop_cm, drop_moa, drop_mrad, tof_s, sd_cm in rows:
            drop_in = round(abs(drop_cm) / 2.54, 1)
            dir_s   = "↓" if drop_cm < 0 else "↑"
            lines.append(
                f"{r:>5}m | {abs(drop_cm):>6.1f}{dir_s} | {drop_in:>5.1f}\" | {drop_moa:>5.1f} | {drop_mrad:>5.2f} | {tof_s:>6.3f} | {sd_cm:>7.1f}R"
            )
        lines += [
            sep,
            "MOA↑/mrad = elevation correction up from zero. Drift = gyroscopic spin drift RIGHT.",
            "Actual values vary with altitude, temperature, and lot-to-lot velocity spread.",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def _ballistic_resolve(self, user_message):
        """
        Parse a ballistic query into resolved parameters — the SINGLE source of
        truth shared by the LLM-context block and the deterministic user-facing
        answer.  Parsing duplicated across two formatters is exactly the kind of
        drift that caused earlier ballistics bugs.

        Returns a dict {is_dope, range_m, zero_m, v0, bc, desc, m_gr, d_in, l_in,
        twist_in, twist_src, round_identified}, or None if neither a target range
        nor a DOPE-card request could be parsed.
        """
        import re
        msg = user_message.lower()

        # ── DOPE card shortcut (no specific range needed) ───────────────────
        # Identified early so we don't bail on missing range.
        _is_dope = bool(re.search(r'\bdope\b', msg))

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
                vals = [v for v in vals if 1 <= v <= 5000]
                if vals:
                    range_m = max(vals)
                    break

        if range_m is None and not _is_dope:
            logger.debug("Ballistic resolve: could not parse range from message")
            return None

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

        # ── Clamp zero distance ─────────────────────────────────────────────
        # If the target is inside the zero distance (e.g. 60 ft shot with
        # default 100 m zero), the bullet would still be rising — use half
        # the range as a near-zero so the physics makes sense.
        if range_m is not None and zero_m >= range_m:
            zero_m = max(range_m * 0.5, 5.0)

        # ── Identify round ──────────────────────────────────────────────────
        round_data = self._identify_round(user_message)
        round_identified = round_data is not None
        if round_data:
            v0, bc, desc, m_gr, d_in, l_in, ref_twist = round_data
        else:
            v0, bc, desc = 975, 0.269, "5.56mm 55gr (assumed)"
            m_gr, d_in, l_in, ref_twist = 55, 0.224, 0.910, 7.0
            logger.debug("Ballistic resolve: round not identified, using 5.56 55gr defaults")

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
        twist_src = "user-specified" if twist_explicit else "standard barrel for this round"

        return {
            "is_dope": _is_dope, "range_m": range_m, "zero_m": zero_m,
            "v0": v0, "bc": bc, "desc": desc, "m_gr": m_gr, "d_in": d_in,
            "l_in": l_in, "twist_in": twist_in, "twist_src": twist_src,
            "round_identified": round_identified,
        }

    # ------------------------------------------------------------------
    def _ballistic_single_compute(self, p):
        """
        Compute drop + spin drift for a single range from resolved params `p`.
        ONE physics path feeds both the LLM-context block and the user answer.
        Returns a dict of every derived quantity (cm/in/ft + MOA/mrad).
        """
        # Angular unit constants — 1 MOA = 2.90888 cm / 100 m, 1 mrad = 10 cm / 100 m.
        _MOA_CM_PER_100M  = 2.90888
        _MRAD_CM_PER_100M = 10.0
        range_m, zero_m = p["range_m"], p["zero_m"]
        v0, bc = p["v0"], p["bc"]

        drop_cm, tof_s = _ballistic_sim(range_m, zero_m, v0, bc)

        # Angular conversion factors for this range — derived once, used for BOTH
        # drop and spin drift.  One source of truth eliminates unit drift.
        moa_per_cm  = 1.0 / (range_m * _MOA_CM_PER_100M  / 100.0)
        mrad_per_cm = 1.0 / (range_m * _MRAD_CM_PER_100M / 100.0)

        drop_abs_cm = abs(drop_cm)
        # Spin drift — Litz: SD_in = 1.25 × Sg × TOF^1.83
        sg    = _miller_sg(p["m_gr"], p["d_in"], p["l_in"], p["twist_in"], v0)
        sd_in = round(1.25 * sg * tof_s ** 1.83, 2)
        sd_cm = round(sd_in * 2.54, 1)

        return {
            "drop_cm": drop_cm, "drop_abs_cm": drop_abs_cm, "tof_s": tof_s,
            "drop_in":   round(drop_abs_cm / 2.54,  2),
            "drop_ft":   round(drop_abs_cm / 30.48, 2),
            "drop_moa":  round(drop_abs_cm * moa_per_cm,  1),
            "drop_mrad": round(drop_abs_cm * mrad_per_cm, 2),
            "direction": "below" if drop_cm < 0 else "above",
            "sg": sg, "sd_in": sd_in, "sd_cm": sd_cm,
            "sd_moa":  round(sd_cm * moa_per_cm,  2),
            "sd_mrad": round(sd_cm * mrad_per_cm, 3),
            "moa_per_cm": moa_per_cm, "mrad_per_cm": mrad_per_cm,
        }

    # Unambiguous ballistic phrases — collision-free enough to act on even when
    # the round or range is missing.  A bare "spin"/"drift" is NOT here (it shows
    # up in "spin up a server"), so those stay on the LLM fallback path.
    _BALLISTIC_STRONG_PHRASES = (
        "spin drift", "gyroscopic", "bullet drop", "holdover", "come-up",
        "scope dial", "zeroed", " moa", " mrad", "windage hold", "elevation hold",
        "dope card", "dope table",
    )

    def _ballistic_clarify(self, has_round, has_range):
        """Deterministic prompt asking for the inputs a ballistic solution needs.
        Keeps the workflow predictable: a confidently-ballistic query never reaches
        the 2b model to be answered (or hallucinated) without its key parameters."""
        need = []
        if not has_round:
            need.append("the **round / caliber** — e.g. `.308 168gr`, `5.56 55gr`, `9mm 115gr`")
        if not has_range:
            need.append("the **target range** — e.g. `300 m` or `500 yd`")
        bullets = "\n".join(f"- {n}" for n in need)
        return (
            "I can compute a full ballistic solution (drop, spin drift, MOA/mrad, "
            "time of flight) straight from verified physics — I just need:\n"
            f"{bullets}\n\n"
            "Optional: zero distance (default 100 m) and barrel twist (e.g. `1:8\"`).\n"
            "Example: *\"spin drift of .308 168gr at 600 yd zeroed 100\"*"
        )

    # ------------------------------------------------------------------
    def _ballistic_user_answer(self, user_message):
        """
        Deterministic, user-facing ballistic answer — the verified physics IS the
        answer, not context for the chat model to narrate.  The small model
        mis-states these numbers (see the ballistics commit history), so we bypass
        it for every confidently-ballistic query: a full solution when the round +
        range are known, otherwise a deterministic prompt for the missing inputs.

        Returns markdown, or None to fall back to the LLM only when the query is
        NOT confidently ballistic (a stray "spin"/"drift" with no round and no
        strong phrase — e.g. "spin up a server at 5 m").
        """
        p = self._ballistic_resolve(user_message)

        # Is this *confidently* ballistic?  A named round, or an unambiguous
        # ballistic phrase.  Anything weaker falls through to the normal pipeline.
        msg = user_message.lower()
        has_round = self._identify_round(user_message) is not None
        confident = has_round or any(s in msg for s in self._BALLISTIC_STRONG_PHRASES)

        if p is None:
            # No target range and not a DOPE request.
            if confident:
                return self._ballistic_clarify(has_round, has_range=False)
            return None

        if not p["round_identified"]:
            # Range (or DOPE) present but no specific round.  Range is already
            # satisfied here (parsed, or not needed for a DOPE card), so we only
            # need the round — ask rather than silently assume 5.56.
            if confident:
                return self._ballistic_clarify(has_round=False, has_range=True)
            return None

        # ── DOPE card — return the table verbatim in a code block ───────────
        if p["is_dope"]:
            try:
                card = self._generate_dope_card(
                    p["v0"], p["bc"], p["desc"], p["m_gr"], p["d_in"],
                    p["l_in"], p["twist_in"], p["twist_src"], p["zero_m"]
                )
                return "```\n" + card + "\n```"
            except Exception as ex:
                logger.warning(f"DOPE card (user answer) failed: {ex}")
                return None

        # ── Single-range solution ───────────────────────────────────────────
        try:
            c = self._ballistic_single_compute(p)
        except Exception as ex:
            logger.warning(f"Ballistic user answer failed: {ex}")
            return None

        range_m, zero_m = p["range_m"], p["zero_m"]
        range_yd = round(range_m * 1.09361)
        zero_yd  = round(zero_m  * 1.09361)
        v0_fps   = _mps_to_fps(p["v0"])
        twist_in = p["twist_in"]

        # Spin drift is sub-tenth-inch at pistol/short range — say so plainly
        # rather than presenting a meaningless 0.02" figure as actionable.
        sd_note = "  _(negligible at this range)_" if c["sd_in"] < 0.25 else ""

        lines = [
            f"**Ballistic solution — {p['desc']}**",
            "",
            f"- Muzzle velocity **{p['v0']} m/s** ({v0_fps} fps) · BC (G1) {p['bc']}",
            f"- Zero **{zero_m:.0f} m** ({zero_yd} yd) · Target **{range_m:.0f} m** ({range_yd} yd)",
            f"- Barrel twist 1:{twist_in:.0f}\" RH [{p['twist_src']}] · Miller Sg {c['sg']:.2f}",
            "",
            f"**Bullet drop:** {c['drop_abs_cm']} cm / {c['drop_in']} in {c['direction']} line of "
            f"sight  ({c['drop_moa']} MOA · {c['drop_mrad']} mrad)",
            f"**Time of flight:** {c['tof_s']} s",
            f"**Spin drift:** {c['sd_in']} in / {c['sd_cm']} cm to the RIGHT  "
            f"({c['sd_moa']} MOA · {c['sd_mrad']} mrad){sd_note}",
            "",
            "_G1 drag model · sea level · standard atmosphere. RH twist drifts right — "
            "specify your barrel twist if it differs._",
        ]
        logger.info(
            f"Ballistic user answer: {p['desc']} {range_m:.0f}m zero={zero_m:.0f}m "
            f"drop={c['drop_cm']}cm tof={c['tof_s']}s sg={c['sg']:.2f} sd={c['sd_in']}in"
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def _ballistic_direct_compute(self, user_message):
        """
        LLM-context block of pre-computed ballistic results, injected only on the
        fallback path (e.g. round not identified, so _ballistic_user_answer
        declined).  No LLM involvement in the maths; always both metric and
        imperial so there is no unit ambiguity.
        """
        p = self._ballistic_resolve(user_message)
        if p is None:
            return ""

        # ── DOPE card request — generate full multi-range table ─────────────
        if p["is_dope"]:
            try:
                return self._generate_dope_card(
                    p["v0"], p["bc"], p["desc"], p["m_gr"], p["d_in"],
                    p["l_in"], p["twist_in"], p["twist_src"], p["zero_m"]
                )
            except Exception as ex:
                logger.warning(f"DOPE card generation failed: {ex}")
                return ""

        # ── Single-range physics ─────────────────────────────────────────────
        try:
            c = self._ballistic_single_compute(p)
            v0, bc, desc       = p["v0"], p["bc"], p["desc"]
            range_m, zero_m    = p["range_m"], p["zero_m"]
            d_in, l_in         = p["d_in"], p["l_in"]
            twist_in, twist_src = p["twist_in"], p["twist_src"]
            drop_cm = c["drop_cm"]
            drop_abs_cm, drop_in, drop_ft = c["drop_abs_cm"], c["drop_in"], c["drop_ft"]
            drop_moa, drop_mrad, direction = c["drop_moa"], c["drop_mrad"], c["direction"]
            tof_s, sg = c["tof_s"], c["sg"]
            sd_in, sd_cm, sd_moa, sd_mrad = c["sd_in"], c["sd_cm"], c["sd_moa"], c["sd_mrad"]
            moa_per_cm = c["moa_per_cm"]

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
    def rag_search(self, query, top_k=5, embed_model=None, location_hint=None):
        """Return (docs, top_score) using passage-level BM25-boosted cosine similarity.

        docs      — list of matching doc dicts (up to top_k, hybrid score >= _RAG_MIN_SCORE).
                    Each carries '_passage'/'_heading': the single best-matching
                    section, so the caller injects that paragraph rather than the
                    whole multi-topic doc.
        top_score — raw cosine similarity of the best retrieved passage (0.0 if none)

        A doc is scored by its best chunk: max cosine over that doc's passages.
        location_hint (e.g. 'Herndon, Virginia, Mid-Atlantic US') is appended to
        the embedding query ONLY for location-scoped questions ('near me', 'here',
        'my area'). Appending it to an ordinary question (e.g. 'how to clean water')
        polluted the query vector with geography and dropped the correct passage's
        cosine by ~0.10-0.12 — enough to flip HIGH→LOW and even push good docs below
        _RAG_MIN_SCORE so they were dropped from context. Regional ranking for those
        queries is instead handled by the structured location_terms boost below
        (+0.08 passage nudge, x1.15 region boost), which needs no embed pollution.

        Hybrid score = max(v, 0.6*v + 0.4*bm25_norm) when v >= _BM25_GATE, else v.
        BM25 can only boost semantically plausible candidates — never surface an
        unrelated doc on keyword coincidence (e.g. "temperature" in an AI-params doc
        matching a cooking question when the vector similarity is near zero).
        """
        doc_embs = self._get_doc_embeddings()
        if not doc_embs:
            return [], 0.0
        # Only fold the location string into the embedded query when the user is
        # explicitly asking about their surroundings; otherwise it corrupts the
        # semantic match for topic questions (see docstring). Location-aware
        # ranking for all queries still happens via location_terms further down.
        embed_query = query
        if location_hint and _is_location_scoped_query(query):
            embed_query = f"{query} (location: {location_hint})"
        try:
            query_emb = self.get_embed(embed_query, embed_model=embed_model, is_query=True)
        except Exception as e:
            logger.warning(f"RAG embed failed: {e}")
            return [], 0.0
        qdim = len(query_emb)

        # Location tokens (e.g. ['herndon','virginia','mid-atlantic']) used to
        # prefer passages that actually name the user's state/region so "near me"
        # surfaces local species over generically-dangerous far-away ones.
        location_terms = []
        if location_hint:
            for part in location_hint.replace(" US", "").split(","):
                p = part.strip().lower()
                if p:
                    location_terms.append(p)

        # Score every doc by its best-matching passage. Chunk vectors whose
        # dimension doesn't match the query (e.g. left over from a previous embed
        # model) are skipped rather than crashing numpy.dot / scoring as garbage.
        vec_scores = {}   # doc_id -> raw cosine of the selected (region-nudged) passage
        max_cos    = {}   # doc_id -> max raw cosine over the doc's passages
        best_chunk = {}   # doc_id -> (heading, passage_text)
        doc_chunks = {}   # doc_id -> [(cosine, heading, passage), ...] sorted desc
        doc_by_id  = {}
        dim_skips = 0
        for doc, chunks in doc_embs:
            best_rank, best_v, best_c, doc_max = -2.0, -1.0, None, -1.0
            scored_chunks = []
            for c in chunks:
                e = c.get("e")
                if not e or len(e) != qdim:
                    dim_skips += 1
                    continue
                v = cosine_similarity(query_emb, e)
                doc_max = max(doc_max, v)
                scored_chunks.append((v, c.get("h", ""), c.get("t", "")))
                # Region-scoped queries nudge passage SELECTION toward the section
                # that names the user's region (+0.08; enough to beat a generic
                # passage like "universal planting principles" that otherwise edges
                # out the regional one). This only steers which passage we inject —
                # the confidence footer uses max_cos (the doc's strongest raw match)
                # so region re-ranking never inflates *or* deflates the score.
                rank_v = v
                if location_terms and _names_location(c.get("t"), location_terms):
                    rank_v = v + 0.08
                if rank_v > best_rank:
                    best_rank, best_v, best_c = rank_v, v, c
            if best_c is None:
                continue
            vec_scores[doc["id"]] = best_v
            max_cos[doc["id"]]    = doc_max
            best_chunk[doc["id"]] = (best_c.get("h", ""), best_c.get("t", ""))
            scored_chunks.sort(key=lambda x: x[0], reverse=True)
            doc_chunks[doc["id"]] = scored_chunks
            doc_by_id[doc["id"]]  = doc
        if dim_skips:
            logger.warning(
                f"RAG: skipped {dim_skips} chunk vector(s) with dim != {qdim} "
                f"(stale embed model?) — re-embed needed."
            )
        if not vec_scores:
            return [], 0.0

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

        scored.sort(key=lambda x: x[0], reverse=True)

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

        # Region boost: for location-scoped queries, lift docs whose matched
        # passage names the user's state/region (+15%) so local species outrank
        # generically-dangerous far-away ones (e.g. VA copperhead over AZ Gila monster).
        if location_terms:
            def _passage_in_region(doc):
                txt = best_chunk.get(doc["id"], ("", ""))[1]
                return _names_location(txt, location_terms)
            scored = [(h * 1.15 if _passage_in_region(doc) else h, v, doc) for h, v, doc in scored]
            scored.sort(key=lambda x: x[0], reverse=True)

        # Filter by threshold and attach the matched passage(s) to each surviving
        # doc so build_context injects just the relevant sections, not the whole doc.
        out = []
        for h, v, doc in scored:
            if h < _RAG_MIN_SCORE:
                continue
            heading, passage = best_chunk.get(doc["id"], ("", ""))
            d = dict(doc)
            d["_passage"] = passage or doc.get("content") or ""
            d["_heading"] = heading
            d["_score"]  = round(h, 3)   # final hybrid+boost score used for ranking
            d["_cosine"] = round(v, 3)   # raw semantic similarity (un-inflated)
            # Sibling passages: a broad "how do I X" query often needs every method
            # in a single-topic doc, not just the highest-cosine one (a 0.003 tie
            # between QUINZHEE and LEAN-TO must not silently drop one). Carry the
            # doc's other chunks within _SIBLING_MARGIN of its best, selected one
            # first, capped at _MAX_PASSAGES_PER_DOC. Most docs add nothing here
            # because only one chunk is in-band.
            sel_h = (heading, passage)
            siblings = [sel_h]
            doc_best = max_cos.get(doc["id"], v)
            for cv, ch, ct in doc_chunks.get(doc["id"], []):
                if (ch, ct) == sel_h:
                    continue
                if cv >= doc_best - _SIBLING_MARGIN and cv >= _RAG_MIN_SCORE:
                    siblings.append((ch, ct))
                if len(siblings) >= _MAX_PASSAGES_PER_DOC:
                    break
            d["_passages"] = [{"heading": sh, "passage": sp} for sh, sp in siblings if sp]
            out.append(d)
            if len(out) >= top_k:
                break

        # Confidence footer = the STRONGEST raw cosine among the passages actually
        # injected — not the hybrid-#1 doc's cosine. BM25/category/region boosts can
        # rank a weaker-cosine doc first (e.g. "Solar Still" hybrid 0.76 outranks the
        # better "Water Purification" passage at cos 0.65 for "how to purify water"),
        # yet the stronger passage is still injected as a source. Confidence must
        # reflect the best evidence on hand, so take the max over injected docs.
        top_score = max((max_cos.get(d["id"], d.get("_cosine", 0.0)) for d in out),
                        default=0.0)
        return out, top_score

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
            "rag_docs": [],
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

        # Current date & season — always injected so time-sensitive advice (shelter
        # type, clothing, planting, foraging, hunting seasons, expected weather) is
        # grounded in the real date. Without it the model had no sense of time and
        # suggested a QUINZHEE snow shelter in June.
        import datetime as _dt
        _now = _dt.datetime.now()
        _season = _season_for(_now.month)
        parts.append(
            "=== CURRENT DATE ===\n"
            f"{_now.strftime('%A, %B %d, %Y')} — season: {_season} (Northern Hemisphere).\n"
            f"IMPORTANT: Tailor every season-dependent answer to {_season}. Lead with the "
            f"method that fits {_season}. Do NOT recommend or lead with cold/winter-only "
            "techniques (snow shelters, quinzhee, ice fishing) unless the user explicitly "
            "asks about winter or snow.\n"
            "=== END DATE ==="
        )
        meta["season"] = _season

        # Live "senses" are retrieved on demand, not always-on.  An unrelated
        # survival/ballistics/general question is no longer flooded with host
        # telemetry + the full mesh state (which the small model otherwise tries
        # to narrate and hallucinates around).  GPS grounding stays always-on.
        msg_lower   = user_message.lower()
        wants_stats = any(_kw_hit(k, msg_lower) for k in _SYSTEM_STATS_KEYWORDS)
        wants_mesh  = any(_kw_hit(k, msg_lower) for k in _MESH_CONTEXT_KEYWORDS)

        # System stats context — only when the message asks about host telemetry
        try:
            s = system_stats.get_stats() if wants_stats else None
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
        # Injected only when the message actually asks about the mesh/alerts — the
        # inject_mesh_context setting is the master switch, wants_mesh is the per-query gate.
        if settings.get("inject_mesh_context", "true").lower() == "true" and wants_mesh:
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
                want_pos = any(_kw_hit(w, msg_lower) for w in ("location","position","map","gps","lat","lon","coord","where"))
                want_topo = any(_kw_hit(w, msg_lower) for w in ("topology","link","snr","rssi","signal","hop","route","path","neighbor"))
                want_tel = any(_kw_hit(w, msg_lower) for w in ("telemetry","battery","voltage","humidity","pressure","temp","sensor"))
                want_msg = any(_kw_hit(w, msg_lower) for w in ("message","chat","said","text","sent","received","broadcast"))

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
        is_live_query = any(_kw_hit(kw, msg_lower) for kw in _LIVE_DATA_KEYWORDS)
        meta["is_live_query"] = is_live_query
        if settings.get("rag_enabled", "true").lower() == "true" and not is_live_query:
            try:
                top_k = int(settings.get("rag_top_k", DEFAULT_SETTINGS["rag_top_k"]))
                embed_model = settings.get("embed_model", DEFAULT_SETTINGS["embed_model"])
                # Location-sensitive queries get the user's resolved region
                # appended to the embedding query so the regional passage outranks
                # far-away ones. This fires for two cases:
                #   1. Deictic phrasing ("dangerous animals near me") → resolve via GPS.
                #   2. An explicitly NAMED place in the query ("best tree to plant in
                #      Virginia") → meta['user_location'] is already set by the location
                #      agent above, so resolve it to 'State, Region US'. Without this the
                #      query named a state but no region passage got boosted, and a
                #      generic passage (e.g. "universal planting principles") won over the
                #      regional "Eastern US → White Oak" one.
                location_hint = None
                if meta.get("user_location") or any(k in msg_lower for k in _LOCATION_INTENT_KEYS):
                    location_hint = _resolve_location_hint(meta.get("user_location"), meta.get("gps_fix"))
                # Fall back to any US state named in the query that the capitalized
                # _NAMED_LOC_RE missed — lowercase full names ("in virginia") and
                # uppercase abbreviations ("in VA") — so they're region-aware too.
                if not location_hint:
                    st = _state_from_query(user_message)
                    if st:
                        region = _region_for_state(st)
                        location_hint = f"{st}, {region} US" if region else st
                relevant_docs, rag_top_score = self.rag_search(
                    user_message, top_k=top_k, embed_model=embed_model, location_hint=location_hint
                )
                meta["rag_top_score"] = rag_top_score
                # Record retrieved passages for the /explain provenance trace so
                # the user can read the exact sources Ray drew on and check the
                # answer against them.
                meta["rag_docs"] = [
                    {
                        "title": d.get("title", ""),
                        "heading": d.get("_heading", ""),
                        "score": d.get("_score", 0.0),
                        "cosine": d.get("_cosine", 0.0),
                        "passage": d.get("_passage", ""),
                    }
                    for d in relevant_docs
                ]
                if relevant_docs:
                    meta["has_rag"] = True
                    # Inject the matched passage(s) per doc (not the whole multi-topic
                    # doc) and cap the total so the KNOWLEDGE BASE block can't overflow
                    # num_ctx and truncate the lowest-ranked matches. A doc can carry
                    # several sibling passages (_passages) so a broad query sees every
                    # method (lean-to AND quinzhee), not just the top-cosine one.
                    rag_lines, used = [], 0
                    for doc in relevant_docs:
                        passages = doc.get("_passages") or [
                            {"heading": doc.get("_heading") or "",
                             "passage": doc.get("_passage") or doc.get("content") or ""}
                        ]
                        # Sink winter-only passages below in-season ones (stable) so a
                        # broad query in summer leads with the 3-season method, not the
                        # snow one that edged it out on cosine.
                        if _season and len(passages) > 1:
                            passages = sorted(
                                passages,
                                key=lambda p: _passage_off_season(
                                    f"{p.get('heading','')} {p.get('passage','')}", _season),
                            )
                        for p in passages:
                            passage = p.get("passage") or ""
                            if not passage:
                                continue
                            heading = p.get("heading") or ""
                            label = doc["title"] + (f" — {heading}" if heading else "")
                            block = f"--- {label} ---\n{passage}"
                            if rag_lines and used + len(block) > _RAG_CONTEXT_CHAR_BUDGET:
                                break
                            rag_lines.append(block)
                            used += len(block)
                        if rag_lines and used > _RAG_CONTEXT_CHAR_BUDGET:
                            break
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

        Confidence tiers (calibrated to qwen3-embedding:0.6b against the LIVE
        knowledge base, not synthetic passages). Measured best-passage cosines for
        correct answers from real section-sized chunks: snakebite 0.74, water-purify
        0.63, best-tree 0.61, deer-caliber 0.56 — i.e. correct answers cluster
        0.55-0.74. The old 0.60 HIGH bar sat in the MIDDLE of that cluster, so the
        lower half of perfectly-correct answers were mislabelled MEDIUM. The bar is
        now 0.55, below the cluster; wrong/loose passages sit at 0.44-0.48, leaving a
        safe gap:
          HIGH   — strong RAG match (score ≥ 0.55) or live data present
          MEDIUM — moderate RAG match (0.47 ≤ score < 0.55) or system stats only
          LOW    — weak RAG match (below 0.47 but above threshold) or training only
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
            if score >= 0.55:
                tier = "HIGH"
            elif score >= 0.47:
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
    # /explain — deterministic provenance cross-check
    # ------------------------------------------------------------------
    def _gps_summary(self, meta):
        """One-line human description of the position context that was injected."""
        user_loc = meta.get("user_location")
        gps_fix  = meta.get("gps_fix")
        if user_loc:
            if user_loc.get("type") == "named":
                return f"user-stated location: {user_loc['name']}"
            if user_loc.get("type") == "coords":
                return f"user-stated coords: {user_loc['lat']:.4f}, {user_loc['lon']:.4f}"
        if gps_fix and gps_fix.get("latitude") is not None:
            lat, lon = gps_fix["latitude"], gps_fix["longitude"]
            place = _reverse_geocode(lat, lon)
            tail = f" ({place})" if place else ""
            return f"device GPS fix: {lat:.4f}, {lon:.4f}{tail}"
        return None

    def _build_explain_trace(self, *, user_message, path, ctx_meta, settings,
                             model, confidence_label, calc_block=None,
                             ballistic_answer=None):
        """Assemble the structured provenance trace for one answer.

        Everything here is recorded straight from the pipeline's own decisions —
        which path ran, what RAG retrieved at what score, what live context was
        injected, the generation settings — so /explain reflects what actually
        produced the answer rather than the model's after-the-fact narration.
        """
        meta = ctx_meta or {}
        path_labels = {
            "ballistic":    "Deterministic ballistic calculator (language model bypassed)",
            "physics_calc": "Physics calculator agent + language model",
            "live":         "Live device data (GPS / mesh / system stats)",
            "rag":          "Knowledge-base retrieval (RAG) + language model",
            "training":     "Language-model training knowledge only (no source retrieved)",
        }
        trace = {
            "version": 1,
            "question": user_message,
            "path": path,
            "path_label": path_labels.get(path, path),
            "category": self._classify_query_category(user_message),
            "model": model,
            "params": {
                "temperature":    settings.get("temperature", DEFAULT_SETTINGS["temperature"]),
                "top_p":          settings.get("top_p", DEFAULT_SETTINGS["top_p"]),
                "top_k":          settings.get("top_k", DEFAULT_SETTINGS["top_k"]),
                "repeat_penalty": settings.get("repeat_penalty", DEFAULT_SETTINGS["repeat_penalty"]),
                "num_ctx":        settings.get("num_ctx", DEFAULT_SETTINGS["num_ctx"]),
            },
            "confidence": confidence_label,
            "rag": {
                "enabled": str(settings.get("rag_enabled", "true")).lower() == "true",
                "skipped_live": bool(meta.get("is_live_query")),
                "min_score": _RAG_MIN_SCORE,
                "top_score": round(meta.get("rag_top_score", 0.0), 3),
                "docs": meta.get("rag_docs", []),
            },
            "live": {
                "gps": self._gps_summary(meta),
                "system_stats": bool(meta.get("has_system_stats")),
                "mesh": bool(meta.get("has_live_data")),
                "self_doc": bool(meta.get("has_self_doc")),
            },
            "calc": calc_block or None,
        }
        if ballistic_answer is not None:
            # The deterministic answer IS its own evidence — keep it verbatim.
            trace["ballistic_answer"] = ballistic_answer
        return trace

    def _format_explain_trace(self, trace):
        """Render a stored trace dict into the markdown shown for /explain."""
        if not trace:
            return (
                "🔍 **/explain** — I don't have a recorded answer to explain yet.\n\n"
                "Ask me something first, then send `/explain` to see exactly how and "
                "why I produced that answer."
            )
        path = trace.get("path")
        L = []
        L.append("🔍 **How I produced that answer — provenance cross-check**")
        L.append(
            "_This breakdown is generated directly from my pipeline's records, "
            "not written by the language model — so you can verify the answer "
            "against its actual sources rather than trusting a self-report._")
        q = (trace.get("question") or "").strip()
        if q:
            L.append(f"**Your question:** {q}")
        L.append(f"**Answer path:** {trace.get('path_label', path)}")
        if trace.get("category"):
            L.append(f"**Topic router matched:** `{trace['category']}`")
        if trace.get("confidence"):
            L.append(f"**Confidence I reported:** {trace['confidence']}")

        # ── Ballistic / deterministic ──────────────────────────────────────
        if path == "ballistic":
            L.append(
                "**Why you can trust the numbers:** this query named a specific "
                "round, so the figures were computed by the verified G1 ballistic "
                "calculator (real trajectory sim + Miller spin-drift formula). "
                "The language model was bypassed entirely and never touched the "
                "numbers, so it could not hallucinate them.")
            L.append(
                "**Cross-check:** re-run the inputs (round, range, zero, twist) in "
                "any external ballistic solver — the drop, time-of-flight and spin "
                "drift should match within rounding.")
            return "\n\n".join(L)

        # ── RAG sources ────────────────────────────────────────────────────
        rag = trace.get("rag", {})
        docs = rag.get("docs", [])
        if rag.get("skipped_live"):
            L.append(
                "**Knowledge base:** skipped — this was classified as a live-data "
                "question, so I answered from current device readings instead of "
                "retrieved documents.")
        elif not rag.get("enabled"):
            L.append("**Knowledge base:** retrieval is turned OFF in AI settings.")
        elif docs:
            lines = [f"**Sources retrieved from knowledge base** "
                     f"(passages scoring ≥ {rag.get('min_score')} were used):"]
            for i, d in enumerate(docs, 1):
                title = d.get("title", "Untitled")
                heading = d.get("heading", "")
                label = f"{title}" + (f" — {heading}" if heading else "")
                lines.append(
                    f"{i}. **{label}** · score {d.get('score')} "
                    f"(raw similarity {d.get('cosine')})")
                passage = (d.get("passage") or "").strip().replace("\n", " ")
                if len(passage) > 320:
                    passage = passage[:320].rstrip() + "…"
                if passage:
                    lines.append(f"   > {passage}")
            L.append("\n".join(lines))
        else:
            L.append(
                f"**Knowledge base:** no passage cleared the relevance threshold "
                f"of {rag.get('min_score')} (best match scored "
                f"{rag.get('top_score')}). I answered from the model's training "
                f"knowledge — treat specifics as **unverified** and double-check "
                f"anything safety-critical.")

        # ── Live context ───────────────────────────────────────────────────
        live = trace.get("live", {})
        live_bits = []
        if live.get("gps"):
            live_bits.append(f"position — {live['gps']}")
        if live.get("system_stats"):
            live_bits.append("live system stats (CPU/RAM/temps/power)")
        if live.get("mesh"):
            live_bits.append("live mesh node / alert data")
        if live.get("self_doc"):
            live_bits.append("my self-architecture document")
        if live_bits:
            L.append("**Live context injected:**\n" +
                     "\n".join(f"- {b}" for b in live_bits))

        # ── Calculator agent ───────────────────────────────────────────────
        if trace.get("calc"):
            calc = trace["calc"].strip()
            if len(calc) > 600:
                calc = calc[:600].rstrip() + "\n…"
            L.append("**Pre-verified calculator results fed to the model:**\n```\n"
                     + calc + "\n```")

        # ── Generation settings ────────────────────────────────────────────
        p = trace.get("params", {})
        L.append(
            f"**Generation settings:** {trace.get('model')} · "
            f"temperature {p.get('temperature')} · top_p {p.get('top_p')} · "
            f"top_k {p.get('top_k')} · repeat_penalty {p.get('repeat_penalty')} · "
            f"context {p.get('num_ctx')} tokens. A non-zero temperature means "
            "wording varies between runs even with identical sources.")

        # ── Cross-check guidance ───────────────────────────────────────────
        tips = [
            "Compare each claim in my answer against the quoted source passages "
            "above — anything not backed by a passage came from model training.",
        ]
        if path == "training" or not docs:
            tips.append(
                "Confidence is **LOW / training-only** here: there is no offline "
                "source behind this answer, so verify it independently before "
                "relying on it.")
        L.append("**How to catch a hallucination:**\n" +
                 "\n".join(f"- {t}" for t in tips))
        return "\n\n".join(L)

    def _explain_trace_json(self, user_message, ctx_meta, settings, model,
                            content, calc_block):
        """Derive path + confidence label from a finished answer and return the
        provenance trace as a JSON string (for the normal LLM pipeline)."""
        meta = ctx_meta or {}
        if calc_block:
            path = "physics_calc"
        elif meta.get("is_live_query") or meta.get("has_live_data") or meta.get("has_system_stats"):
            path = "live"
        elif meta.get("has_rag"):
            path = "rag"
        else:
            path = "training"
        m = re.search(r"\n\n---\nConfidence:\s*(.+)$", content or "", re.S)
        conf_label = m.group(1).strip() if m else None
        try:
            return json.dumps(self._build_explain_trace(
                user_message=user_message, path=path, ctx_meta=meta,
                settings=settings, model=model, confidence_label=conf_label,
                calc_block=calc_block))
        except Exception as e:
            logger.warning(f"explain trace build failed: {e}")
            return None

    def _explain_response(self, chat_id, parsed):
        """Build the /explain reply for a chat. Returns the markdown string."""
        import database as db
        target = db.ai_get_explainable_message(
            chat_id, message_id=parsed.get("message_id"))
        if not target:
            return self._format_explain_trace(None)
        try:
            trace = json.loads(target.get("explain") or "null")
        except Exception:
            trace = None
        return self._format_explain_trace(trace)

    # ------------------------------------------------------------------
    def chat(self, chat_id, user_message):
        """Full pipeline: build context, call Ollama, store messages, return dict."""
        import database as db
        self._wait_for_startup_warmup()
        settings = db.ai_get_settings()  # fetch once; passed downstream
        model = settings.get("model", DEFAULT_SETTINGS["model"])
        keep_alive = f"{settings.get('keep_alive_hours', DEFAULT_SETTINGS['keep_alive_hours'])}h"
        base_system = settings.get("system_prompt", DEFAULT_SETTINGS["system_prompt"])

        # ── /explain cross-check ─────────────────────────────────────────────
        # Deterministically describe how the *previous* answer was produced, from
        # the recorded provenance trace. No LLM call — the explanation can't
        # itself hallucinate. Stored with explain=NULL so it's skipped next time.
        explain_cmd = _parse_explain_command(user_message)
        if explain_cmd is not None:
            answer = self._explain_response(chat_id, explain_cmd)
            db.ai_add_message(chat_id, "user", user_message)
            db.ai_add_message(chat_id, "assistant", answer,
                              tokens=None, duration_ms=0)
            return {
                "content": answer, "model": model,
                "eval_count": None, "eval_duration": None,
                "duration_ms": 0, "tok_per_sec": None,
            }

        # ── Deterministic ballistic short-circuit ───────────────────────────
        # A ballistic query naming a specific round is answered straight from
        # verified G1 physics — the chat model is bypassed entirely because it
        # narrates/hallucinates these numbers (242 ft of spin drift for a 9mm…).
        # Falls through to the normal pipeline when no round was identified.
        if self._is_ballistic_query(user_message):
            ballistic_answer = self._ballistic_user_answer(user_message)
            if ballistic_answer:
                ballistic_answer += (
                    "\n\n---\nConfidence: HIGH | "
                    "Source: Ballistic Calculator (verified G1 physics)"
                )
                _bx = json.dumps(self._build_explain_trace(
                    user_message=user_message, path="ballistic", ctx_meta={},
                    settings=settings, model=model,
                    confidence_label="HIGH | Source: Ballistic Calculator (verified G1 physics)",
                    ballistic_answer=ballistic_answer))
                db.ai_add_message(chat_id, "user", user_message)
                db.ai_add_message(chat_id, "assistant", ballistic_answer,
                                  tokens=None, duration_ms=0, explain=_bx)
                chat = db.ai_get_chat(chat_id)
                if chat and chat.get("title") == "New Chat":
                    short_title = user_message[:40].strip()
                    if len(user_message) > 40:
                        short_title += "…"
                    db.ai_update_chat_title(chat_id, short_title)
                return {
                    "content": ballistic_answer, "model": model,
                    "eval_count": None, "eval_duration": None,
                    "duration_ms": 0, "tok_per_sec": None,
                }

        # Build augmented system prompt (settings already loaded — no extra DB call)
        context, ctx_meta = self.build_context(user_message, settings=settings)

        # Agent loop: for physics/ballistics queries, run the two-pass calculator agent
        # before the final answer so the model is given pre-verified numbers.
        explain_calc_block = None
        if self._is_physics_query(user_message):
            calc_block, had_results = self._calc_agent_pass(user_message, settings)
            if had_results:
                explain_calc_block = calc_block
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
                "repeat_penalty": float(settings.get("repeat_penalty", DEFAULT_SETTINGS["repeat_penalty"])),
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

        # Save assistant message + its /explain provenance trace
        explain_json = self._explain_trace_json(
            user_message, ctx_meta, settings, model, content, explain_calc_block)
        db.ai_add_message(chat_id, "assistant", content, tokens=tokens,
                          duration_ms=duration_ms, explain=explain_json)

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

        # ── /explain cross-check (see chat() for rationale) ──────────────────
        explain_cmd = _parse_explain_command(user_message)
        if explain_cmd is not None:
            answer = self._explain_response(chat_id, explain_cmd)
            db.ai_add_message(chat_id, "user", user_message)
            yield answer
            db.ai_add_message(chat_id, "assistant", answer,
                              tokens=None, duration_ms=0)
            yield {"done": True, "model": model, "eval_count": None,
                   "eval_duration": None, "duration_ms": 0, "tok_per_sec": None}
            return

        # ── Deterministic ballistic short-circuit (see chat() for rationale) ──
        # Verified physics is yielded as a single chunk; the chat model is never
        # called, so it cannot mis-state drop/spin-drift/TOF.
        if self._is_ballistic_query(user_message):
            ballistic_answer = self._ballistic_user_answer(user_message)
            if ballistic_answer:
                ballistic_answer += (
                    "\n\n---\nConfidence: HIGH | "
                    "Source: Ballistic Calculator (verified G1 physics)"
                )
                _bx = json.dumps(self._build_explain_trace(
                    user_message=user_message, path="ballistic", ctx_meta={},
                    settings=settings, model=model,
                    confidence_label="HIGH | Source: Ballistic Calculator (verified G1 physics)",
                    ballistic_answer=ballistic_answer))
                db.ai_add_message(chat_id, "user", user_message)
                yield ballistic_answer
                db.ai_add_message(chat_id, "assistant", ballistic_answer,
                                  tokens=None, duration_ms=0, explain=_bx)
                chat = db.ai_get_chat(chat_id)
                if chat and chat.get("title") == "New Chat":
                    short_title = user_message[:40].strip()
                    if len(user_message) > 40:
                        short_title += "…"
                    db.ai_update_chat_title(chat_id, short_title)
                yield {"done": True, "model": model, "eval_count": None,
                       "eval_duration": None, "duration_ms": 0, "tok_per_sec": None}
                return

        context, ctx_meta = self.build_context(user_message, settings=settings)

        # Agent loop: run calculator agent pass before streaming so numbers are pre-verified
        explain_calc_block = None
        if self._is_physics_query(user_message):
            calc_block, had_results = self._calc_agent_pass(user_message, settings)
            if had_results:
                explain_calc_block = calc_block
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
                "repeat_penalty": float(settings.get("repeat_penalty", DEFAULT_SETTINGS["repeat_penalty"])),
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

        explain_json = self._explain_trace_json(
            user_message, ctx_meta, settings, model, full_content, explain_calc_block)
        db.ai_add_message(chat_id, "assistant", full_content, tokens=eval_count,
                          duration_ms=duration_ms, explain=explain_json)

        chat = db.ai_get_chat(chat_id)
        if chat and chat.get("title") == "New Chat":
            short_title = user_message[:40].strip()
            if len(user_message) > 40:
                short_title += "…"
            db.ai_update_chat_title(chat_id, short_title)

        yield {"done": True, "model": model, "eval_count": eval_count,
               "eval_duration": eval_duration, "duration_ms": duration_ms, "tok_per_sec": tok_per_sec}
