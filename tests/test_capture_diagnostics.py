import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mabang_sync.config import load_config
from mabang_sync.collector import MODULES, collect


def packet(name, status=200, body=None, method="POST", host="other.example"):
    return SimpleNamespace(url=f"https://{host}/proxy/api/biscreen/v1/modules/{name}/?token=never-log-this",
                           is_failed=False, request=SimpleNamespace(method=method),
                           response=SimpleNamespace(status=status, body=body if body is not None else
                                                    {"code": 200, "success": True, "data": {}}))


class CaptureDiagnosticTests(unittest.TestCase):
    def test_changed_host_prefix_and_trailing_slash_are_matched(self):
        config = load_config(Path("config.example.toml"))
        config["timing"]["retry_count"] = 0
        tab, saved, diagnostics = Mock(), Mock(), Mock()
        tab.listen.wait.side_effect = [packet(name) for name in MODULES]
        with patch("mabang_sync.collector.trigger_dashboard") as trigger, patch("mabang_sync.collector.verify_timezone"):
            with self.assertLogs(level="INFO") as logs:
                captured, errors = collect(tab, config, saved, diagnostics)
        self.assertEqual(len(captured), 8)
        self.assertEqual(errors, {})
        self.assertIs(trigger.call_args.args[2], True)
        self.assertNotIn("never-log-this", "\n".join(logs.output))
        self.assertEqual(diagnostics.call_args.args[0]["rounds"][0]["accepted"], 8)

    def test_http_business_rejection_and_options_are_visible_without_bodies(self):
        config = load_config(Path("config.example.toml"))
        config["timing"]["retry_count"] = 0
        tab, diagnostics = Mock(), Mock()
        tab.listen.wait.side_effect = [packet("hourly", method="OPTIONS"), packet("hourly", status=403),
                                      packet("statistics", body={"success": False, "code": 401, "data": None, "msg": "private-body"}),
                                      *[packet(name) for name in MODULES]]
        with patch("mabang_sync.collector.trigger_dashboard"), patch("mabang_sync.collector.verify_timezone"):
            with self.assertLogs(level="INFO") as logs:
                captured, errors = collect(tab, config, Mock(), diagnostics)
        report = diagnostics.call_args.args[0]["rounds"][0]
        self.assertEqual(report["rejected"], 2)
        self.assertEqual(report["preflight"], 1)
        self.assertEqual(report["rejections"][0]["http_status"], 403)
        self.assertEqual(report["rejections"][1]["code"], 401)
        self.assertEqual(len(captured), 8)
        self.assertEqual(errors, {})
        text = "\n".join(logs.output)
        self.assertIn("已收到目标响应但校验拒绝", text)
        self.assertNotIn("private-body", text)

    def test_non_target_packet_is_reported_but_not_saved(self):
        config = load_config(Path("config.example.toml"))
        config["timing"]["retry_count"] = 0
        tab, saved, diagnostics = Mock(), Mock(), Mock()
        tab.listen.wait.side_effect = [packet("other"), *[packet(name) for name in MODULES]]
        with patch("mabang_sync.collector.trigger_dashboard"), patch("mabang_sync.collector.verify_timezone"):
            collect(tab, config, saved, diagnostics)
        report = diagnostics.call_args.args[0]["rounds"][0]
        self.assertEqual(len(report["other_endpoints"]), 1)
        self.assertEqual(saved.call_count, 8)
        self.assertNotIn("?token", report["other_endpoints"][0])
