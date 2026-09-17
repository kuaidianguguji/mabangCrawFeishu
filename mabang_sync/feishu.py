"""飞书 API 适配器与幂等日记录更新。不会自动删除/重命名已有字段。"""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import requests
import logging
from time import monotonic
from difflib import get_close_matches
import hashlib
import json
from uuid import UUID
from .retry import run_with_retry
from .feishu_plan import TABLE_NAMES, table_schema, UPDATED_AT_FIELD
from .diagnostics import FeishuError, safe_text


class FeishuClient:
    def __init__(self, config):
        self.retry_config = config
        self.config = config["feishu"]
        for key in ("app_id", "app_secret", "app_token"):
            if not self.config[key]:
                raise FeishuError(f"请配置 feishu.{key}")
        ids = list(self.config["tables"].values())
        if not all(ids) or len(set(ids)) != len(TABLE_NAMES):
            raise FeishuError("请配置六个互不相同的飞书 table_id")
        self.session = requests.Session()
        self.token = None
        self.request_number = 0

    def request(self, method, path, **kwargs):
        return run_with_retry(self.retry_config, "feishu",
                              lambda: self._request_once(method, path, **kwargs), "飞书请求 " + method)

    def _request_once(self, method, path, **kwargs):
        self.request_number += 1
        request_number = self.request_number
        endpoint = safe_text(path, self.config, (self.token,))
        started = monotonic()
        logging.info("飞书请求 #%s：%s %s", request_number, method, endpoint)
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        status = None
        try:
            res = self.session.request(method, "https://open.feishu.cn/open-apis/" + path,
                                       headers=headers, timeout=self.config["timeout"], **kwargs)
            status = res.status_code
            res.raise_for_status()
            payload = res.json()
        except (requests.RequestException, ValueError) as exc:
            transient = status is None or status in (408, 429) or status >= 500 or isinstance(exc, ValueError)
            message = f"飞书请求 #{request_number} 失败：HTTP={status}，异常={type(exc).__name__}；可重试={transient}"
            logging.error("%s；耗时=%.2f秒", message, monotonic() - started)
            raise FeishuError(message, retryable=transient) from None
        if not isinstance(payload, dict):
            raise FeishuError(f"飞书请求 #{request_number} 返回的 JSON 不是对象", retryable=True)
        logging.info("飞书响应 #%s：HTTP=%s；code=%s；耗时=%.2f秒", request_number, status, payload.get("code"), monotonic() - started)
        if payload.get("code") != 0:
            detail = safe_text(payload.get("msg", ""), self.config, (self.token,))
            raise FeishuError(f"飞书 API 返回错误码 {payload.get('code')}：{detail}；请求 #{request_number}",
                              retryable=payload.get("code") in (99991400, 1254290, 1254291, 1255001, 1255040))
        return payload

    def authenticate(self):
        logging.info("飞书鉴权开始：申请自建应用访问凭证（不输出密钥或令牌）")
        self.token = self.request("POST", "auth/v3/tenant_access_token/internal", json={
            "app_id": self.config["app_id"], "app_secret": self.config["app_secret"]})["tenant_access_token"]
        logging.info("飞书鉴权成功")

    def path(self, table, suffix):
        return f"bitable/v1/apps/{self.config['app_token']}/tables/{self.config['tables'][table]}/{suffix}"

    def items(self, table, suffix):
        items, cursor = [], None
        page = 0
        while True:
            params = {"page_size": 100}
            if cursor:
                params["page_token"] = cursor
            data = self.request("GET", self.path(table, suffix), params=params)["data"]
            batch = data.get("items") or []
            items.extend(batch)
            page += 1
            logging.info("飞书读取：表=%s；内容=%s；第%s页=%s条；累计=%s条；还有下一页=%s",
                         TABLE_NAMES[table], "字段" if suffix == "fields" else "记录", page, len(batch), len(items), bool(data.get("has_more")))
            if not data.get("has_more"):
                return items
            next_cursor = data.get("page_token")
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("飞书分页游标异常")
            cursor = next_cursor

    def fields(self, table):
        return self.items(table, "fields")

    def records(self, table):
        return self.items(table, "records")

    def create(self, table, fields):
        path = self.path(table, "records")
        # 同一目标与完整载荷固定为同一 UUID，HTTP 重试、整体重试、离线重跑均复用。
        # 官方 SDK 的新增记录接口将 client_token 放在查询参数中。
        encoded = json.dumps([path, fields], sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
        token = str(UUID(bytes=hashlib.sha256(encoded).digest()[:16], version=4))
        return self.request("POST", path, params={"client_token": token}, json={"fields": fields})["data"]["record"]

    def update(self, table, record_id, fields):
        return self.request("PUT", self.path(table, "records/" + record_id), json={"fields": fields})["data"]["record"]


def scalar(value):
    if isinstance(value, list) and all(isinstance(part, dict) and "text" in part for part in value):
        return "".join(part["text"] for part in value)
    return value


def equal_value(old, new):
    old = scalar(old)
    if new is None:
        return old in (None, "", [])
    if isinstance(new, (float, int)) and not isinstance(new, bool):
        try:
            return Decimal(str(old)) == Decimal(str(new))
        except InvalidOperation:
            return False
    return old == new


def timestamp_ms(value, source_timezone):
    """兼容日期字段的毫秒值，以及文本字段的带时区 ISO 时间。"""
    value = scalar(value)
    if value in (None, "", []):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
            return int(parsed.timestamp() * 1000)
        except (ValueError, TypeError):
            raise ValueError("更新时间字段既不是毫秒时间戳，也不是有效 ISO 日期时间") from None


def sync_plan(client, plan, config, progress=None, check_only=False):
    """先读完所有相关表并验证，再写入；按日期定位，不修改未传入字段。"""
    progress = progress or (lambda report: None)
    zone = ZoneInfo(plan["timezone"])
    report = {"complete": False, "mode": "check" if check_only else "write", "phase": "authentication", "actions": [], "plan_errors": plan["errors"]}
    indexes, field_maps, updated_fields = {}, {}, {}
    try:
        logging.info("飞书%s开始：北京时间行日期=%s；源数据日期=%s；计划记录=%s条",
                     "只读检查" if check_only else "同步", plan.get("business_date"), plan.get("source_business_date"), len(plan["records"]))
        client.authenticate()
        report["phase"] = "preflight"
        for table in sorted({row["table"] for row in plan["records"]}):
            report["table"] = TABLE_NAMES[table]
            logging.info("飞书预检开始：表=%s", TABLE_NAMES[table])
            fields = {f["field_name"]: f["type"] for f in client.fields(table)}
            expected = table_schema()[table]
            mapping = {}
            update_name = UPDATED_AT_FIELD if UPDATED_AT_FIELD in fields else "更新时间"
            if fields.get(update_name) in (1, 5):
                mapping[UPDATED_AT_FIELD] = update_name
                updated_fields[table] = (update_name, fields[update_name])
            for key, kind in expected.items():
                if kind == 2 and fields.get(key) == 20:
                    mapping[key] = config["feishu"].get("formula_source_prefix", "马帮-") + key
            if mapping:
                logging.info("飞书字段映射：表=%s；%s", TABLE_NAMES[table], safe_text(mapping, config))
            bad = [f"{mapping.get(key, key)}(需类型{kind})" for key, kind in expected.items()
                   if fields.get(mapping.get(key, key)) != kind and not (key == UPDATED_AT_FIELD and table in updated_fields)]
            if bad:
                for key, kind in expected.items():
                    target = mapping.get(key, key)
                    if fields.get(target) == kind or (key == UPDATED_AT_FIELD and table in updated_fields):
                        continue
                    logging.error("飞书字段不匹配：表=%s；期望字段=%r；期望类型=%s；实际类型=%s；相近字段=%s",
                                  TABLE_NAMES[table], safe_text(target, config), kind, fields.get(target, "字段不存在"),
                                  safe_text(get_close_matches(target, fields, n=3, cutoff=0.5), config))
                logging.error("飞书实际字段清单：表=%s；%s", TABLE_NAMES[table], safe_text(fields, config))
                raise ValueError(f"{TABLE_NAMES[table]} 缺少字段或字段类型不符：" + "、".join(bad))
            logging.info("飞书字段预检通过：表=%s；期望=%s个；实际=%s个", TABLE_NAMES[table], len(expected), len(fields))
            field_maps[table] = mapping
            index = {}
            for record in client.records(table):
                value = record.get("fields", {}).get("日期")
                if value is None:
                    continue
                try:
                    day = datetime.fromtimestamp(int(value) / 1000, zone).date().isoformat()
                except (TypeError, ValueError, OverflowError):
                    raise ValueError(f"{TABLE_NAMES[table]} 存在非法日期") from None
                if day in index:
                    raise ValueError(f"{TABLE_NAMES[table]} 日期 {day} 存在重复记录，请先合并")
                index[day] = record
            indexes[table] = index
            logging.info("飞书日期索引：表=%s；有效日期=%s个；最早=%s；最新=%s",
                         TABLE_NAMES[table], len(index), min(index, default="无"), max(index, default="无"))

        report["phase"] = "compare" if check_only else "write"
        for row in plan["records"]:
            table, day = row["table"], row["date"]
            existing = indexes[table].get(day)
            current = existing.get("fields", {}) if existing else {}
            action = {"table": TABLE_NAMES[table], "date": day}
            report.update(table=TABLE_NAMES[table], date=day)
            logging.info("飞书日期匹配：表=%s；日期=%s；匹配记录=%s；历史纠正=%s",
                         TABLE_NAMES[table], day, existing["record_id"] if existing else "无，将新增", row["historical"])
            # 更新时间使用源快照时间，禁止较旧离线文件回滚较新数据。
            update_name, update_type = updated_fields[table]
            source_timezone = plan.get("source_timezone", config["feishu"]["sync"]["source_timezone"])
            previous_time = timestamp_ms(current.get(update_name), source_timezone)
            if previous_time is not None and previous_time > plan["source_timestamp_ms"]:
                action["action"] = "skip_older_snapshot"
                logging.warning("飞书跳过旧快照：表=%s；日期=%s；线上时间戳=%s > 本批时间戳=%s",
                                TABLE_NAMES[table], day, previous_time, plan["source_timestamp_ms"])
                report["actions"].append(action)
                progress(report)
                continue
            incoming = {field_maps[table].get(k, k): v for k, v in row["fields"].items()}
            delta = {k: v for k, v in incoming.items() if not equal_value(current.get(k), v)}
            logging.info("飞书数据比较：表=%s；日期=%s；比较字段=%s个；差异=%s个；变化字段=%s",
                         TABLE_NAMES[table], day, len(incoming), len(delta), safe_text(list(delta), config))
            if existing and not delta:
                action["action"] = "unchanged"
                logging.info("飞书无需更新：表=%s；日期=%s；业务字段全部一致，更新时间也不改", TABLE_NAMES[table], day)
            elif check_only:
                action.update(action="would_update" if existing else "would_create", changed_fields=list(delta))
                logging.info("飞书只读检查：表=%s；日期=%s；预计%s，不发送写入请求", TABLE_NAMES[table], day, "更新" if existing else "新增")
            else:
                # 新行保留所有字段；更新只传业务差异及元数据，不擦除历史其他指标。
                fields = delta if existing else dict(incoming)
                note = row["note"]
                old_note = scalar(current.get("数据说明"))
                if existing and row["historical"] and old_note and note not in old_note:
                    note = old_note + "；" + note
                update_value = plan["source_timestamp_ms"]
                if update_type == 1:
                    update_value = datetime.fromtimestamp(update_value / 1000, ZoneInfo(source_timezone)).isoformat(timespec="milliseconds")
                fields.update({update_name: update_value, "数据说明": note})
                action.update({"action": "update" if existing else "create", "changed_fields": list(delta), "status": "pending"})
                report["actions"].append(action)
                progress(report)
                logging.info("飞书准备%s：表=%s；日期=%s；提交字段=%s",
                             "更新" if existing else "新增", TABLE_NAMES[table], day, safe_text(list(fields), config))
                if existing:
                    client.update(table, existing["record_id"], fields)
                    existing["fields"].update(fields)
                else:
                    fields["日期"] = int(datetime.combine(date.fromisoformat(day), time(), zone).timestamp() * 1000)
                    record = client.create(table, fields)
                    indexes[table][day] = {"record_id": record["record_id"], "fields": fields}
                action["status"] = "success"
                action["record_id"] = indexes[table][day]["record_id"]
                logging.info("飞书%s成功：表=%s；日期=%s；record_id=%s",
                             "更新" if existing else "新增", TABLE_NAMES[table], day, action["record_id"])
                progress(report)
                continue
            report["actions"].append(action)
            progress(report)
        report["complete"] = not bool(plan["errors"])
        report["phase"] = "finished"
        logging.info("飞书处理结束：完整=%s；动作数=%s；报告模式=%s", report["complete"], len(report["actions"]), report["mode"])
    except (RuntimeError, ValueError) as exc:
        report["error"] = safe_text(exc, config)
        logging.error("飞书处理失败：阶段=%s；表=%s；日期=%s；原因=%s；已成功写入=%s条",
                      report["phase"], report.get("table", "未选择"), report.get("date", "未进入写入"), report["error"],
                      sum(a.get("status") == "success" for a in report["actions"]))
        progress(report)
        raise FeishuError(report["error"], retryable=getattr(exc, "retryable", isinstance(exc, RuntimeError))) from None
    progress(report)
    return report
