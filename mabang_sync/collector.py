import json
import logging
import time
from urllib.parse import urlsplit
from .dashboard import trigger_dashboard, verify_timezone, stop_listener

MODULES = ("sales-overview", "hourly", "amount-category", "manager-refund",
           "hot-product", "statistics", "order-metrics", "shop-ranking")


def validate_response(body):
    if isinstance(body, (str, bytes)):
        body = json.loads(body)
    if not isinstance(body, dict) or body.get("success") is not True or body.get("code") != 200:
        raise ValueError("接口业务状态不成功")
    if not isinstance(body.get("data"), (dict, list)):
        raise ValueError("接口 data 不是对象或数组")
    return body


def collect(tab, config, save_raw):
    """确认 UTC-3 后采集；丢弃时区未确认批次，重试补齐有效模块。"""
    m, t = config["mabang"], config["timing"]
    targets = {m["api_base"].rstrip("/") + "/" + name: name for name in MODULES}
    captured, errors = {}, {}
    for attempt in range(t["retry_count"] + 1):
        batch = {}
        try:
            trigger_dashboard(tab, config, list(targets))
            deadline = time.monotonic() + t["capture_timeout"]
            while len(captured) + len(batch) < len(MODULES) and time.monotonic() < deadline:
                packet = tab.listen.wait(timeout=min(1, max(0.01, deadline - time.monotonic())), raise_err=False)
                if not packet:
                    continue
                parsed = urlsplit(packet.url)
                name = targets.get(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
                if not name or name in captured or name in batch:
                    continue
                try:
                    if packet.is_failed or packet.response.status != 200:
                        raise ValueError("网络失败或 HTTP 非 200")
                    body = validate_response(packet.response.body)
                except (ValueError, TypeError) as exc:
                    errors[name] = str(exc)
                    continue
                batch[name] = body
                errors.pop(name, None)
                logging.info("已捕获 %s (%s/8)", name, len(captured) + len(batch))
            verify_timezone(tab, config)
            for name, body in batch.items():
                save_raw(name, body)
                captured[name] = body
        except Exception as exc:
            # 仅记录异常类型，第三方异常可能含 URL 或敏感内容。
            errors["navigation"] = type(exc).__name__
        finally:
            stop_listener(tab)
        if len(captured) == len(MODULES):
            errors.pop("navigation", None)
            break
        if attempt < t["retry_count"]:
            logging.warning("仍缺少 %s，准备重试", ", ".join(n for n in MODULES if n not in captured))
            time.sleep(t["retry_interval"])
    return captured, errors
