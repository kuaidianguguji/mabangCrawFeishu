import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from filelock import FileLock

from mabang_sync.config import load_config
from mabang_sync.scheduler import BEIJING, next_run, run_scheduler, run_slot
from mabang_sync.__main__ import run_collection


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path("config.example.toml"))

    def test_beijing_time_independent_of_os_timezone(self):
        now = datetime(2026, 9, 11, 1, 54, tzinfo=timezone.utc)
        expected = datetime(2026, 9, 11, 9, 55, tzinfo=BEIJING)
        self.assertEqual(next_run(now, ["09:55"]), expected)

    def test_startup_after_time_waits_until_tomorrow(self):
        now = datetime(2026, 9, 11, 10, tzinfo=BEIJING)
        self.assertEqual(next_run(now, ["09:55"]).day, 12)

    def test_exact_time_and_persisted_slot_not_repeated(self):
        now = datetime(2026, 9, 11, 9, 55, tzinfo=BEIJING)
        self.assertEqual(next_run(now, ["09:55"]), now)
        self.assertEqual(next_run(now, ["09:55"], now.isoformat()).day, 12)

    def test_clock_rollback_does_not_repeat_previous_days(self):
        now = datetime(2026, 9, 10, 9, tzinfo=BEIJING)
        last = datetime(2026, 9, 11, 9, 55, tzinfo=BEIJING).isoformat()
        self.assertEqual(next_run(now, ["09:55"], last).day, 12)

    def test_failed_slot_recorded_and_scheduler_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.config["schedule"].update(state_dir=tmp, times=["09:55", "09:56"])
            moment = [datetime(2026, 9, 11, 9, 54, 59, tzinfo=BEIJING)]
            runner = Mock(side_effect=[RuntimeError("test failure"), 0])

            def sleep(seconds):
                if runner.call_count >= 2:
                    raise KeyboardInterrupt
                moment[0] += timedelta(seconds=seconds)

            with patch("mabang_sync.scheduler.load_config", return_value=self.config):
                result = run_scheduler(Path(tmp) / "config.toml", runner, lambda: moment[0], sleep)
            self.assertEqual(result, 0)
            self.assertEqual(runner.call_count, 2)
            state = json.loads((Path(tmp) / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "success")
            self.assertIn("09:56", state["last_slot"])

    def test_slot_is_persisted_before_runner_and_failure_has_no_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            slot = datetime(2026, 9, 11, 9, 55, tzinfo=BEIJING)

            def runner(config):
                self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["status"], "running")
                raise RuntimeError("sensitive exception text")

            with patch("mabang_sync.scheduler.load_config", return_value=self.config):
                state = run_slot(slot, path, Path("config.toml"), runner, lambda: slot)
            self.assertEqual(state["status"], "failed")
            self.assertNotIn("sensitive", path.read_text(encoding="utf-8"))

    def test_duplicate_scheduler_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.config["schedule"]["state_dir"] = tmp
            with FileLock(str(Path(tmp) / "scheduler.lock")), patch("mabang_sync.scheduler.load_config", return_value=self.config):
                with self.assertRaisesRegex(RuntimeError, "已有常驻进程"):
                    run_scheduler(Path("config.toml"), Mock())

    def test_collection_closes_browser_on_login_failure(self):
        browser = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            self.config["output"]["directory"] = tmp
            with patch("mabang_sync.browser.open_browser", return_value=(browser, Mock())), patch("mabang_sync.browser.ensure_login", side_effect=RuntimeError("login")):
                with self.assertRaises(RuntimeError):
                    run_collection(self.config)
        browser.quit.assert_called_once()

    def test_invalid_time_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.toml"
            for value in ('["25:00"]', '["9:55"]', '[]', '["09:55", "09:55"]', '[955]'):
                config.write_text('[schedule]\ntimes = ' + value, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(config)


if __name__ == "__main__":
    unittest.main()
