import unittest
from unittest import mock

import app


class SoftwareUpdateCheckTests(unittest.TestCase):
    def setUp(self):
        with app._UPDATE_CHECK_STATE_LOCK:
            app._UPDATE_CHECK_STATE.update({
                "checking": False,
                "last_attempt_at": None,
                "last_check_error": None,
                "result": None,
            })

    def test_successful_check_is_cached_for_get_requests(self):
        def fake_git(*args, **_kwargs):
            responses = {
                ("fetch", "origin", "main"): (0, ""),
                ("rev-parse", "--short", "FETCH_HEAD"): (0, "abc1234"),
                ("rev-list", "--count", "HEAD..FETCH_HEAD"): (0, "2"),
                ("rev-list", "--count", "FETCH_HEAD..HEAD"): (0, "0"),
                ("status", "--porcelain", "--untracked-files=no"): (0, ""),
                (
                    "log", "--format=%h%x09%cs%x09%s", "--max-count=20",
                    "HEAD..FETCH_HEAD",
                ): (0, "abc1234\t2026-07-11\tUpdate check change"),
            }
            return responses[args]

        local = {"version": "def5678", "is_git_checkout": True, "installed_at": None}
        with mock.patch.object(app, "_update_local_info", return_value=local), \
                mock.patch.object(app, "_update_unit_active", return_value=False), \
                mock.patch.object(app, "_update_git", side_effect=fake_git):
            payload, status = app._run_software_update_check()
            cached = app._update_public_info()

        self.assertEqual(status, 200)
        self.assertTrue(payload["update_available"])
        self.assertEqual(payload["behind"], 2)
        self.assertEqual(payload["commits"][0]["subject"], "Update check change")
        self.assertEqual(cached["latest"], "abc1234")
        self.assertEqual(cached["auto_check_interval_seconds"], 30 * 60)
        self.assertIsNone(cached["last_check_error"])

    def test_failed_fetch_records_error_without_a_success_timestamp(self):
        local = {"version": "def5678", "is_git_checkout": True, "installed_at": None}
        with mock.patch.object(app, "_update_local_info", return_value=local), \
                mock.patch.object(app, "_update_unit_active", return_value=False), \
                mock.patch.object(app, "_update_git", return_value=(1, "")):
            payload, status = app._run_software_update_check()

        snapshot = app._update_check_snapshot()
        self.assertEqual(status, 502)
        self.assertIn("Could not reach GitHub", payload["error"])
        self.assertIn("Could not reach GitHub", snapshot["last_check_error"])
        self.assertNotIn("checked_at", snapshot)
        self.assertFalse(snapshot["checking"])

    def test_automatic_check_does_not_contact_github_while_offline(self):
        with mock.patch.object(
                app, "_network_connectivity_state",
                return_value={"online": False, "checked": True, "state": "none"}), \
                mock.patch.object(app, "_update_unit_active", return_value=False), \
                mock.patch.object(app, "_run_software_update_check") as run_check:
            result = app._automatic_software_update_check_once()

        self.assertEqual(result, "offline")
        run_check.assert_not_called()

    def test_automatic_check_runs_when_due_and_online(self):
        response = ({"update_available": False}, 200)
        with mock.patch.object(
                app, "_network_connectivity_state",
                return_value={"online": True, "checked": True, "state": "full"}), \
                mock.patch.object(app, "_update_unit_active", return_value=False), \
                mock.patch.object(
                    app, "_run_software_update_check", return_value=response,
                ) as run_check:
            result = app._automatic_software_update_check_once()

        self.assertEqual(result, "checked")
        run_check.assert_called_once_with(automatic=True)

    def test_manual_check_returns_conflict_when_another_check_is_running(self):
        local = {"version": "def5678", "is_git_checkout": True, "installed_at": None}
        with mock.patch.object(app, "_update_local_info", return_value=local), \
                mock.patch.object(app, "_update_unit_active", return_value=False):
            app._UPDATE_CHECK_RUN_LOCK.acquire()
            try:
                payload, status = app._run_software_update_check()
            finally:
                app._UPDATE_CHECK_RUN_LOCK.release()

        self.assertEqual(status, 409)
        self.assertIn("already in progress", payload["error"])

    def test_install_does_not_launch_during_an_update_check(self):
        with app.app.test_request_context("/api/update/apply", method="POST"):
            app._UPDATE_CHECK_RUN_LOCK.acquire()
            try:
                response, status = app.api_update_apply()
            finally:
                app._UPDATE_CHECK_RUN_LOCK.release()

        self.assertEqual(status, 409)
        self.assertIn("check is in progress", response.get_json()["error"])

    def test_unexpected_check_failure_clears_the_checking_state(self):
        with mock.patch.object(
                app, "_run_software_update_check_locked",
                side_effect=RuntimeError("test failure")), \
                mock.patch.object(app, "_update_public_info", return_value={}), \
                mock.patch.object(app.logger, "exception"):
            payload, status = app._run_software_update_check()

        self.assertEqual(status, 500)
        self.assertIn("unexpectedly", payload["error"])
        self.assertFalse(app._update_check_snapshot()["checking"])

    def test_successful_checks_are_due_every_thirty_minutes(self):
        now = 10_000
        self.assertFalse(app._automatic_update_check_due(
            {"checked_at": now - (30 * 60) + 1}, now,
        ))
        self.assertTrue(app._automatic_update_check_due(
            {"checked_at": now - (30 * 60)}, now,
        ))


if __name__ == "__main__":
    unittest.main()
