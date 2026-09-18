"""看板时区选择与监听重置，不处理响应业务数据。"""
import logging
import time
from .retry import load_page, click_button, run_with_retry


def stop_listener(tab):
    # DrissionPage 4.1 的未启动监听器不能直接 stop（内部 driver 尚为 None）。
    if tab.listen.listening:
        tab.listen.stop()


def timezone_button(tab, config):
    button = tab.ele("xpath:" + config["mabang"]["timezone_button_xpath"], timeout=config["timing"]["element_timeout"])
    if not button:
        logging.error("时区按钮未找到；XPath=%s", config["mabang"]["timezone_button_xpath"])
        raise RuntimeError("未找到看板时区选择按钮")
    return button


def verify_timezone(tab, config):
    expected = config["mabang"]["timezone_text"]
    xpath = config["mabang"]["timezone_button_xpath"]
    previous = object()
    actual_text = None
    deadline = time.monotonic() + config["timing"]["element_timeout"]
    while time.monotonic() < deadline:
        button = tab.ele("xpath:" + xpath, timeout=0.2)
        actual_text = button.text if button else None
        matched = actual_text is not None and expected in actual_text
        if actual_text != previous:
            logging.info("时区复核：XPath=%s；实际内容=%r；包含 %r=%s", xpath, actual_text, expected, matched)
            previous = actual_text
        if matched:
            return
        time.sleep(config["timing"]["poll_interval"])
    logging.error("时区复核超时：XPath=%s；最后读取内容=%r；期望包含=%r", xpath, actual_text, expected)
    raise RuntimeError(f"看板时区未确认为 {expected}，本轮响应不保存")


def wait_timezone_rendered(tab, config):
    """按钮初始可能先返回内部编码（例如 cbt,mla,mlb,mlu,br）。
    在出现可读的 UTC 文本前不作时区分支判断，避免误弃用首轮响应。
    """
    m, t = config["mabang"], config["timing"]
    xpath = m["timezone_button_xpath"]
    deadline = time.monotonic() + t["element_timeout"]
    previous = object()
    while time.monotonic() < deadline:
        button = tab.ele("xpath:" + xpath, timeout=0.2)
        actual_text = button.text if button else None
        if actual_text != previous:
            logging.info("等待时区按钮渲染：XPath=%s；实际内容=%r；包含 'UTC'=%s",
                         xpath, actual_text, bool(actual_text and "UTC" in actual_text))
            previous = actual_text
        if actual_text and "UTC" in actual_text:
            return button, actual_text
        time.sleep(t["poll_interval"])
    logging.error("时区按钮渲染超时：XPath=%s；最后内容=%r；需要出现 'UTC'", xpath, actual_text)
    raise RuntimeError("时区选择按钮尚未渲染出 UTC 文本")


def select_timezone(tab, config):
    def select():
        button = tab.ele("xpath:" + config["mabang"]["timezone_button_xpath"], timeout=0.2)
        if button and config["mabang"]["timezone_text"] in button.text:
            return
        return _select_timezone(tab, config)
    return run_with_retry(config, "button", select, "巴西时区选项点击")


def _select_timezone(tab, config):
    """选项可能在可滚动列表外，或需滚动后才加载到 DOM。"""
    m, t = config["mabang"], config["timing"]
    deadline = time.monotonic() + t["element_timeout"]
    while time.monotonic() < deadline:
        option = tab.ele("xpath:" + m["timezone_option_xpath"], timeout=0.2)
        if option:
            option.scroll.to_see(center=True)
            if option.states.is_displayed:
                logging.info("准备选择时区：XPath=%s；实际内容=%r；title=%r",
                             m["timezone_option_xpath"], option.text, option.attr("title"))
                if option.click(by_js=False) is False:
                    raise RuntimeError("巴西时区选项点击失败")
                return
        for panel in tab.eles("xpath:" + m["timezone_list_xpath"], timeout=0.2):
            if panel.states.is_displayed:
                logging.info("时区选项尚不可点击，向下滚动列表 300px；XPath=%s", m["timezone_list_xpath"])
                panel.scroll.down(300)
        time.sleep(t["poll_interval"])
    raise RuntimeError("下拉列表中未找到可点击的巴西时区选项，已尝试滚动")


def trigger_dashboard(tab, config, targets):
    m, t = config["mabang"], config["timing"]
    def restart_listener():
        stop_listener(tab)
        tab.listen.start(targets, method=True, res_type=True)
        logging.info("网络监听已启动：全部资源类型/所有请求方法；URL过滤=%s；先监听再进入看板", targets)
    load_page(tab, m["dashboard_url"], config, before_attempt=restart_listener)
    button, actual_text = wait_timezone_rendered(tab, config)
    selected = m["timezone_text"] in actual_text
    logging.info("时区初次判断：XPath=%s；实际内容=%r；包含 %r=%s",
                 m["timezone_button_xpath"], actual_text, m["timezone_text"], selected)
    if selected:
        logging.info("看板已为 %s，保留进入页面前启动的监听结果，不刷新", m["timezone_text"])
    else:
        # 首次加载使用了其他时区，清空旧队列后只采集切换触发的响应。
        stop_listener(tab)
        tab.listen.start(targets, method=True, res_type=True)
        logging.info("看板时区不是 %s，弃用首次响应并重启监听后选择巴西时区", m["timezone_text"])
        click_button(tab, m["timezone_button_xpath"], config, click_kwargs={"by_js": False},
                     satisfied=lambda: any(panel.states.is_displayed for panel in
                                           tab.eles("xpath:" + m["timezone_list_xpath"], timeout=0.2)))
        select_timezone(tab, config)
    verify_timezone(tab, config)
