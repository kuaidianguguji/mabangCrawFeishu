import json
import logging
import time
from urllib.parse import urlsplit
from .dashboard import trigger_dashboard, verify_timezone, stop_listener
from .diagnostics import safe_text

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


def response_summary(body):
    """仅记录校验字段的有限元信息，不输出业务数据、请求头或 Cookie。"""
    def scalar(value):
        return value if value is None or type(value) in (bool, int, float) else f"<{type(value).__name__}>"
    if not isinstance(body, dict):
        return {"body_type": type(body).__name__}
    return {"body_type": "dict", "success": scalar(body.get("success")),
            "code": scalar(body.get("code")), "data_type": type(body.get("data")).__name__}


def endpoint_label(url, config):
    parsed = urlsplit(url)
    # 不含查询参数、fragment 或 userinfo。
    authority = parsed.netloc.rsplit("@", 1)[-1]
    return safe_text(f"{parsed.scheme}://{authority}{parsed.path}", config)


def collect(tab, config, save_raw, save_diagnostics=None):
    """确认 UTC-3 后采集；丢弃时区未确认批次，重试补齐有效模块。"""
    m, t = config["mabang"], config["timing"]
    expected_base = urlsplit(m["api_base"])
    paths = {expected_base.path.rstrip("/") + "/" + name: name for name in MODULES}
    captured, errors = {}, {}
    diagnostics = {"expected_api_base": endpoint_label(m["api_base"], config),
                   "listener": "current tab; all resource types; all methods", "rounds": []}
    for attempt in range(t["retry_count"] + 1):
        batch = {}
        stats = {"round": attempt + 1, "xhr_fetch_packets": 0, "module_packets": 0,
                 "accepted": 0, "rejected": 0, "duplicates": 0, "preflight": 0,
                 "other_endpoints": [], "rejections": [], "timezone_verified": False}
        diagnostics["rounds"].append(stats)
        try:
            # 不用固定完整域名过滤：不同站点/网关可能使用不同域名与前缀。
            # 先观察当前标签页全部 XHR/Fetch，再根据配置路径或 /modules/<模块> 识别。
            trigger_dashboard(tab, config, True)
            deadline = time.monotonic() + t["capture_timeout"]
            next_notice = time.monotonic() + 10
            while len(captured) + len(batch) < len(MODULES) and time.monotonic() < deadline:
                if time.monotonic() >= next_notice:
                    logging.info("监听等待中：XHR/Fetch包=%s；目标模块包=%s；有效=%s/8；拒绝=%s；还需等待最多 %.0f 秒",
                                 stats["xhr_fetch_packets"], stats["module_packets"], len(captured) + len(batch), stats["rejected"],
                                 max(0, deadline - time.monotonic()))
                    next_notice = time.monotonic() + 10
                packet = tab.listen.wait(timeout=min(1, max(0.01, deadline - time.monotonic())), raise_err=False)
                if not packet:
                    continue
                stats["xhr_fetch_packets"] += 1
                parsed = urlsplit(packet.url)
                path = parsed.path.rstrip("/")
                name = paths.get(path)
                if not name:
                    name = next((n for n in MODULES if path.endswith("/modules/" + n)), None)
                if not name:
                    endpoint = endpoint_label(packet.url, config)
                    if endpoint not in stats["other_endpoints"] and len(stats["other_endpoints"]) < 10:
                        stats["other_endpoints"].append(endpoint)
                        logging.info("监听到非目标 XHR/Fetch：%s（不保存响应正文）", endpoint)
                    continue
                stats["module_packets"] += 1
                method = getattr(getattr(packet, "request", None), "method", "unknown")
                if method == "OPTIONS":
                    stats["preflight"] += 1
                    continue
                if name in captured or name in batch:
                    stats["duplicates"] += 1
                    continue
                endpoint = endpoint_label(packet.url, config)
                if parsed.netloc != expected_base.netloc or path not in paths:
                    logging.warning("接口地址与配置不同，按模块路径识别：模块=%s；实际地址=%s", name, endpoint)
                metadata = {}
                try:
                    if packet.is_failed:
                        raise ValueError("请求网络失败，未取得响应")
                    metadata["http_status"] = packet.response.status
                    if packet.response.status != 200:
                        raise ValueError("网络失败或 HTTP 非 200")
                    body = packet.response.body
                    if isinstance(body, (str, bytes)):
                        body = json.loads(body)
                    metadata.update(response_summary(body))
                    body = validate_response(body)
                except Exception as exc:
                    stats["rejected"] += 1
                    # JSON/第三方错误可能包含响应内容，原因只输出明确的校验错误或异常类名。
                    reason = str(exc) if type(exc) is ValueError else type(exc).__name__
                    errors[name] = safe_text(reason, config)
                    detail = {"module": name, "endpoint": endpoint, "reason": errors[name], **metadata}
                    if len(stats["rejections"]) < 30:
                        stats["rejections"].append(detail)
                    logging.warning("已收到目标响应但校验拒绝：%s", detail)
                    continue
                batch[name] = body
                stats["accepted"] += 1
                errors.pop(name, None)
                logging.info("已捕获 %s (%s/8)；实际地址=%s；HTTP=200；JSON校验通过", name, len(captured) + len(batch), endpoint)
            verify_timezone(tab, config)
            stats["timezone_verified"] = True
            for name, body in batch.items():
                save_raw(name, body)
                captured[name] = body
        except Exception as exc:
            # 仅记录异常类型，第三方异常可能含 URL 或敏感内容。
            errors["navigation"] = type(exc).__name__
            stats["exception_type"] = type(exc).__name__
            logging.warning("采集轮次 %s/%s 失败：%s", attempt + 1, t["retry_count"] + 1, type(exc).__name__)
        finally:
            stats["missing"] = [n for n in MODULES if n not in captured]
            logging.info("本轮监听汇总：XHR/Fetch=%s；目标模块包=%s；有效=%s；拒绝=%s；重复=%s；OPTIONS=%s；时区复核=%s",
                         stats["xhr_fetch_packets"], stats["module_packets"], stats["accepted"], stats["rejected"],
                         stats["duplicates"], stats["preflight"], stats["timezone_verified"])
            if not stats["module_packets"]:
                logging.warning("本轮没有监听到目标模块响应；当前监听标签页=%s；页面=%s；检查 Network 中请求的标签页/iframe/实际路径与本日志是否一致",
                                getattr(tab, "tab_id", "unknown"), endpoint_label(str(getattr(tab, "url", "")), config))
            try:
                stop_listener(tab)
            except Exception as exc:
                logging.warning("停止监听失败：%s；后续整体重试将重建浏览器", type(exc).__name__)
            if save_diagnostics:
                save_diagnostics(diagnostics)
        if len(captured) == len(MODULES):
            errors.pop("navigation", None)
            break
        if attempt < t["retry_count"]:
            logging.warning("仍缺少 %s，准备重试", ", ".join(n for n in MODULES if n not in captured))
            time.sleep(t["retry_interval"])
    return captured, errors
