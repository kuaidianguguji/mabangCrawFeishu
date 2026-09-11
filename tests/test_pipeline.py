import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mabang_sync.cleaners import clean_module, convert
from mabang_sync.collector import MODULES, collect, validate_response
from mabang_sync.config import load_config
from mabang_sync.browser import ensure_login
from mabang_sync.storage import FileSink


def response(data):
    return {"success": True, "code": 200, "data": data, "currentTimestamp": 123}


class PipelineTests(unittest.TestCase):
    def test_null_zero_percent_and_precision(self):
        self.assertIsNone(convert(None, "decimal"))
        self.assertIsNone(convert("-", "ratio"))
        self.assertEqual(convert(0, "integer"), 0)
        self.assertEqual(convert("-64.5%", "ratio"), "-0.645")
        self.assertEqual(convert("58.581", "decimal"), "58.581")
        self.assertEqual(convert("0012", "string"), "0012")
        for bad in (True, 1.2, -1, "NaN"):
            with self.assertRaises(ValueError):
                convert(bad, "integer")
        with self.assertRaises(ValueError):
            convert("0.2", "ratio")

    def test_schema_failure_not_zero(self):
        result = clean_module("sales-overview", response({"trend": {"days": [{"date": "invalid"}], "currencyCode": "CNY"}}))
        self.assertEqual(result["status"], "schema_error")
        self.assertEqual(result["tables"], {})

    def test_unknown_module_preserved(self):
        data = {"unknown": [1, 2]}
        result = clean_module("hourly", response(data))
        self.assertEqual(result["status"], "unmapped")
        self.assertEqual(result["unmapped_data"], data)

    def test_empty_rankings_valid(self):
        result = clean_module("shop-ranking", response([]))
        self.assertEqual(result["tables"]["shop_ranking"], [])

    def test_business_failure_rejected(self):
        with self.assertRaises(ValueError):
            validate_response({"success": False, "code": 200, "data": {}})

    def test_partial_report(self):
        with tempfile.TemporaryDirectory() as folder:
            sink = FileSink(folder)
            report = sink.finish({"shop-ranking": response([])}, {})
            self.assertFalse(report["capture_complete"])
            self.assertEqual(len(report["missing_modules"]), 7)
            self.assertTrue((sink.path / "report.json").exists())

    def test_collector_starts_before_navigation_deduplicates(self):
        config = load_config(Path("config.example.toml"))
        config["timing"]["retry_count"] = 0
        tab = Mock()
        packets = [SimpleNamespace(url=config["mabang"]["api_base"] + n + "?x=1", is_failed=False,
                                   response=SimpleNamespace(status=200, body=response({}))) for n in MODULES]
        tab.listen.wait.side_effect = [packets[0], packets[0], *packets[1:]]
        sink = Mock()
        captured, errors = collect(tab, config, sink)
        self.assertEqual(set(captured), set(MODULES))
        self.assertEqual(sink.call_count, 8)
        self.assertEqual(tab.mock_calls[0][0], "listen.start")
        self.assertEqual(tab.mock_calls[1][0], "get")
        self.assertEqual(errors, {})
        tab.listen.stop.assert_called_once()

    def test_collector_retries_failed_response(self):
        config = load_config(Path("config.example.toml"))
        tab = Mock()
        tab.get.side_effect = [RuntimeError("navigation"), None]
        tab.listen.wait.side_effect = [SimpleNamespace(url=config["mabang"]["api_base"] + n, is_failed=False,
            response=SimpleNamespace(status=200, body=response({}))) for n in MODULES]
        with patch("mabang_sync.collector.time.sleep"):
            captured, errors = collect(tab, config, Mock())
        self.assertEqual(len(captured), 8)
        self.assertEqual(tab.listen.start.call_count, 2)
        self.assertEqual(errors, {})

    def test_existing_login_does_not_submit(self):
        tab = Mock(url="https://www.mabangerp.com/index.htm")
        tab.ele.side_effect = lambda locator, timeout: Mock() if locator == 'xpath://div[@id="mb-user"]' else None
        ensure_login(tab, load_config(Path("config.example.toml")))
        tab.get.assert_called_once()

    def test_login_clicks_entry_before_form_and_waits_for_submit(self):
        config = load_config(Path("config.example.toml"))
        config["account"] = {"username": "test-user", "password": "test-password"}
        events = []
        state = {"opened": False, "submitted": False, "submit_lookups": 0}
        tab = Mock(url=config["mabang"]["home_url"])
        link, user, password, submit = (Mock() for _ in range(4))
        for element in (link, user, password, submit):
            element.states.is_displayed = True

        def open_popup():
            events.append("open")
            state["opened"] = True

        def submit_form():
            events.append("submit")
            state["submitted"] = True
            tab.url = "https://www.mabangerp.com/index.php?mod=main"

        link.click.side_effect = open_popup
        user.input.side_effect = lambda *a, **k: events.append("username")
        password.input.side_effect = lambda *a, **k: events.append("password")
        submit.click.side_effect = submit_form

        def locate(locator, timeout):
            m = config["mabang"]
            if locator == "xpath:" + m["logged_in_xpath"]:
                return Mock() if state["submitted"] else None
            if locator == "xpath:" + m["login_link_xpath"]:
                return None if state["submitted"] else link
            self.assertTrue(state["opened"], "登录入口点击前不能操作预加载表单")
            if locator == "xpath:" + m["submit_xpath"]:
                state["submit_lookups"] += 1
                return submit if state["submit_lookups"] > 1 else None
            return user if locator == "xpath:" + m["username_xpath"] else password

        tab.ele.side_effect = locate
        with patch("mabang_sync.browser.time.sleep"):
            ensure_login(tab, config)
        self.assertEqual(events, ["open", "username", "password", "submit"])
        self.assertEqual(state["submit_lookups"], 2)

    def test_url_without_user_marker_is_not_login_success(self):
        tab = Mock(url="https://www.mabangerp.com/index.php?mod=main")
        tab.ele.return_value = None
        config = load_config(Path("config.example.toml"))
        with patch("mabang_sync.browser.time.monotonic", side_effect=[0, 1, 121]), patch("mabang_sync.browser.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "登录确认超时"):
                ensure_login(tab, config)


if __name__ == "__main__":
    unittest.main()
