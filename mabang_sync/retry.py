"""有界重试；次数均为首次执行之外的额外次数，不捕获 Ctrl+C。"""
import logging
import time
from .diagnostics import safe_text, FeishuError


class PermanentError(RuntimeError):
    """需要修改配置或人工处理的问题，不自动重试。"""


class CaptureIncompleteError(RuntimeError):
    """程序生成的缺少接口说明，可以安全输出。"""


def retryable(exc):
    return not isinstance(exc, PermanentError) and getattr(exc, "retryable", True)


def run_with_retry(config, key, operation, label=None):
    policy = config["retry"][key]
    total = policy["count"] + 1
    label = label or key
    for attempt in range(1, total + 1):
        logging.info("执行阶段=%s；第 %s/%s 次", label, attempt, total)
        try:
            result = operation()
        except Exception as exc:
            reason = safe_text(exc, config) if isinstance(exc, (CaptureIncompleteError, PermanentError, FeishuError)) else "请查看该阶段前面的诊断日志（第三方异常内容不直接输出）"
            logging.warning("阶段=%s；失败原因=%s", label, reason)
            # 第三方异常可能附带密码、Cookie 或请求正文，仅输出类型。
            if not retryable(exc) or attempt == total:
                logging.error("阶段=%s 失败；异常=%s；%s", label, type(exc).__name__,
                              "不适合自动重试" if not retryable(exc) else "重试次数已用尽")
                raise
            logging.warning("阶段=%s 失败；异常=%s；等待 %s 秒后重试（下次 %s/%s）",
                            label, type(exc).__name__, policy["interval"], attempt + 1, total)
            time.sleep(policy["interval"])
        else:
            if attempt > 1:
                logging.info("阶段=%s 重试成功；第 %s/%s 次", label, attempt, total)
            return result


def load_page(tab, url, config, before_attempt=None):
    def navigate():
        if before_attempt:
            before_attempt()
        # 关闭 DrissionPage 内置重试，由本项目统一计数。False 也算失败。
        if tab.get(url, timeout=config["timing"]["page_timeout"], retry=0, show_errmsg=True) is False:
            raise RuntimeError("页面加载返回 False")
    return run_with_retry(config, "page", navigate, "页面加载")


def click_button(tab, xpath, config, *, satisfied=None, click_kwargs=None):
    attempted = False
    def click():
        nonlocal attempted
        # 上次可能已经生效，先验证再决定是否重放点击。
        if attempted and satisfied and satisfied():
            return
        attempted = True
        element = tab.ele("xpath:" + xpath, timeout=config["timing"]["element_timeout"])
        if not element or not element.states.is_displayed:
            raise RuntimeError("按钮不存在或不可见")
        if element.click(**(click_kwargs or {})) is False:
            raise RuntimeError("按钮点击返回 False")
    return run_with_retry(config, "button", click, "按钮点击 " + xpath)
