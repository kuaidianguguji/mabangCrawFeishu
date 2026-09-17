import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from mabang_sync.config import load_config
from mabang_sync.retry import run_with_retry, load_page, click_button, PermanentError
from mabang_sync.feishu import FeishuClient, sync_plan
from mabang_sync.feishu_plan import build_plan, table_schema
from mabang_sync.diagnostics import FeishuError
from mabang_sync.__main__ import run_collection
from mabang_sync.login_state import prepare_profile, restore_login_state, save_login_state
from test_feishu import sources, FakeClient


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path("config.example.toml"))
        for policy in self.config["retry"].values():
            policy["interval"] = 0

    def client(self):
        self.config["feishu"].update(app_id="app", app_secret="secret", app_token="base")
        self.config["feishu"]["tables"] = {key: "tbl_" + key for key in table_schema()}
        client = FeishuClient(self.config)
        client.session = Mock()
        return client

    def test_count_zero_and_permanent_and_interrupt_never_retry(self):
        operation = Mock(side_effect=RuntimeError())
        self.config["retry"]["button"]["count"] = 0
        with self.assertRaises(RuntimeError):
            run_with_retry(self.config, "button", operation)
        operation.assert_called_once()
        self.config["retry"]["button"]["count"] = 2
        for exception in (PermanentError(), FeishuError("bad field"), KeyboardInterrupt()):
            operation = Mock(side_effect=exception)
            with self.assertRaises(type(exception)):
                run_with_retry(self.config, "button", operation)
            operation.assert_called_once()

    def test_button_false_or_stale_refetches_element(self):
        tab, first, second, third = Mock(), Mock(), Mock(), Mock()
        first.click.side_effect = RuntimeError("stale")
        second.click.return_value = False
        tab.ele.side_effect = [first, second, third]
        click_button(tab, "//button", self.config)
        self.assertEqual(tab.ele.call_count, 3)
        third.click.assert_called_once()

    def test_click_exception_after_success_does_not_click_again(self):
        tab = Mock()
        tab.ele.return_value.click.side_effect = RuntimeError("response lost")
        click_button(tab, "//button", self.config, satisfied=lambda: True)
        tab.ele.return_value.click.assert_called_once()

    def test_page_false_and_exception_restart_listener_before_each_navigation(self):
        tab = Mock()
        tab.get.side_effect = [False, RuntimeError("network"), True]
        prepare = Mock()
        tab.attach_mock(prepare, "prepare")
        load_page(tab, "https://example.invalid", self.config, before_attempt=prepare)
        self.assertEqual([c[0] for c in tab.mock_calls], ["prepare", "get"] * 3)
        self.assertTrue(all(c.kwargs["retry"] == 0 for c in tab.get.call_args_list))

    def test_feishu_timeout_retries_same_create_token_across_clients(self):
        client = self.client()
        response = Mock(status_code=200)
        response.json.return_value = {"code": 0, "data": {"record": {"record_id": "rec1"}}}
        client.session.request.side_effect = [requests.Timeout(), response]
        fields = {"日期": 123, "销售额": 5}
        self.assertEqual(client.create("summary", fields)["record_id"], "rec1")
        calls = client.session.request.call_args_list
        self.assertEqual(calls[0].kwargs["params"], calls[1].kwargs["params"])
        other = self.client()
        other.session.request.return_value = response
        other.create("summary", copy.deepcopy(fields))
        self.assertEqual(calls[0].kwargs["params"], other.session.request.call_args.kwargs["params"])

    def test_feishu_429_retries_but_403_and_bad_field_do_not(self):
        for status, count in ((429, 4), (503, 4), (403, 1)):
            client = self.client()
            response = Mock(status_code=status)
            response.raise_for_status.side_effect = requests.HTTPError()
            client.session.request.return_value = response
            with self.assertRaises(FeishuError):
                client.request("GET", "records")
            self.assertEqual(client.session.request.call_count, count)
        client = self.client()
        response = Mock(status_code=200)
        response.json.return_value = {"code": 1254045, "msg": "field not found"}
        client.session.request.return_value = response
        with self.assertRaises(FeishuError):
            client.request("PUT", "records/rec1")
        client.session.request.assert_called_once()

    def test_whole_upload_retry_rereads_after_partial_success(self):
        client = FakeClient()
        original = client.create
        lost = True
        def create(table, fields):
            nonlocal lost
            result = original(table, fields)
            if lost:
                lost = False
                raise FeishuError("response lost", retryable=True)
            return result
        client.create = create
        plan = build_plan(sources(), self.config)
        result = run_with_retry(self.config, "after_capture", lambda: sync_plan(client, plan, self.config))
        self.assertTrue(result["complete"])
        self.assertEqual(len(client.writes), 6)
        self.assertTrue(all(len(rows) == 1 for rows in client.data.values()))

    def test_capture_restart_then_disk_retry_does_not_recapture_json(self):
        with tempfile.TemporaryDirectory() as root:
            self.config["output"]["directory"] = root
            self.config["browser"]["login_info_dir"] = root
            first, second, tab = Mock(), Mock(), Mock()
            from mabang_sync.storage import FileSink
            original = FileSink.save_raw
            fail_once = True
            def save(sink, name, body):
                nonlocal fail_once
                if fail_once:
                    fail_once = False
                    raise OSError("disk temporarily unavailable")
                return original(sink, name, body)
            with patch("mabang_sync.browser.open_browser", side_effect=[(first, tab), (second, tab)]), \
                 patch("mabang_sync.browser.ensure_login", side_effect=[RuntimeError("disconnected"), None]), \
                 patch("mabang_sync.login_state.restore_login_state"), patch("mabang_sync.login_state.save_login_state"), \
                 patch("mabang_sync.__main__.collect", return_value=(sources(), {})) as collect, \
                 patch.object(FileSink, "save_raw", save), \
                 patch("mabang_sync.__main__.export_feishu", return_value=True) as export:
                self.assertEqual(run_collection(self.config), 0)
            collect.assert_called_once()
            export.assert_called_once()
            first.quit.assert_called_once()
            second.quit.assert_called_once()
            self.assertEqual(len(list(Path(root).glob("*/raw/*.json"))), 8)

    def test_invalid_retry_values_rejected(self):
        for text in ('count = -1', 'count = 1.5', 'interval = inf', 'interval = -1'):
            with tempfile.TemporaryDirectory() as root:
                path = Path(root) / "config.toml"
                path.write_text("[retry.page]\n" + text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_missing_json_retries_then_stops_without_upload(self):
        with tempfile.TemporaryDirectory() as root:
            self.config["output"]["directory"] = root
            self.config["retry"]["before_capture"]["count"] = 1
            browser, tab = Mock(), Mock()
            with patch("mabang_sync.browser.open_browser", return_value=(browser, tab)), \
                 patch("mabang_sync.browser.ensure_login"), \
                 patch("mabang_sync.login_state.restore_login_state", return_value=None), \
                 patch("mabang_sync.login_state.save_login_state"), \
                 patch("mabang_sync.__main__.collect", return_value=({}, {})) as collect, \
                 patch("mabang_sync.__main__.export_feishu") as export:
                with self.assertRaisesRegex(RuntimeError, "尚未收齐"):
                    run_collection(self.config)
            self.assertEqual(collect.call_count, 2)
            self.assertEqual(browser.quit.call_count, 2)
            export.assert_not_called()
            self.assertEqual(len(list(Path(root).glob("*/capture_failure.json"))), 2)

    def test_postprocessing_exception_retries_same_batch_and_folder(self):
        with tempfile.TemporaryDirectory() as root:
            self.config["output"]["directory"] = root
            browser, tab, raw = Mock(), Mock(), sources()
            with patch("mabang_sync.browser.open_browser", return_value=(browser, tab)) as opened, \
                 patch("mabang_sync.browser.ensure_login"), \
                 patch("mabang_sync.login_state.restore_login_state", return_value=None), \
                 patch("mabang_sync.login_state.save_login_state"), \
                 patch("mabang_sync.__main__.collect", return_value=(raw, {})) as collect, \
                 patch("mabang_sync.__main__.export_feishu", side_effect=[OSError("temporary"), True]) as export:
                self.assertEqual(run_collection(self.config), 0)
            opened.assert_called_once()
            collect.assert_called_once()
            self.assertEqual(export.call_count, 2)
            self.assertIs(export.call_args_list[0].args[0], raw)
            self.assertIs(export.call_args_list[1].args[0], raw)
            self.assertEqual(export.call_args_list[0].args[2], export.call_args_list[1].args[2])

    def test_profile_migration_preserves_old_and_only_runs_once(self):
        with tempfile.TemporaryDirectory() as root:
            old, new = Path(root) / "old", Path(root) / "storage" / "profile"
            old.mkdir()
            (old / "Local State").write_text("old", encoding="utf-8")
            self.config["browser"].update(profile_dir=str(new), legacy_profile_dir=str(old), login_info_dir=str(new.parent))
            prepare_profile(self.config)
            (new / "Local State").write_text("new", encoding="utf-8")
            prepare_profile(self.config)
            self.assertEqual((old / "Local State").read_text(), "old")
            self.assertEqual((new / "Local State").read_text(), "new")

    def test_cookie_session_snapshot_and_expired_cookie_filter(self):
        with tempfile.TemporaryDirectory() as root:
            self.config["browser"]["login_info_dir"] = root
            tab = Mock()
            tab.cookies.return_value = [
                {"name": "session", "value": "private", "domain": ".mabangerp.com", "path": "/", "httpOnly": True, "expires": -1},
                {"name": "expired", "value": "old", "domain": ".mabangerp.com", "expires": 1}]
            tab.run_js.return_value = {"origin": "https://www.mabangerp.com", "values": {"key": "secret"}}
            save_login_state(tab, self.config)
            restored = Mock()
            restored.cookies.return_value = []
            restore_login_state(restored, self.config)
            cookies = restored.browser.set.cookies.call_args.args[0]
            self.assertEqual([c["name"] for c in cookies], ["session"])
            self.assertTrue(cookies[0]["httpOnly"])
            self.assertIn("https://www.mabangerp.com", restored.add_init_js.call_args.args[0])
            restored.reset_mock()
            restored.cookies.return_value = [tab.cookies.return_value[0]]
            restore_login_state(restored, self.config)
            restored.browser.set.cookies.assert_not_called()
