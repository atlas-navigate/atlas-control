"""Stats collection must not spawn a process or sleep per request."""
import os
import stat
import tempfile
import time
import unittest
from unittest import mock

import system_stats

LINE = ("09-25-2026 15:37:39 RAM 5332/7485MB (lfb 3x4MB) SWAP 676/36511MB (cached 2MB) "
        "CPU [10%@729,4%@1036,3%@1036,3%@1036,1%@729,2%@729] GR3D_FREQ 7% cpu@48.5C gpu@47.1C")


class TegraStatsReaderTests(unittest.TestCase):
    def fake_tegrastats(self, lines_per_run=1000):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        runs = os.path.join(d, "runs")
        script = os.path.join(d, "tegrastats")
        with open(script, "w") as f:
            f.write("#!/bin/sh\necho run >> %s\ni=0\nwhile [ $i -lt %d ]; do echo '%s'; sleep 0.05; i=$((i+1)); done\n"
                    % (runs, lines_per_run, LINE))
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        return script, runs

    def test_one_process_serves_many_reads(self):
        script, runs = self.fake_tegrastats()
        reader = system_stats.TegraStatsReader(command=[script])
        for _ in range(20):
            sample = reader.latest()
            self.assertEqual(sample["gpu_pct"], 7)
        self.assertEqual(open(runs).read().count("run"), 1)
        reader.IDLE_STOP_S = 0          # let it stop
        time.sleep(0.3)
        reader._thread.join(3)
        self.assertFalse(reader._thread.is_alive())

    def test_missing_tegrastats_is_harmless_and_not_retried_constantly(self):
        reader = system_stats.TegraStatsReader(command=["/nonexistent/tegrastats"])
        self.assertEqual(reader.latest(), {})
        self.assertEqual(reader.latest(), {})
        self.assertGreater(reader._unavailable_until, time.monotonic())

    def test_get_stats_does_not_sleep_for_a_cpu_sample(self):
        import psutil
        calls = []
        real = psutil.cpu_percent
        with mock.patch.object(system_stats, "_tegrastats", mock.Mock(latest=lambda: {})), \
                mock.patch.object(system_stats, "_get_disk_extended", return_value={}), \
                mock.patch.object(psutil, "cpu_percent",
                                  side_effect=lambda **kw: (calls.append(kw), real(**kw))[1]):
            system_stats._cache["data"] = None
            stats = system_stats.get_stats()
        self.assertEqual([c.get("interval") for c in calls], [None])
        self.assertIn("cpu_pct", stats)


if __name__ == "__main__":
    unittest.main()
