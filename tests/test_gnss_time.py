"""GPS time for setting Atlas's clock offline: only a time the receiver
vouches for (valid, fully resolved, confirmed) is used, and never one from a
GPS week-number rollover."""
import calendar
import struct
import time
import unittest

import pynmea2

import gps_node


def nav_pvt(year=2026, month=10, day=8, hour=22, minute=4, sec=5, valid=0x07, t_acc_ns=30,
            nano=250_000_000, flags=0x01, flags2=0xE0, fix=3):
    p = bytearray(92)
    struct.pack_into("<H", p, 4, year)
    p[6:12] = bytes([month, day, hour, minute, sec, valid])
    struct.pack_into("<I", p, 12, t_acc_ns)
    struct.pack_into("<i", p, 16, nano)
    p[20], p[21], p[22], p[23] = fix, flags, flags2, 9
    return bytes(p)


def nmea(body):
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return f"${body}*{cs:02X}"


RMC_ACTIVE = nmea("GPRMC,220405.25,A,3854.000,N,07724.000,W,0.0,0.0,081026,,,A")
RMC_VOID = nmea("GPRMC,220405.25,V,,,,,,,081026,,,N")
RMC_ROLLOVER = nmea("GPRMC,220405.25,A,3854.000,N,07724.000,W,0.0,0.0,220207,,,A")

EXPECTED = calendar.timegm((2026, 10, 8, 22, 4, 5, 0, 0, 0)) + 0.25


class UbxTimeTests(unittest.TestCase):
    def test_confirmed_time(self):
        epoch, acc = gps_node.ubx_pvt_time(nav_pvt(), gnss_ok=True)
        self.assertAlmostEqual(epoch, EXPECTED, places=6)
        self.assertLess(acc, 1e-6)

    def test_unconfirmed_time_is_ignored_when_the_receiver_reports_confirmation(self):
        self.assertIsNone(gps_node.ubx_pvt_time(nav_pvt(flags2=0x20), gnss_ok=True))   # avail, not confirmed
        self.assertIsNone(gps_node.ubx_pvt_time(nav_pvt(flags2=0x60), gnss_ok=True))   # date only

    def test_older_receivers_need_a_fix(self):
        self.assertIsNotNone(gps_node.ubx_pvt_time(nav_pvt(flags2=0x00), gnss_ok=True))
        self.assertIsNone(gps_node.ubx_pvt_time(nav_pvt(flags2=0x00), gnss_ok=False))

    def test_invalid_or_unresolved_time_is_ignored(self):
        for valid in (0x00, 0x03, 0x05, 0x06):
            self.assertIsNone(gps_node.ubx_pvt_time(nav_pvt(valid=valid), gnss_ok=True))

    def test_inaccurate_and_rollover_times_are_ignored(self):
        self.assertIsNone(gps_node.ubx_pvt_time(nav_pvt(t_acc_ns=2_000_000_000), gnss_ok=True))
        self.assertIsNone(gps_node.ubx_pvt_time(nav_pvt(year=2007), gnss_ok=True))   # 1024-week rollover

    def test_node_keeps_the_time_even_without_a_position_fix(self):
        gps = gps_node.GpsNode(port="AUTO")
        gps._decode_nav_pvt(nav_pvt(flags=0x00, fix=0))   # no fix, but confirmed time
        epoch, kind, _ = gps.gnss_time()
        self.assertEqual(kind, "ubx")
        self.assertAlmostEqual(epoch, EXPECTED, delta=1.0)

    def test_stale_time_is_not_offered(self):
        gps = gps_node.GpsNode(port="AUTO")
        gps._gnss_time = (EXPECTED, time.monotonic() - 30, "ubx", 0.0)
        self.assertIsNone(gps.gnss_time())


class RmcTimeTests(unittest.TestCase):
    def test_active_rmc(self):
        self.assertAlmostEqual(gps_node.rmc_time(pynmea2.parse(RMC_ACTIVE)), EXPECTED, places=3)
        gps = gps_node.GpsNode(port="AUTO")
        gps._parse_nmea_line(RMC_ACTIVE)
        self.assertEqual(gps.gnss_time()[1], "nmea")

    def test_void_rmc_sets_nothing(self):
        gps = gps_node.GpsNode(port="AUTO")
        gps._parse_nmea_line(RMC_VOID)
        self.assertIsNone(gps.gnss_time())

    def test_rollover_date_is_rejected(self):
        self.assertIsNone(gps_node.rmc_time(pynmea2.parse(RMC_ROLLOVER)))


if __name__ == "__main__":
    unittest.main()
