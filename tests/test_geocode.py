"""Offline reverse-geocoder and coordinate-parser tests.

These exist because both subsystems failed *silently* for an entire install
cycle: `_reverse_geocode()` read two GeoNames files that nothing ever
downloaded and returned None for every coordinate, and `_COORD_RE` could not
parse the very format `_fmt_coord()` emits. Ray was handed bare decimal degrees
and invented a city — a fix in Herndon, VA was reported as "1.2 miles west of
Pittsburgh, PA". Nothing caught either one, so they are pinned here.

The DB-backed tests skip (rather than fail) when static/data/us_cities.db is
absent, so a bare checkout without install.sh's data step still runs green.
"""
import os
import unittest

import ai_manager


HAS_CITIES_DB = os.path.exists(ai_manager._US_CITIES_DB)
_needs_db = unittest.skipUnless(
    HAS_CITIES_DB, f"{ai_manager._US_CITIES_DB} not built (run download_cities.py)")


@_needs_db
class ReverseGeocodeTests(unittest.TestCase):
    def setUp(self):
        ai_manager._GEO_CACHE.clear()

    def test_resolves_the_coordinate_that_produced_pittsburgh(self):
        """The original bug report, pinned exactly."""
        self.assertEqual(
            ai_manager._reverse_geocode(38.95890, -77.41555), "Herndon, Virginia")

    def test_gravity_beats_raw_proximity(self):
        """Herndon (25k, 2.8km) must win over Floris (8k, 2.4km).

        A nearest-centroid lookup returns Floris here. Weighting by
        population/distance² is the whole reason this resolves to the name a
        person would actually use.
        """
        self.assertNotIn("Floris", ai_manager._reverse_geocode(38.95890, -77.41555))

    def test_other_cities_resolve_correctly(self):
        """Right for the right reason, not just tuned to one coordinate."""
        for lat, lon, expected in [
            (40.4406, -79.9959, "Pittsburgh, Pennsylvania"),
            (37.7749, -122.4194, "San Francisco, California"),
            (64.8378, -147.7164, "Fairbanks, Alaska"),
            (21.3069, -157.8583, "Honolulu, Hawaii"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(ai_manager._reverse_geocode(lat, lon), expected)

    def test_far_from_anywhere_returns_none(self):
        """Mid-Pacific must resolve to nothing, not to the nearest landfall."""
        self.assertIsNone(ai_manager._reverse_geocode(30.0, -140.0))

    def test_nearby_small_town_is_not_vetoed_by_a_distant_large_one(self):
        """Range must gate candidacy, not just the final answer.

        At this remote Alaska coordinate Healy (pop ~1k) sits 46 km away and
        Fairbanks (pop ~32k) sits ~110 km away. Scoring first and range-checking
        afterwards lets Fairbanks win and then get rejected, yielding None for a
        coordinate that has a perfectly good answer.

        Targets the gravity tier directly: it is the fallback now that the ZIP
        tier runs first, but this candidacy logic still has to be right because
        it is the only US tier on a box where the GeoNames download never ran.
        """
        import math
        cos_lat = math.cos(math.radians(64.2008))
        self.assertEqual(
            ai_manager._us_city_lookup(64.2008, -149.4937, cos_lat), "Healy, Alaska")

    def test_result_is_cached_per_cell(self):
        ai_manager._reverse_geocode(38.95890, -77.41555)
        self.assertIn((38.959, -77.416), ai_manager._GEO_CACHE)

    def test_region_hint_resolves_for_rag(self):
        """_resolve_location_hint feeding None is what left retrieval region-blind."""
        self.assertEqual(
            ai_manager._resolve_location_hint(
                None, {"latitude": 38.9589, "longitude": -77.41555}),
            "Herndon, Virginia, Mid-Atlantic US")


@unittest.skipUnless(os.path.exists(ai_manager._US_ZIP),
                     f"{ai_manager._US_ZIP} not fetched (run download_geonames.py)")
class ZipTierTests(unittest.TestCase):
    """Tier 2 — GeoNames US postal centroids.

    Nothing in the repo fetched this file until download_geonames.py existed,
    so this tier silently loaded zero rows on every install.
    """

    def setUp(self):
        ai_manager._GEO_CACHE.clear()

    def test_wilderness_beats_the_gravity_tier(self):
        """Why the ZIP tier runs first.

        Population weighting drags the answer toward whatever large town is in
        range, which is wrong in exactly the terrain this device is for. Left
        column is what Ray used to say.
        """
        for lat, lon, expected, gravity_said in [
            (46.8523, -121.7603, "Longmire, Washington", "Tacoma (68 km away)"),
            (43.8791, -103.4591, "Keystone, South Dakota", "Rapid City (29 km)"),
            (48.7596, -113.7870, "West Glacier, Montana", "Kalispell (74 km)"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(ai_manager._reverse_geocode(lat, lon), expected,
                                 f"gravity tier would say {gravity_said}")

    def test_yellowstone_resolves_to_the_right_state(self):
        """The gravity tier named Montana for a coordinate inside Wyoming."""
        result = ai_manager._reverse_geocode(44.4280, -110.5885)
        self.assertIn("Wyoming", result)

    def test_towns_are_unchanged(self):
        """Reordering must not regress the populated case, incl. the original bug."""
        self.assertEqual(
            ai_manager._reverse_geocode(38.95890, -77.41555), "Herndon, Virginia")
        self.assertEqual(
            ai_manager._reverse_geocode(40.4406, -79.9959), "Pittsburgh, Pennsylvania")


@unittest.skipUnless(os.path.exists(ai_manager._GEO_TSV),
                     f"{ai_manager._GEO_TSV} not fetched (run download_geonames.py)")
class WorldTierTests(unittest.TestCase):
    """Tier 3 — the only source of any non-US place name (us_cities.db is US-only)."""

    def setUp(self):
        ai_manager._GEO_CACHE.clear()

    def test_international_coordinates_resolve(self):
        for lat, lon, expected in [
            (48.8566, 2.3522, "Paris, FR"),
            (-33.8688, 151.2093, "Sydney, AU"),
            (35.6762, 139.6503, "Tokyo, JP"),
            (51.5074, -0.1278, "London, GB"),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(ai_manager._reverse_geocode(lat, lon), expected)


class MissingDataTests(unittest.TestCase):
    def test_no_data_returns_none_and_warns(self):
        """A missing DB must be loud. Silence is how this shipped broken."""
        saved = (ai_manager._US_CITY_DATA, ai_manager._US_ZIP_DATA,
                 ai_manager._WORLD_GEO_DATA, ai_manager._GEO_WARNED)
        ai_manager._GEO_CACHE.clear()
        ai_manager._US_CITY_DATA = []
        ai_manager._US_ZIP_DATA = []
        ai_manager._WORLD_GEO_DATA = []
        ai_manager._GEO_WARNED = False
        try:
            with self.assertLogs("ai_manager", level="WARNING") as caught:
                result = ai_manager._reverse_geocode(38.95890, -77.41555)
            self.assertIsNone(result)
            self.assertTrue(
                any("reverse geocoder has NO data" in m for m in caught.output),
                caught.output)
        finally:
            (ai_manager._US_CITY_DATA, ai_manager._US_ZIP_DATA,
             ai_manager._WORLD_GEO_DATA, ai_manager._GEO_WARNED) = saved
            ai_manager._GEO_CACHE.clear()


class CoordinateParsingTests(unittest.TestCase):
    def assertCoords(self, msg, lat, lon):
        got = ai_manager._extract_user_location(msg)
        self.assertIsNotNone(got, f"no coordinate parsed from {msg!r}")
        self.assertEqual(got["type"], "coords")
        self.assertAlmostEqual(got["lat"], lat, places=4, msg=msg)
        self.assertAlmostEqual(got["lon"], lon, places=4, msg=msg)

    def test_suffix_notation(self):
        """The exact format _fmt_coord emits — previously parsed as 77.4155/-5.0."""
        self.assertCoords("38.95890°N 77.41555°W", 38.9589, -77.41555)
        self.assertCoords("What is at 38.95890N 77.41555W?", 38.9589, -77.41555)
        self.assertCoords("33.8688°S 151.2093°E", -33.8688, 151.2093)

    def test_prefix_notation(self):
        self.assertCoords("N38.9589 W77.41555", 38.9589, -77.41555)

    def test_signed_pairs(self):
        self.assertCoords("38.9589, -77.41555", 38.9589, -77.41555)
        self.assertCoords("38.9589 -77.41555", 38.9589, -77.41555)

    def test_keyword_form(self):
        self.assertCoords("lat 38.9589 lon -77.41555", 38.9589, -77.41555)
        self.assertCoords("lat=38.9589 long=-77.41555", 38.9589, -77.41555)

    def test_round_trips_through_fmt_coord(self):
        """The property that guards this whole class of bug.

        _fmt_coord emits suffix notation; if _COORD_RE cannot read it back,
        quoting Ray's own output at it corrupts the position.
        """
        for lat, lon in [(38.9589, -77.41555), (-33.8688, 151.2093),
                         (64.8378, -147.7164), (0.5, -0.5), (21.3069, -157.8583)]:
            with self.subTest(lat=lat, lon=lon):
                self.assertCoords(ai_manager._fmt_coord(lat, lon), lat, lon)

    def test_ordinary_numbers_are_not_coordinates(self):
        """'meeting at 3, 4 pm' parsed as lat 3 / lon 4 — the Gulf of Guinea —
        and was then injected as the user's location with 'USE THIS'."""
        for msg in ["Is there water near me",
                    "meeting at 3, 4 pm",
                    "I have 2 radios and 3 batteries",
                    "set channel to 5, 7"]:
            with self.subTest(msg=msg):
                got = ai_manager._extract_user_location(msg)
                self.assertNotEqual(
                    (got or {}).get("type"), "coords", f"false positive on {msg!r}")


class LocationPrefixTests(unittest.TestCase):
    """The prefix is prepended to the user turn, so it is the model's last word
    on where it is. It must never carry naked coordinates."""

    @_needs_db
    def test_names_the_place_for_a_device_fix(self):
        out = ai_manager._build_location_prefix(
            {"gps_fix": {"latitude": 38.9589, "longitude": -77.41555}})
        self.assertIn("Herndon, Virginia", out)

    @_needs_db
    def test_names_the_place_for_user_supplied_coords(self):
        """This branch used to emit coordinates and nothing else."""
        out = ai_manager._build_location_prefix(
            {"user_location": {"type": "coords", "lat": 40.4406, "lon": -79.9959}})
        self.assertIn("Pittsburgh, Pennsylvania", out)

    def test_unresolved_position_says_so(self):
        out = ai_manager._build_location_prefix(
            {"gps_fix": {"latitude": 30.0, "longitude": -140.0}})
        self.assertIn("locality unknown", out)

    def test_no_position_yields_no_prefix(self):
        self.assertEqual(ai_manager._build_location_prefix({}), "")


class GeometryTests(unittest.TestCase):
    def test_distance_matches_known_separation(self):
        # Herndon VA → Pittsburgh PA: 1.4817° lat (164.6 km) and 2.5804° lon
        # (220.9 km at the mean latitude) → 275.5 km great-circle.
        km = ai_manager._dist_km(38.9589, -77.41555, 40.4406, -79.9959)
        self.assertAlmostEqual(km, 275.5, delta=2)

    def test_bearing_cardinals(self):
        self.assertEqual(ai_manager._bearing_compass(0, 0, 1, 0), "N")
        self.assertEqual(ai_manager._bearing_compass(0, 0, -1, 0), "S")
        self.assertEqual(ai_manager._bearing_compass(0, 0, 0, 1), "E")
        self.assertEqual(ai_manager._bearing_compass(0, 0, 0, -1), "W")


class CategoryDetectionTests(unittest.TestCase):
    def test_category_detection(self):
        self.assertEqual(ai_manager._detect_category("somewhere to eat near me"), "food")
        self.assertEqual(ai_manager._detect_category("nearest hospital"), "health")
        self.assertIsNone(ai_manager._detect_category("how do I start a fire"))

    def test_kind_detection_carries_indexed_categories(self):
        """kinds must always arrive with categories — places.db has no kind index,
        so a kind-only query scans a nationwide latitude band (~6s vs ~0.05s)."""
        for query in ("is there water near me", "any trails nearby",
                      "where can I shelter"):
            with self.subTest(query=query):
                label, cats, kinds = ai_manager._detect_kind_query(query)
                self.assertIsNotNone(label)
                self.assertTrue(cats, f"{query!r} produced kinds with no categories")
                self.assertTrue(kinds)

    def test_non_location_query_detects_nothing(self):
        label, cats, kinds = ai_manager._detect_kind_query("what is a lithium battery")
        self.assertIsNone(label)


@unittest.skipUnless(os.path.exists(ai_manager._PLACES_DB),
                     "places.db not built (run build_places_index.py)")
class NearbyPlacesTests(unittest.TestCase):
    LAT, LON = 38.95890, -77.41555

    def test_finds_real_named_features(self):
        rows = ai_manager._nearby_places(self.LAT, self.LON, categories=("food",))
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue(r["name"])
            self.assertGreaterEqual(r["dist_km"], 0)
            self.assertIn(r["bearing"], ai_manager._COMPASS_16)

    def test_results_are_sorted_by_distance(self):
        rows = ai_manager._nearby_places(self.LAT, self.LON, categories=("food",))
        self.assertEqual([r["dist_km"] for r in rows],
                         sorted(r["dist_km"] for r in rows))

    def test_requires_a_category(self):
        """Guards the index: a kind-only call would scan the whole lat band."""
        self.assertEqual(
            ai_manager._nearby_places(self.LAT, self.LON, categories=None), [])


if __name__ == "__main__":
    unittest.main()
