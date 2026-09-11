# 清洗字段字典

八个接口均已按真实响应补齐清洗。金额/比例以十进制字符串保存在 cleaned JSON；飞书写入计划再转换为数值。日期、ID 保留字符串；null、空串、"-" 保留为空。

飞书六表字段及业务规则见 [feishu_sync.md](feishu_sync.md) 和 [feishu_schema.json](feishu_schema.json)。国家/品类仅保留源数据，不写飞书。

| 源字段 | 清洗字段 | 逻辑类型 | 含义 |
| --- | --- | --- | --- |
| `hour` | `hour` | integer | 小时，0–23 |
| `today` | `today` | decimal | 今日该小时的值 |
| `yesterday` | `yesterday` | decimal | 昨日该小时的值；优先于昨日采集的 today |
| `ratio` | `ratio` | ratio | 销售额变化比例 |
| `yesterdayIncome` | `yesterday_income` | decimal | 昨日销售额 |
| `dayBeforeYesterdayIncome` | `day_before_yesterday_income` | decimal | 前日销售额 |
| `yesterdaySameTimeIncome` | `yesterday_same_time_income` | decimal | 昨日同时点销售额，不能覆盖昨日全天 |
| `platformId` | `platform_id` | string | 平台 ID |
| `platformName` | `platform_name` | string | 平台名称 |
| `managerId` | `manager_id` | string | 管理员 ID |
| `employeeName` | `employee_name` | string | 管理员姓名 |
| `stockId` | `stock_id` | string | 商品 ID |
| `name` | `name` | string | 商品名称 |
| `sku` | `sku` | string | 商品 SKU |
| `orderIncome` | `order_income` | decimal | 退款率分母，订单收入 |
| `refundRate` | `refund_rate` | ratio | 退款率，与退款金额变化比例区分 |
| `currencyCode` | `currency_code` | string | 币种代码 |
| `date` | `date` | date | 接口统计日期；保留原日期，不调整时区 |
| `orderNum` | `order_count` | integer | 订单数量 |
| `sales` | `sales_amount` | decimal | 销售金额 |
| `income` | `income_amount` | decimal | 收入金额；具体收入口径待业务确认 |
| `orderRevenue` | `order_revenue` | decimal | 订单收入；与 income 的区别待确认 |
| `gross` | `gross_profit` | decimal | 毛利润（按字段名推断，口径待确认） |
| `shippingTotal` | `shipping_total` | decimal | 运费合计（收取或支出口径待确认） |
| `expend` | `expenditure_amount` | decimal | 支出金额（组成待确认） |
| `itemTotal` | `item_total` | decimal | 商品合计值；金额或数量单位待确认 |
| `refund` | `refund_amount` | decimal | 退款金额；样例 notes 说明按今日退款发生时间统计 |
| `quantity` | `quantity` | integer | 数量；具体对象待确认 |
| `todayOrderNum` | `today_order_count` | integer | 今日订单数量 |
| `rank` | `rank` | integer | 接口返回的排名 |
| `shopId` | `shop_id` | string | 店铺 ID，以字符串保存 |
| `shopName` | `shop_name` | string | 店铺名称 |
| `skuNum` | `sku_count` | integer | SKU 数量；去重口径待确认 |
| `shipping_total` | `shipping_total` | decimal | 运费合计（收取或支出口径待确认） |
| `item_total` | `item_total` | decimal | 商品合计值；金额或数量单位待确认 |
| `order_num` | `order_count` | integer | 订单数量 |
| `incomeRatio` | `income_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `grossRatio` | `gross_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `expendRatio` | `expend_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `refundRatio` | `refund_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `quantityRatio` | `quantity_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `grossMargin` | `gross_margin` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `grossMarginRatio` | `gross_margin_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `orderNumRatio` | `order_num_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |
| `orderRatio` | `order_ratio` | ratio | 比例，百分数转换为小数；比较周期/业务口径待确认，不能直接当作退款率 |

hourly.orders 中 today/yesterday 为整数订单数，其余 hourly.sales 为金额。店铺 skuNum 在飞书中保留为「销量原值」，不推断为去重 SKU 数。

source_timestamp_ms 为响应 currentTimestamp；run_started_at 为本次清洗/采集运行开始时间。status=cleaned 表示结构清洗成功，schema_error 表示已知结构不符，unmapped 仅用于未来未支持模块。tables 为清洗后记录数组；extra_fields 为未知字段；notes 保留接口业务统计说明。
