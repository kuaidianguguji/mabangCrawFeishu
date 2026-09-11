import logging
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
    stage = "等待首页登录状态"
    while time.monotonic() < deadline:
        if tab.ele("xpath:" + m["logged_in_xpath"], timeout=0.1):
            logging.info("已检测到登录成功标志 mb-user")
            return
        link = tab.ele("xpath:" + m["login_link_xpath"], timeout=0.1)
        # 首页可能预先存在账号输入框，必须先点击登录入口再访问表单。
        if not clicked and link:
            stage = "等待首页登录按钮可见"
            if link.states.is_displayed:
                link.click()
                clicked = True
                stage = "等待登录弹窗的账号、密码及提交按钮可见"
                logging.info("已点击首页登录按钮，等待登录弹窗")
            time.sleep(t["poll_interval"])
            continue

        if clicked and not submitted:
            elements = {key: tab.ele("xpath:" + m[key], timeout=0.1)
                        for key in ("username_xpath", "password_xpath", "submit_xpath")}
            missing = [m[key] for key, element in elements.items()
                       if not element or not element.states.is_displayed]
            if missing:
                stage = "等待登录弹窗元素可见: " + ", ".join(missing)
            else:
                account = config["account"]
                if not account["username"] or not account["password"]:
                    raise RuntimeError("当前未登录，请在 config.toml 或环境变量中配置用户名和密码")
                elements["username_xpath"].input(account["username"], clear=True)
                elements["password_xpath"].input(account["password"], clear=True)
                elements["submit_xpath"].click()
                submitted = True
                stage = "已提交登录，等待首页；检查验证码或登录错误提示"
                logging.info("已提交登录；如有验证码，请在浏览器中完成")
        time.sleep(t["poll_interval"])
    raise RuntimeError(f"登录确认超时：{stage}；请检查页面和配置的 XPath/登录成功标志")
