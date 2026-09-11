"""纯函数清洗层，不依赖浏览器、配置或飞书 SDK。"""
from datetime import date
from decimal import Decimal, InvalidOperation
import re
from .collector import validate_response

# source: (output, type, meaning)。金额以 Decimal 校验，以十进制字符串存储。
FIELDS = {
    "currencyCode": ("currency_code", "string", "币种代码"),
    "date": ("date", "date", "接口统计日期；保留原日期，不调整时区"),
    "orderNum": ("order_count", "integer", "订单数量"),
    "sales": ("sales_amount", "decimal", "销售金额"),
    "income": ("income_amount", "decimal", "收入金额；具体收入口径待业务确认"),
    "orderRevenue": ("order_revenue", "decimal", "订单收入；与 income 的区别待确认"),
    "gross": ("gross_profit", "decimal", "毛利润（按字段名推断，口径待确认）"),
    "shippingTotal": ("shipping_total", "decimal", "运费合计（收取或支出口径待确认）"),
    "expend": ("expenditure_amount", "decimal", "支出金额（组成待确认）"),
    "itemTotal": ("item_total", "decimal", "商品合计值；金额或数量单位待确认"),
    "refund": ("refund_amount", "decimal", "退款金额；样例 notes 说明按今日退款发生时间统计"),
    "quantity": ("quantity", "integer", "数量；具体对象待确认"),
    "todayOrderNum": ("today_order_count", "integer", "今日订单数量"),
    "rank": ("rank", "integer", "接口返回的排名"),
    "shopId": ("shop_id", "string", "店铺 ID，以字符串保存"),
    "shopName": ("shop_name", "string", "店铺名称"),
    "skuNum": ("sku_count", "integer", "SKU 数量；去重口径待确认"),
}
for original, alias in (("shipping_total", "shippingTotal"), ("item_total", "itemTotal"), ("order_num", "orderNum")):
    FIELDS[original] = FIELDS[alias]
for source in ("incomeRatio", "grossRatio", "expendRatio", "refundRatio", "quantityRatio",
               "grossMargin", "grossMarginRatio", "orderNumRatio", "orderRatio"):
    output = re.sub(r"(?<!^)(?=[A-Z])", "_", source).lower()
    FIELDS[source] = (output, "ratio", "比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率")


def convert(value, kind):
    if value is None or value == "-" or value == "":
        return None
    if kind == "string":
        return str(value)
    if kind == "date":
        return date.fromisoformat(str(value)).isoformat()
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为数值")
    text = str(value).strip()
    if kind == "ratio":
        if not text.endswith("%"):
            raise ValueError("非百分号比例单位不明确")
        number = Decimal(text[:-1]) / 100
    else:
        number = Decimal(text)
    if not number.is_finite():
        raise ValueError("数值非有限")
    if kind == "integer":
        if number != number.to_integral_value() or number < 0:
            raise ValueError("数量/排名必须为非负整数")
        return int(number)
    return format(number, "f")


def clean_record(record, required, warnings):
    if not isinstance(record, dict):
        raise ValueError("记录不是对象")
    missing = set(required) - record.keys()
    if missing:
        raise ValueError("缺少字段: " + ", ".join(sorted(missing)))
    result, extra = {}, {}
    for key, value in record.items():
        if key not in FIELDS:
            extra[key] = value
            continue
        output, kind, _ = FIELDS[key]
        if output in result:
            raise ValueError(f"字段映射冲突: {output}")
        try:
            result[output] = convert(value, kind)
        except (ValueError, InvalidOperation):
            raise ValueError(f"字段类型不符合约定: {key}") from None
    if extra:
        result["extra_fields"] = extra
        warnings.append("存在未定义字段，已保留到 extra_fields")
    return result


def clean_module(name, body):
    body = validate_response(body)
    data, warnings = body["data"], []
    result = {"module": name, "source_timestamp_ms": body.get("currentTimestamp"),
              "status": "cleaned", "tables": {}, "warnings": warnings}
    try:
        if name == "sales-overview":
            trend = data["trend"]
            if not isinstance(trend["days"], list):
                raise ValueError("trend.days 必须为数组")
            rows = []
            for item in trend["days"]:
                row = clean_record(item, ("date", "orderNum", "sales"), warnings)
                row["currency_code"] = convert(trend["currencyCode"], "string")
                rows.append(row)
            result["tables"]["sales_daily"] = rows
            result["unmapped_data"] = {k: v for k, v in data.items() if k != "trend"}
            result["unmapped_trend"] = {k: v for k, v in trend.items() if k not in ("days", "currencyCode")}
            warnings.append("countrySales 样例为空，尚未定义国家销售字段")
        elif name == "statistics":
            metrics = clean_record(data["metrics"], ("income", "gross", "shipping_total", "expend", "item_total", "order_num"), warnings)
            summary = clean_record({k: v for k, v in data.items() if k not in ("metrics", "platforms", "notes")}, ("todayOrderNum", "orderRatio", "currencyCode"), warnings)
            metrics["currency_code"] = summary["currency_code"]
            result["tables"] = {"statistics_metrics": [metrics], "statistics_summary": [summary]}
            result["notes"] = data.get("notes", [])
            result["unmapped_data"] = {"platforms": data.get("platforms")}
            warnings.append("platforms 样例为空，尚未定义平台字段")
        elif name == "order-metrics":
            result["tables"]["order_metrics"] = [clean_record(data, ("orderNum", "refund", "currencyCode"), warnings)]
        elif name == "shop-ranking":
            if not isinstance(data, list):
                raise ValueError("店铺排名应为数组")
            result["tables"]["shop_ranking"] = [clean_record(row, ("rank", "shopId", "shopName", "income", "orderNum", "skuNum", "gross", "currencyCode"), warnings) for row in data]
        else:
            result["status"] = "unmapped"
            result["unmapped_data"] = data
            warnings.append("该接口样例与 sales-overview 相同，真实结构待确认；原样保留，不推断业务字段")
    except (KeyError, TypeError, ValueError) as exc:
        result["status"] = "schema_error"
        result["tables"] = {}
        result["unmapped_data"] = data
        warnings.append(f"结构校验失败: {exc}")
    return result
