"""GPS: one live event per fix, at most twice a second, and track history
written on movement rather than every second."""
import unittest
from unittest import mock

import gps_node


class FakeSocket:
    def __init__(self):
        self.events = []

    def emit(self, event, data, namespace=None):
        self.events.append(event)


def _fix(t, lat=38.9, lon=-77.4, fix_type=3):
    return {"node_id": "!gps", "latitude": lat, "longitude": lon, "altitude": 100.0, "timestamp": t,
            "speed": 0.0, "heading": None, "fix_type": fix_type, "sats_in_view": 9}


class GpsEventTests(unittest.TestCase):
    def setUp(self):
        self.sock = FakeSocket()
        self.gps = gps_node.GpsNode(port="AUTO", socketio=self.sock)

    def test_many_fixes_per_second_become_at_most_two_events(self):
        for i in range(50):                                   # 10 Hz for 5 s while moving
            self.gps._emit_fix(_fix(1000 + i * 0.1, lat=38.9 + i * 1e-4))
        self.assertEqual(set(self.sock.events), {"gps_fix"})
        self.assertLessEqual(len(self.sock.events), 11)

    def test_parked_receiver_sends_a_heartbeat_not_a_stream(self):
        for i in range(600):                                  # 60 s at 10 Hz, not moving
            self.gps._emit_fix(_fix(2000 + i * 0.1))
        self.assertLessEqual(len(self.sock.events), 13)

    def test_track_history_follows_movement(self):
        with mock.patch.object(gps_node.db, "get_node", return_value={"node_id": "!gps"}), \
                mock.patch.object(gps_node.db, "update_node_position"), \
                mock.patch.object(gps_node.db, "insert_position") as insert:
            for i in range(300):                              # parked for 5 minutes at 1 Hz
                self.gps._store_fix(_fix(3000 + i))
            parked = insert.call_count
            for i in range(60):                               # then walking ~1.5 m/s for a minute
                self.gps._store_fix(_fix(3300 + i, lat=38.9 + i * 1.35e-5))
            walking = insert.call_count - parked
        self.assertLessEqual(parked, 6)                       # was 300
        self.assertGreaterEqual(walking, 7)                   # ~90 m moved → a point per 10 m
        self.assertLessEqual(walking, 10)


if __name__ == "__main__":
    unittest.main()
