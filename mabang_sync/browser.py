import logging
import re
import socket
import time
from pathlib import Path


def open_browser(config):
    from DrissionPage import Chromium, ChromiumOptions

    b, t = config["browser"], config["timing"]
    # 不接管端口上未知浏览器，避免默默使用其他 profile。
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", b["port"])) == 0:
            raise RuntimeError("浏览器端口已占用，请关闭上次专用浏览器或修改端口")
    Path(b["profile_dir"]).mkdir(parents=True, exist_ok=True)
    options = ChromiumOptions(read_file=False)
    options.set_local_port(b["port"])
    options.set_user_data_path(b["profile_dir"])
    options.headless(b["headless"])
    options.set_argument("--lang", b["language"])
    options.set_argument("--window-size", b["window_size"])
    options.set_timeouts(base=t["element_timeout"], page_load=t["page_timeout"])
    if b["executable"]:
        options.set_browser_path(b["executable"])
    if b["user_agent"]:
        options.set_user_agent(b["user_agent"])
    browser = Chromium(options)
    return browser, browser.latest_tab


def ensure_login(tab, config):
    m, t = config["mabang"], config["timing"]
    tab.get(m["home_url"], timeout=t["page_timeout"])
    deadline = time.monotonic() + t["login_timeout"]
    submitted = False
    clicked = False
    while time.monotonic() < deadline:
        user = tab.ele("xpath:" + m["username_xpath"], timeout=0.1)
        link = tab.ele("xpath:" + m["login_link_xpath"], timeout=0.1)
        user_visible = bool(user and user.states.is_displayed)
        link_visible = bool(link and link.states.is_displayed)
        if not user_visible and not link_visible and re.search(m["logged_in_url_pattern"], tab.url):
            if not m["logged_in_xpath"] or tab.ele("xpath:" + m["logged_in_xpath"], timeout=0.1):
                logging.info("已确认登录首页")
                return
        if user_visible and not submitted:
            account = config["account"]
            if not account["username"] or not account["password"]:
                raise RuntimeError("当前未登录，请在 config.toml 或环境变量中配置用户名和密码")
            user.input(account["username"], clear=True)
            tab.ele("xpath:" + m["password_xpath"]).input(account["password"], clear=True)
            tab.ele("xpath:" + m["submit_xpath"]).click()
            submitted = True
            logging.info("已提交登录；如有验证码，请在浏览器中完成")
        elif link_visible and not clicked and not submitted:
            link.click()
            clicked = True
        time.sleep(t["poll_interval"])
    raise RuntimeError("登录确认超时：检查账号、验证码或已登录首页 URL/元素配置")
