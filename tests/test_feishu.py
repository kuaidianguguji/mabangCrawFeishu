import copy
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from mabang_sync.config import load_config, merge_config
from mabang_sync.feishu_plan import build_plan, table_schema
from mabang_sync.feishu import equal_value, sync_plan

STAMP = int(datetime(2026, 9, 11, 15, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)


def sources():
    def body(data):
        return {"success": True, "code": 200, "data": data, "currentTimestamp": STAMP}
    metrics = dict(income=None, orderRevenue=777, gross=443, shippingTotal=32, expend=396,
                   itemTotal=745, orderNum=11, refund=279, quantity=14, currencyCode="CNY")
    for field in ("incomeRatio", "grossRatio", "expendRatio", "refundRatio", "quantityRatio", "grossMargin", "grossMarginRatio", "orderNumRatio"):
        metrics[field] = "-"
    return {
        "order-metrics": body(metrics),
        "amount-category": body({"amount": {"income": 839, "currencyCode": "CNY", "ratio": "-95.1%", "yesterdayIncome": 26039, "dayBeforeYesterdayIncome": 37999, "yesterdaySameTimeIncome": 17025}, "categories": []}),
        "statistics": body({"metrics": {"income": 839, "gross": 443, "shipping_total": 32, "expend": 396, "item_total": 745, "order_num": 11}, "todayOrderNum": 11, "orderRatio": "-89.3%", "currencyCode": "CNY", "platforms": [{"platformId": "17", "platformName": "Shopee", "sales": 450, "orderNum": 6, "rank": 1}]}),
        "manager-refund": body({"managers": [{"rank": 1, "managerId": "M1", "employeeName": "测试管理员", "income": 839, "orderNum": 11}], "refundRate": {"refund": 279, "orderIncome": 777, "refundRate": "36%", "currencyCode": "CNY"}}),
        "hot-product": body({"incomeRanking": [{"rank": 1, "stockId": "P1", "name": "商品一", "income": 207, "quantity": 3}], "quantityRanking": [{"rank": 1, "stockId": "P2", "name": "商品二", "quantity": 4}]}),
        "shop-ranking": body([{ "rank": 1, "shopId": "S1", "shopName": "测试店铺", "income": 251, "orderNum": 3, "skuNum": 3, "gross": 146.484, "currencyCode": "CNY"}]),
        "hourly": body({"currencyCode": "CNY", "sales": [{"hour": h, "today": 0, "yesterday": 304 if h == 8 else 0} for h in range(24)], "orders": [{"hour": h, "today": 0, "yesterday": 1} for h in range(24)]}),
        "sales-overview": body({"trend": {"currencyCode": "CNY", "days": [{"date": "2026-09-10", "sales": 26000, "orderNum": 164}]}, "countrySales": []}),
    }


class FakeClient:
    def __init__(self):
        self.data = {key: [] for key in table_schema()}
        self.writes = []

    def authenticate(self):
        pass

    def fields(self, table):
        return [{"field_name": k, "type": v} for k, v in table_schema()[table].items()]

    def records(self, table):
        return copy.deepcopy(self.data[table])

    def create(self, table, fields):
        self.writes.append((table, "create", copy.deepcopy(fields)))
        row = {"record_id": str(len(self.data[table])), "fields": copy.deepcopy(fields)}
        self.data[table].append(row)
        return row

    def update(self, table, identity, fields):
        self.writes.append((table, "update", copy.deepcopy(fields)))
        row = next(r for r in self.data[table] if r["record_id"] == identity)
        row["fields"].update(fields)
        return row


class FeishuTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(Path("config.example.toml"))
        self.raw = sources()

    def test_six_daily_rows_and_product_income_ranking(self):
        plan = build_plan(self.raw, self.config)
        self.assertEqual(plan["errors"], {})
        self.assertEqual(len(plan["records"]), 6)
        row = next(r for r in plan["records"] if r["table"] == "products")
        self.assertEqual(row["fields"]["排名1-商品名"], "商品一")
        self.assertIsNone(row["fields"]["排名5-商品名"])

    def test_beijing_row_date_and_brazil_source_date_are_separate(self):
        stamp = int(datetime(2026, 9, 12, 9, 55, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)
        for body in self.raw.values():
            body["currentTimestamp"] = stamp
        self.config["feishu"]["sync"]["compare_yesterday"] = True
        plan = build_plan(self.raw, self.config)
        self.assertEqual(plan["business_date"], "2026-09-12")
        self.assertEqual(plan["source_business_date"], "2026-09-11")
        self.assertEqual(plan["timezone"], "Asia/Shanghai")
        self.assertEqual({r["date"] for r in plan["records"] if r["historical"]}, {"2026-09-11"})
        self.config["feishu"]["sync"]["backfill_sales_trend"] = True
        plan = build_plan(self.raw, self.config)
        rows = [r for r in plan["records"] if r["historical"] and r["table"] == "summary"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-09-11")
        self.assertEqual(rows[0]["fields"], {"销售额": 26039, "订单数": 164})

    def test_yesterday_switch_and_source_priority(self):
        self.config["feishu"]["sync"].update(compare_yesterday=True, backfill_sales_trend=True)
        plan = build_plan(self.raw, self.config)
        history = [r for r in plan["records"] if r["historical"]]
        self.assertEqual(len(history), 2)
        summary = next(r for r in history if r["table"] == "summary")
        self.assertEqual(summary["fields"], {"销售额": 26039, "订单数": 164})
        self.assertTrue(plan["warnings"])
        hourly = next(r for r in history if r["table"] == "hourly")
        self.assertEqual(hourly["fields"]["8时"], 304)
        self.assertEqual(summary["date"], "2026-09-10")

    def test_hourly_missing_hour_blocks_only_hourly(self):
        self.raw["hourly"]["data"]["sales"].pop()
        plan = build_plan(self.raw, self.config)
        self.assertIn("hourly", plan["errors"])
        self.assertFalse(any(r["table"] == "hourly" for r in plan["records"]))

    def test_duplicate_rank_blocks_table(self):
        rows = self.raw["hot-product"]["data"]["incomeRanking"]
        rows.append(copy.deepcopy(rows[0]))
        self.assertIn("products", build_plan(self.raw, self.config)["errors"])

    def test_currency_mismatch_blocks_table(self):
        self.raw["shop-ranking"]["data"][0]["currencyCode"] = "USD"
        self.assertIn("shops", build_plan(self.raw, self.config)["errors"])

    def test_repeat_run_does_not_duplicate_or_rewrite(self):
        client = FakeClient()
        plan = build_plan(self.raw, self.config)
        sync_plan(client, plan, self.config)
        self.assertEqual(len(client.writes), 6)
        self.assertIn("更新时间(巴西)", client.writes[0][2])
        self.assertNotIn("更新时间", client.writes[0][2])
        client.writes.clear()
        sync_plan(client, plan, self.config)
        self.assertEqual(client.writes, [])

    def test_correction_updates_only_changed_fields(self):
        client = FakeClient()
        self.config["feishu"]["sync"]["compare_yesterday"] = True
        plan = build_plan(self.raw, self.config)
        sync_plan(client, plan, self.config)
        history = next(r for r in client.data["summary"] if r["fields"]["销售额"] == 26039)
        history["fields"].update({"销售额": 25000, "毛利润": 8000})
        client.writes.clear()
        sync_plan(client, plan, self.config)
        self.assertEqual(history["fields"]["销售额"], 26039)
        self.assertEqual(history["fields"]["毛利润"], 8000)
        self.assertEqual(len(client.writes), 1)
        self.assertNotIn("毛利润", client.writes[0][2])

    def test_normal_empty_ranking_clears_old_cells(self):
        client = FakeClient()
        sync_plan(client, build_plan(self.raw, self.config), self.config)
        self.raw["shop-ranking"]["data"] = []
        sync_plan(client, build_plan(self.raw, self.config), self.config)
        self.assertIsNone(client.data["shops"][0]["fields"]["排名1-店铺名"])

    def test_duplicate_dates_abort_before_any_write(self):
        client = FakeClient()
        plan = build_plan(self.raw, self.config)
        sync_plan(client, plan, self.config)
        client.data["shops"].append(copy.deepcopy(client.data["shops"][0]))
        client.writes.clear()
        with self.assertRaisesRegex(ValueError, "重复记录"):
            sync_plan(client, plan, self.config)
        self.assertEqual(client.writes, [])

    def test_older_snapshot_does_not_overwrite(self):
        client = FakeClient()
        plan = build_plan(self.raw, self.config)
        sync_plan(client, plan, self.config)
        plan["source_timestamp_ms"] -= 1000
        plan["records"][0]["fields"]["排名1-商品名"] = "旧商品"
        client.writes.clear()
        sync_plan(client, plan, self.config)
        self.assertEqual(client.writes, [])

    def test_numeric_equivalence_and_partial_nested_config(self):
        self.assertTrue(equal_value("304.00", 304))
        defaults = {"sync": {"compare_yesterday": False, "backfill_sales_trend": False}}
        merge_config(defaults, {"sync": {"compare_yesterday": True}})
        self.assertEqual(defaults["sync"], {"compare_yesterday": True, "backfill_sales_trend": False})

    def test_formula_preserved_source_written_to_prefixed_field(self):
        client = FakeClient()
        original = client.fields

        def fields(table):
            result = original(table)
            if table == "summary":
                next(f for f in result if f["field_name"] == "退款率")["type"] = 20
                result.append({"field_name": "马帮-退款率", "type": 2})
            return result

        client.fields = fields
        plan = build_plan(self.raw, self.config)
        sync_plan(client, plan, self.config)
        summary = client.data["summary"][0]["fields"]
        self.assertNotIn("退款率", summary)
        self.assertEqual(summary["马帮-退款率"], 0.36)
        client.writes.clear()
        sync_plan(client, plan, self.config)
        self.assertEqual(client.writes, [])

    def test_legacy_text_update_field_gets_brazil_time(self):
        client = FakeClient()
        original = client.fields

        def fields(table):
            result = original(table)
            for field in result:
                if field["field_name"] == "更新时间(巴西)":
                    field.update(field_name="更新时间", type=1)
            return result

        client.fields = fields
        plan = build_plan(self.raw, self.config)
        sync_plan(client, plan, self.config)
        value = client.data["shops"][0]["fields"]["更新时间"]
        self.assertTrue(value.endswith("-03:00"))
        self.assertIn("04:00:00", value)
        self.assertEqual(int(datetime.fromisoformat(value).timestamp() * 1000), STAMP)
        client.writes.clear()
        plan["source_timestamp_ms"] -= 1000
        sync_plan(client, plan, self.config)
        self.assertEqual(client.writes, [])


if __name__ == "__main__":
    unittest.main()
