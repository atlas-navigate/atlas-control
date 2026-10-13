"""OSRM start-up: a dead osrm-routed is noticed at once, the reason is
named, and waiting for a start never freezes the gevent hub."""
import os
import socket
import tempfile
import time
import unittest
from unittest import mock

import routing_node


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class DeadProc:
    pid = 999999

    def __init__(self, code=1):
        self.code = code

    def poll(self):
        return self.code


class PortReadyTests(unittest.TestCase):
    def test_gives_up_at_once_when_the_process_has_exited(self):
        start = time.monotonic()
        self.assertFalse(routing_node._port_ready(_free_port(), timeout=30, proc=DeadProc()))
        self.assertLess(time.monotonic() - start, 1.0)

    def test_sleeps_through_atlas_blocking(self):
        # On the hub, atlas_blocking.sleep is gevent.sleep: other requests run.
        with mock.patch.object(routing_node.atlas_blocking, "sleep") as sleep:
            self.assertFalse(routing_node._port_ready(_free_port(), timeout=0.05))
        self.assertTrue(sleep.called)

    def test_ready_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            self.assertTrue(routing_node._port_ready(s.getsockname()[1], timeout=1, proc=DeadProc(None)))
        finally:
            s.close()


class UnreadableGraphTests(unittest.TestCase):
    @unittest.skipIf(os.geteuid() == 0, "root reads everything")
    def test_names_the_files_this_account_cannot_read(self):
        with tempfile.TemporaryDirectory() as d:
            graph = os.path.join(d, "region.osrm")
            for name in ("region.osrm", "region.osrm.ebg", "region.osrm.fileIndex"):
                open(os.path.join(d, name), "w").close()
            os.chmod(os.path.join(d, "region.osrm.fileIndex"), 0o000)
            self.assertEqual(routing_node._unreadable_parts(graph), ["region.osrm.fileIndex"])

    def test_a_dead_start_is_reported_and_reaped(self):
        with tempfile.TemporaryDirectory() as d:
            graph_dir = os.path.join(d, "maryland", "car")
            os.makedirs(graph_dir)
            open(os.path.join(graph_dir, "region.osrm"), "w").close()
            open(os.path.join(graph_dir, ".processed"), "w").close()
            rn = routing_node.RoutingNode(states_dir=d, use_native=True)
            dead = DeadProc(1)
            with mock.patch.object(rn, "_start_native", return_value=dead), \
                    mock.patch.object(rn, "_stop_process") as stop, \
                    mock.patch.object(routing_node, "_unreadable_parts", return_value=["region.osrm.fileIndex"]), \
                    self.assertLogs("routing-node", "ERROR") as logs:
                start = time.monotonic()
                self.assertIsNone(rn._ensure_running("maryland", "car"))
            self.assertLess(time.monotonic() - start, 2.0)
            stop.assert_called_once()
            self.assertIn("cannot read region.osrm.fileIndex", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
