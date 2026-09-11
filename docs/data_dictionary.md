# 清洗字段字典

逻辑类型 decimal / ratio 在 JSON 中均为 string，可为 null；integer 为 JSON 整数；date 为 YYYY-MM-DD 字符串。所有字段可因源数据 null、空串或 "-" 而为 null。缺少必要字段会标记 schema_error，不填零。

| 源字段 | 清洗变量名 | 逻辑类型 | 含义 |
| --- | --- | --- | --- |
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

## 数据表与来源

| 输出表 | 接口及 JSON 路径 | 粒度与字段 |
| --- | --- | --- |
| sales_daily | sales-overview: data.trend.days[] | 每个源日期一行；date、order_count、sales_amount，加上 data.trend.currencyCode → currency_code；不强制只能五天 |
| statistics_metrics | statistics: data.metrics | 一次响应一行；income_amount、gross_profit、shipping_total、expenditure_amount、item_total、order_count；增加 currency_code |
| statistics_summary | statistics: data | 一次响应一行；today_order_count、order_ratio、currency_code |
| order_metrics | order-metrics: data | 一次响应一行；该响应所有已定义字段按上表映射 |
| shop_ranking | shop-ranking: data[] | 每个店铺排名记录一行；rank、shop_id、shop_name、income_amount、order_count、sku_count、gross_profit、currency_code |

## 公共与保留字段

| 字段 | JSON 类型 | 含义 |
| --- | --- | --- |
| module | string | 原接口模块名 |
| source_timestamp_ms | integer 或 null | currentTimestamp 原值，Unix 毫秒；非业务统计日期 |
| run_started_at | string | 运行开始时间，ISO 8601 UTC；不是每个数据包的接收时刻 |
| status | string | cleaned / unmapped / schema_error |
| tables | object | 表名到记录数组的映射 |
| warnings | array[string] | 结构或业务解释待确认项 |
| notes | array[string] | statistics 的原始统计口径文字 |
| extra_fields | object | 记录中的未知字段，名称与值原样保留 |
| unmapped_data / unmapped_trend | object 或 array | 尚无可靠映射的数据，原样保留 |

## 解释边界

- `refundRatio = "-64.5%"` 转为 `refund_ratio = "-0.645"`，仅转换格式；不认定它是退款率、同比或环比。
- `grossMargin` 从字段名推断为毛利率，但计算公式未确认；其余 Ratio 的比较基准同样未确认。
- `itemTotal` / `item_total` 不能从零值样例确定单位，不当作商品数量或金额用于计算。
- `income = null` 保持 null；`orderRevenue = 0` 保留为十进制字符串 "0"；二者不互相替代。
- statistics.notes 描述不同平台的付款时间统计时区、pending 订单计入范围、实时刷新、退款按今日退款时间统计，以及退款率=今日退款金额/今日付款金额。该说明不证明 refundRatio 就是该退款率。
- 四个重复样例接口不生成业务表；需真实响应才能补齐映射。countrySales、platforms 的空数组也无法提供元素字段。
- 所有 raw 响应完整保留；本阶段不去重或合并不同接口的业务指标，避免合并不同统计口径。
