"""将已清洗的长表转换为六张飞书宽表；纯数据转换，不联网。"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from .cleaners import clean_module

TABLE_NAMES = {"products": "每日商品榜单", "shops": "每日店铺表现", "managers": "每日管理员表现",
               "platforms": "每日平台销售", "summary": "每日经营汇总", "hourly": "每日每时销售额"}
UPDATED_AT_FIELD = "更新时间(巴西)"
RANK_FIELDS = {
    "products": {"商品名": "name", "销售额": "income_amount", "销量": "quantity"},
    "shops": {"店铺名": "shop_name", "销售额": "income_amount", "订单数": "order_count", "销量原值": "sku_count", "毛利润": "gross_profit"},
    "managers": {"姓名": "employee_name", "销售额": "income_amount", "订单数": "order_count"},
}
SUMMARY_FIELDS = {"订单收入": "order_revenue", "订单数": "order_count", "销量": "quantity", "毛利润": "gross_profit",
                  "支出": "expenditure_amount", "退款金额": "refund_amount", "毛利率": "gross_margin",
                  "订单收入变化比例": "income_ratio", "订单数变化比例": "order_num_ratio", "销量变化比例": "quantity_ratio",
                  "毛利润变化比例": "gross_ratio", "支出变化比例": "expend_ratio", "退款金额变化比例": "refund_ratio",
                  "毛利率变化值": "gross_margin_ratio"}


def table_schema():
    # 飞书类型：1 文本、2 数字、5 日期；比例字段使用数字百分比显示。
    schema = {key: {"日期": 5, UPDATED_AT_FIELD: 5, "数据说明": 1} for key in TABLE_NAMES}
    for key, mapping in RANK_FIELDS.items():
        for rank in range(1, 6):
            for label in mapping:
                schema[key][f"排名{rank}-{label}"] = 1 if label in ("商品名", "店铺名", "姓名") else 2
    for platform in ("虾皮", "美客多"):
        schema["platforms"].update({f"{platform}-{label}": 2 for label in ("销售额", "订单数", "排名")})
    schema["summary"].update({key: 2 for key in (*SUMMARY_FIELDS, "销售额", "销售额变化比例", "退款率")})
    schema["hourly"].update({f"{hour}时": 2 for hour in range(24)})
    return schema


def number(value):
    if value is None:
        return None
    n = Decimal(str(value))
    if not n.is_finite():
        raise ValueError("金额或比例不是有限数值")
    return int(n) if n == n.to_integral_value() else float(n)


def build_plan(captured, config, business_date=None):
    sync = config["feishu"]["sync"]
    zone = ZoneInfo(sync["business_timezone"])
    timestamps = [b.get("currentTimestamp") for b in captured.values()]
    if not timestamps or any(type(ts) is not int or ts <= 0 for ts in timestamps):
        raise ValueError("缺少有效接口时间戳，无法生成按日写入计划")
    dates = {datetime.fromtimestamp(ts / 1000, zone).date() for ts in timestamps}
    if len(dates) != 1:
        raise ValueError("响应跨统计日，请重新采集，避免 today/yesterday 归错日期")
    today = date.fromisoformat(business_date) if business_date else dates.pop()
    source_dates = {datetime.fromtimestamp(ts / 1000, ZoneInfo(sync["source_timezone"])).date() for ts in timestamps}
    if len(source_dates) != 1:
        raise ValueError("响应跨看板统计日，请重新采集")
    source_today = source_dates.pop()
    date_shift = today - source_today
    yesterday = today - timedelta(days=1)
    stamp = max(timestamps)
    cleaned = {name: clean_module(name, body) for name, body in captured.items()}
    plan = {"business_date": today.isoformat(), "source_timestamp_ms": stamp,
            "timezone": sync["timezone"], "business_timezone": sync["business_timezone"],
            "source_timezone": sync["source_timezone"], "source_business_date": source_today.isoformat(),
            "scope": sync["scope"], "currency": sync["currency"],
            "records": [], "errors": {}, "warnings": []}

    def tables(module):
        value = cleaned.get(module)
        if not value or value["status"] != "cleaned":
            raise ValueError(f"{module} 缺失或结构校验失败")
        # 币种只校验已清洗字段；hot-product 顶层币种 null 不覆盖行级币种。
        for rows in value["tables"].values():
            for row in rows:
                if row.get("currency_code") not in (None, sync["currency"]):
                    raise ValueError(f"{module} 币种不符合配置")
        root_currency = captured[module]["data"].get("currencyCode") if isinstance(captured[module]["data"], dict) else None
        if root_currency not in (None, sync["currency"]):
            raise ValueError(f"{module} 币种不符合配置")
        return value["tables"]

    def add(key, day, fields, note, historical=False):
        plan["records"].append({"table": key, "date": day.isoformat(), "fields": fields,
                                "note": note, "historical": historical})

    ranking_sources = {"products": ("hot-product", "income_ranking", "stock_id"),
                       "shops": ("shop-ranking", "shop_ranking", "shop_id"),
                       "managers": ("manager-refund", "managers", "manager_id")}
    for key, (module, table, identity) in ranking_sources.items():
        try:
            rows = tables(module)[table]
            ranks, ids = set(), set()
            fields = {f"排名{i}-{label}": None for i in range(1, 6) for label in RANK_FIELDS[key]}
            for row in rows:
                rank = row["rank"]
                if type(rank) is not int or rank < 1 or rank in ranks or not row[identity] or row[identity] in ids:
                    raise ValueError("排名或对象 ID 缺失、重复或非法")
                ranks.add(rank)
                ids.add(row[identity])
                if rank > 5:
                    continue
                for label, source in RANK_FIELDS[key].items():
                    fields[f"排名{rank}-{label}"] = row[source] if label in ("商品名", "店铺名", "姓名") else number(row[source])
            add(key, today, fields, "当日累计；销售额榜前五名")
        except (KeyError, ValueError) as exc:
            plan["errors"][key] = str(exc)

    try:
        rows = tables("statistics")["platforms"]
        fields = {f"{name}-{label}": None for name in ("虾皮", "美客多") for label in ("销售额", "订单数", "排名")}
        seen = set()
        for row in rows:
            identity = row["platform_id"]
            if identity in seen:
                raise ValueError("平台 ID 重复")
            seen.add(identity)
            name = {"17": "虾皮", "42": "美客多"}.get(identity)
            if not name:
                plan["warnings"].append(f"平台 {identity} 未配置飞书列，仅保留本地")
                continue
            for label, source in (("销售额", "sales_amount"), ("订单数", "order_count"), ("排名", "rank")):
                fields[f"{name}-{label}"] = number(row[source])
        add("platforms", today, fields, "当日累计；固定平台 ID 映射；未返回平台留空")
    except (KeyError, ValueError) as exc:
        plan["errors"]["platforms"] = str(exc)

    history = {}
    try:
        amount = tables("amount-category")["sales_amount"][0]
        metrics = tables("order-metrics")["order_metrics"][0]
        refund = tables("manager-refund")["refund_rate"][0]
        fields = {label: number(metrics[source]) for label, source in SUMMARY_FIELDS.items()}
        fields.update({"销售额": number(amount["income_amount"]), "销售额变化比例": number(amount["ratio"]), "退款率": number(refund["refund_rate"])})
        add("summary", today, fields, "当日累计；统计口径见原始 notes")
    except (KeyError, ValueError) as exc:
        plan["errors"]["summary"] = str(exc)

    # 历史回补独立于今日其他经营字段是否可用。明确昨日/前日值覆盖趋势值。
    try:
        if sync["backfill_sales_trend"]:
            for row in tables("sales-overview")["sales_daily"]:
                # 历史源日期与本批 today/yesterday 采用一致的北京时间行标签。
                day = date.fromisoformat(row["date"]) + date_shift
                if day < today:
                    if day in history:
                        raise ValueError("历史趋势日期重复")
                    history[day] = {"销售额": number(row["sales_amount"]), "订单数": number(row["order_count"])}
        for enabled, key, offset in ((sync["compare_yesterday"], "yesterday_income", 1),
                                     (sync["correct_day_before_yesterday"], "day_before_yesterday_income", 2)):
            if enabled:
                amount = tables("amount-category")["sales_amount"][0]
                value = amount.get(key)
                if value is None:
                    plan["warnings"].append(f"{key} 无有效值，跳过该金额纠正")
                    continue
                day = today - timedelta(days=offset)
                value = number(value)
                if day in history and history[day]["销售额"] != value:
                    plan["warnings"].append(f"{day} 销售额来源冲突，采用 amount 的明确历史值")
                history.setdefault(day, {})["销售额"] = value
        for day, fields in sorted(history.items()):
            # 不用 null 擦除已有历史指标。
            fields = {k: v for k, v in fields.items() if v is not None}
            if fields:
                add("summary", day, fields, "历史回补/校正字段：" + "、".join(fields), True)
    except (KeyError, ValueError) as exc:
        plan["errors"]["summary_history"] = str(exc)

    try:
        rows = tables("hourly")["hourly_sales"]
        for period, day in (("today", today), ("yesterday", yesterday)):
            if period == "yesterday" and not sync["compare_yesterday"]:
                continue
            fields = {f"{row['hour']}时": number(row[period]) for row in rows}
            if period == "yesterday":
                fields = {k: v for k, v in fields.items() if v is not None}
            if fields:
                add("hourly", day, fields, "昨日分时销售校正" if period == "yesterday" else "当日累计；未发生时段的零不是最终值", period == "yesterday")
    except (KeyError, ValueError) as exc:
        plan["errors"]["hourly"] = str(exc)
    return plan
