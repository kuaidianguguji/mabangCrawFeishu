import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from mabang_sync.config import load_config
from mabang_sync.dashboard import trigger_dashboard, select_timezone, stop_listener, wait_timezone_rendered
from mabang_sync.collector import collect, MODULES


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path("config.example.toml"))

    def test_selected_timezone_keeps_navigation_listener_without_refresh(self):
        tab = Mock()
        tab.ele.return_value.text = "UTC-3 巴西时区"
        trigger_dashboard(tab, self.config, ["endpoint"])
        actions = [c[0] for c in tab.mock_calls]
        self.assertEqual(actions[:4], ["listen.stop", "listen.start", "get", "ele"])
        tab.listen.start.assert_called_once_with(["endpoint"], method=True, res_type=True)
        tab.listen.stop.assert_called_once()
        tab.refresh.assert_not_called()
        tab.ele.return_value.click.assert_not_called()

    def test_unstarted_listener_is_not_stopped(self):
        tab = Mock()
        tab.listen.listening = False
        stop_listener(tab)
        tab.listen.stop.assert_not_called()

    def test_internal_timezone_value_is_polled_until_utc_text(self):
        tab = Mock()
        button = Mock()
        button.text = "cbt,mla,mlb,mlu,br"
        tab.ele.side_effect = [button, button]
        button.text = "cbt,mla,mlb,mlu,br"

        def update_text(*args, **kwargs):
            button.text = "UTC+8"

        with patch("mabang_sync.dashboard.time.sleep", side_effect=update_text):
            rendered, text = wait_timezone_rendered(tab, self.config)
        self.assertIs(rendered, button)
        self.assertEqual(text, "UTC+8")
        self.assertEqual(tab.ele.call_count, 2)

    def test_switch_scrolls_clipped_option_after_restarting_listener(self):
        tab, button, option = Mock(), Mock(), Mock()
        button.text = "UTC+8"
        option.states.is_displayed = True
        option.click.side_effect = lambda **kw: setattr(button, "text", "UTC-3 巴西时区")
        tab.attach_mock(button, "button")
        tab.attach_mock(option, "option")
        tab.ele.side_effect = lambda locator, timeout: option if locator == "xpath:" + self.config["mabang"]["timezone_option_xpath"] else button
        trigger_dashboard(tab, self.config, ["endpoint"])
        actions = [c[0] for c in tab.mock_calls]
        starts = [i for i, action in enumerate(actions) if action == "listen.start"]
        stops = [i for i, action in enumerate(actions) if action == "listen.stop"]
        self.assertEqual(len(starts), 2)
        self.assertLess(starts[0], actions.index("get"))
        self.assertLess(actions.index("get"), stops[1])
        self.assertLess(stops[1], starts[1])
        self.assertLess(starts[1], actions.index("button.click"))
        self.assertLess(actions.index("option.scroll.to_see"), actions.index("option.click"))
        tab.refresh.assert_not_called()

    def test_virtualized_option_appears_after_panel_scroll(self):
        tab, panel, option = Mock(), Mock(), Mock()
        tab.ele.side_effect = [None, None, option] # 先检查是否已选中，再查找滚动选项
        tab.eles.return_value = [panel]
        option.states.is_displayed = panel.states.is_displayed = True
        with patch("mabang_sync.dashboard.time.sleep"):
            select_timezone(tab, self.config)
        panel.scroll.down.assert_called_once_with(300)
        option.scroll.to_see.assert_called_once_with(center=True)
        option.click.assert_called_once_with(by_js=False)

    def test_timezone_validation_failure_discards_batch(self):
        tab = Mock()
        self.config["timing"]["retry_count"] = 0
        tab.listen.wait.side_effect = [SimpleNamespace(url=self.config["mabang"]["api_base"] + n,
            is_failed=False, response=SimpleNamespace(status=200, body={"success": True, "code": 200, "data": {}})) for n in MODULES]
        save = Mock()
        with patch("mabang_sync.collector.trigger_dashboard"), patch("mabang_sync.collector.verify_timezone", side_effect=RuntimeError("timezone")):
            captured, errors = collect(tab, self.config, save)
        self.assertEqual(captured, {})
        save.assert_not_called()
        self.assertIn("navigation", errors)
