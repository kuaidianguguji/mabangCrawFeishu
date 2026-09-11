"""飞书 API 适配器与幂等日记录更新。不会自动删除/重命名已有字段。"""
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
import requests
from .feishu_plan import TABLE_NAMES, table_schema


class FeishuClient:
    def __init__(self, config):
        self.config = config["feishu"]
        for key in ("app_id", "app_secret", "app_token"):
            if not self.config[key]:
                raise ValueError(f"请配置 feishu.{key}")
        ids = list(self.config["tables"].values())
        if not all(ids) or len(set(ids)) != len(TABLE_NAMES):
            raise ValueError("请配置六个互不相同的飞书 table_id")
        self.session = requests.Session()
        self.token = None

    def request(self, method, path, **kwargs):
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            res = self.session.request(method, "https://open.feishu.cn/open-apis/" + path,
                                       headers=headers, timeout=self.config["timeout"], **kwargs)
            res.raise_for_status()
            payload = res.json()
        except (requests.RequestException, ValueError) as exc:
            # 不输出响应全文、凭证或请求 URL。写入超时不自动重试，避免重复创建。
            raise RuntimeError(f"飞书请求失败或结果未知（{type(exc).__name__}）；重新同步前将重新读取记录") from None
        if payload.get("code") != 0:
            raise RuntimeError(f"飞书 API 返回错误码 {payload.get('code')}，请检查权限和字段配置")
        return payload

    def authenticate(self):
        self.token = self.request("POST", "auth/v3/tenant_access_token/internal", json={
            "app_id": self.config["app_id"], "app_secret": self.config["app_secret"]})["tenant_access_token"]

    def path(self, table, suffix):
        return f"bitable/v1/apps/{self.config['app_token']}/tables/{self.config['tables'][table]}/{suffix}"

    def items(self, table, suffix):
        items, cursor = [], None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["page_token"] = cursor
            data = self.request("GET", self.path(table, suffix), params=params)["data"]
            items.extend(data.get("items") or [])
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
        return self.request("POST", self.path(table, "records"), json={"fields": fields})["data"]["record"]

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


def sync_plan(client, plan, config, progress=None):
    """先读完所有相关表并验证，再写入；按日期定位，不修改未传入字段。"""
    progress = progress or (lambda report: None)
    zone = ZoneInfo(plan["timezone"])
    report = {"complete": False, "actions": [], "plan_errors": plan["errors"]}
    indexes, field_maps = {}, {}
    try:
        client.authenticate()
        for table in sorted({row["table"] for row in plan["records"]}):
            fields = {f["field_name"]: f["type"] for f in client.fields(table)}
            expected = table_schema()[table]
            mapping = {}
            for key, kind in expected.items():
                if kind == 2 and fields.get(key) == 20:
                    mapping[key] = config["feishu"].get("formula_source_prefix", "马帮-") + key
            bad = [f"{mapping.get(key, key)}(需类型{kind})" for key, kind in expected.items()
                   if fields.get(mapping.get(key, key)) != kind]
            if bad:
                raise ValueError(f"{TABLE_NAMES[table]} 缺少字段或字段类型不符：" + "、".join(bad))
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

        for row in plan["records"]:
            table, day = row["table"], row["date"]
            existing = indexes[table].get(day)
            current = existing.get("fields", {}) if existing else {}
            action = {"table": TABLE_NAMES[table], "date": day}
            # 更新时间使用源快照时间，禁止较旧离线文件回滚较新数据。
            previous_time = current.get("更新时间")
            if previous_time is not None and int(previous_time) > plan["source_timestamp_ms"]:
                action["action"] = "skip_older_snapshot"
                report["actions"].append(action)
                progress(report)
                continue
            incoming = {field_maps[table].get(k, k): v for k, v in row["fields"].items()}
            delta = {k: v for k, v in incoming.items() if not equal_value(current.get(k), v)}
            if existing and not delta:
                action["action"] = "unchanged"
            else:
                # 新行保留所有字段；更新只传业务差异及元数据，不擦除历史其他指标。
                fields = delta if existing else dict(incoming)
                note = row["note"]
                old_note = scalar(current.get("数据说明"))
                if existing and row["historical"] and old_note and note not in old_note:
                    note = old_note + "；" + note
                fields.update({"更新时间": plan["source_timestamp_ms"], "数据说明": note})
                action.update({"action": "update" if existing else "create", "changed_fields": list(delta), "status": "pending"})
                report["actions"].append(action)
                progress(report)
                if existing:
                    client.update(table, existing["record_id"], fields)
                    existing["fields"].update(fields)
                else:
                    fields["日期"] = int(datetime.combine(date.fromisoformat(day), time(), zone).timestamp() * 1000)
                    record = client.create(table, fields)
                    indexes[table][day] = {"record_id": record["record_id"], "fields": fields}
                action["status"] = "success"
                progress(report)
                continue
            report["actions"].append(action)
            progress(report)
        report["complete"] = not bool(plan["errors"])
    except (RuntimeError, ValueError) as exc:
        report["error"] = str(exc)
        progress(report)
        raise
    progress(report)
    return report
