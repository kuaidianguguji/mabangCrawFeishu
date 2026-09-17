"""本地真实浏览器验证：关闭再启动后恢复 Cookie、sessionStorage 和 localStorage。"""
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mabang_sync.config import load_config
from mabang_sync.browser import open_browser
from mabang_sync.login_state import restore_login_state, save_login_state
from mabang_sync.retry import load_page


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>Login persistence test</title><p>Local test</p>")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = None
    try:
        with tempfile.TemporaryDirectory() as root:
            config = load_config(Path("config.example.toml"))
            config["browser"].update(profile_dir=str(Path(root) / "profile"), login_info_dir=root,
                                     legacy_profile_dir="", port=9449, headless=True)
            url = f"http://127.0.0.1:{server.server_port}/"
            config["mabang"].update(home_url=url, dashboard_url=url)
            try:
                browser, tab = open_browser(config)
                load_page(tab, url, config)
                tab.browser.set.cookies([{"name": "session_test", "value": "ok", "domain": "127.0.0.1", "path": "/", "httpOnly": True}])
                tab.set.session_storage("session_test", "ok")
                tab.set.local_storage("local_test", "ok")
                save_login_state(tab, config)
                assert (Path(root) / "login_state.json").is_file(), "snapshot was not saved"
                browser.quit()
                browser = None
                browser, tab = open_browser(config)
                script = restore_login_state(tab, config)
                load_page(tab, url, config)
                if script:
                    tab.remove_init_js(script)
                assert any(c["name"] == "session_test" and c["value"] == "ok" and c["httpOnly"]
                           for c in tab.cookies(all_domains=True, all_info=True)), "session cookie missing"
                assert tab.session_storage("session_test") == "ok", "sessionStorage missing"
                assert tab.local_storage("local_test") == "ok", "localStorage missing"
                print("PASS: Cookie (HttpOnly/session), sessionStorage and localStorage restored across browser restarts")
            finally:
                if browser:
                    browser.quit()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
