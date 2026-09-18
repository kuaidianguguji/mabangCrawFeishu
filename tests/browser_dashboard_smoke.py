"""可选真实浏览器验证：本地模拟看板，不登录马帮、不访问飞书。

项目根目录执行：python tests/browser_dashboard_smoke.py。
"""
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mabang_sync.browser import open_browser
from mabang_sync.collector import MODULES, collect
from mabang_sync.config import load_config


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/"):
            content = json.dumps({"success": True, "code": 200, "data": {
                "timezone": parse_qs(urlsplit(self.path).query)["tz"][0]}}).encode()
            kind = "application/json"
        else:
            content = ('''<!doctype html><meta charset="utf-8"><section>
            <div data-filter="timezone"><button id="zone" onclick="document.getElementById('list').style.display='block'">UTC+8</button></div>
            <div id="list" role="listbox" style="display:none;height:80px;width:300px;overflow:auto">
            <div style="height:900px">Other timezones</div>
            <button title="巴西时区 UTC-3" onclick="selectBrazil()">巴西时区 UTC-3</button></div>
            </section><script>
            let current=localStorage.getItem('tz')||'UTC+8';
            document.getElementById('zone').textContent=current;
            const modules=MODULE_LIST;
            function emit(){for(const m of modules)fetch('/api/'+m+'?tz='+encodeURIComponent(current));}
            function selectBrazil(){current='UTC-3';localStorage.setItem('tz',current);
            document.getElementById('zone').textContent=current;
            document.getElementById('list').style.display='none';emit();}
            emit();
            </script>'''.replace("MODULE_LIST", json.dumps(MODULES))).encode()
            kind = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = None
    try:
        with tempfile.TemporaryDirectory(prefix="mabang-dashboard-smoke-") as temp:
            config = load_config(Path(__file__).resolve().parents[1] / "config.example.toml")
            config["browser"].update(headless=True, profile_dir=temp, port=9448)
            config["mabang"].update(dashboard_url=f"http://127.0.0.1:{server.server_port}/page",
                                    api_base=f"http://127.0.0.1:{server.server_port}/api/")
            config["timing"].update(capture_timeout=8, retry_count=0)
            browser, tab = open_browser(config)
            try:
                for mode in ("switch_and_scroll", "already_selected_initial_responses", "different_api_host"):
                    if mode == "different_api_host":
                        config["mabang"]["api_base"] = config["mabang"]["api_base"].replace("127.0.0.1", "localhost")
                    saved = {}
                    captured, errors = collect(tab, config, lambda name, body: saved.update({name: body}))
                    assert len(captured) == len(saved) == 8, (mode, errors, list(captured))
                    assert all(body["data"]["timezone"] == "UTC-3" for body in captured.values()), mode
                    print(mode, "PASS", flush=True)
            finally:
                browser.quit()
                browser = None
    finally:
        if browser:
            browser.quit()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
