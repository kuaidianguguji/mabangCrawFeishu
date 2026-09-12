# 六张飞书表的配置与同步

每表每天一行，只保存同一个固定统计范围和币种。国家、品类不写入飞书；原始 JSON 保留。管理员表名为「每日管理员表现」，每个排名包含姓名、销售额、订单数，排名 1～5 各一组。

## 字段调整

完整字段名和类型见 [feishu_schema.json](feishu_schema.json)。类型编号：1=文本，2=数字，5=日期。所有六表使用「更新时间(巴西)」（日期，亦兼容文本）和「数据说明」（文本）。未更名的「更新时间」也可兼容。日期列必须为日期类型，不能为公式或文本。金额字段保留原精度；比例是数字字段，建议选择百分比显示，例如 0.36 显示为 36%。

- 每日商品榜单：销售额榜 incomeRanking 前五名；每个位置的商品名、销售额、销量取同一对象，不混用 quantityRanking。其余商品保留在本地清洗结果。
- 每日店铺表现：按接口 rank 取前五名；skuNum 写入「销量原值」。
- 每日管理员表现：按 rank 取前五名，employeeName→姓名，income→销售额，orderNum→订单数。
- 每日平台销售：platformId 17→虾皮、42→美客多；只使用「虾皮-销售额/订单数/排名」和「美客多-销售额/订单数/排名」。原有「虾皮」「美客多」文本列可以留着，程序忽略。新增平台不会擅自改表，会记录警告。
- 每日经营汇总：「各项变化比例」拆为独立数字字段，精确名称见下方；旧总括文本列可保留，程序忽略。
- 每日每时销售额：日期和 0时～23时，只写 hourly.sales，不写 hourly.orders。

经营汇总业务字段：销售额、订单收入、订单数、销量、毛利润、支出、退款金额、退款率、毛利率、销售额变化比例、订单收入变化比例、订单数变化比例、销量变化比例、毛利润变化比例、支出变化比例、退款金额变化比例、毛利率变化值。

销售额来自 amount-category.amount.income；订单收入来自 orderRevenue，二者不能合并。退款率来自 manager-refund.refundRate.refundRate，退款金额变化比例来自 order-metrics.refundRatio。毛利率变化值是否为百分点变化尚待业务确认。

## 本地配置

现有 config.toml 已补充飞书配置节，不修改账号密码。填写 feishu.app_id、app_secret、app_token 及 feishu.tables 中六个 table_id；也支持环境变量 FEISHU_APP_ID、FEISHU_APP_SECRET。app_token 是多维表格标识，不是应用 ID。

应用需要有该多维表格的读取和编辑权限。程序用企业自建应用 tenant_access_token 访问；未实现个人 OAuth 登录。表通过 table_id 定位，程序不会自动重命名表或删除旧字段；写入前会检查六表所需字段及类型。若业务数字列实际为公式字段（type=20），程序保留公式，将源数据写入「马帮-原字段名」数字列；这些数字列须事先存在。前缀由 feishu.formula_source_prefix 配置。

```toml
[feishu.sync]
compare_yesterday = false
correct_day_before_yesterday = false
backfill_sales_trend = false
timezone = "Asia/Shanghai"
business_timezone = "Asia/Shanghai" # 飞书行日期按北京时间
source_timezone = "Etc/GMT+3" # 看板数据为 UTC-3
currency = "CNY"
scope = "全平台"
```

- compare_yesterday=false：只写今天，不创建或修改昨天。
- compare_yesterday=true：hourly.yesterday 校正昨天每小时；amount.yesterdayIncome 校正昨天销售额。昨日不存在就新增，其他未知指标留空。
- correct_day_before_yesterday：独立控制 amount.dayBeforeYesterdayIncome 修正前日销售额。
- backfill_sales_trend：独立控制 sales-overview 返回的历史日期销售额和订单数回补；即使 compare_yesterday=false，该开关仍可修改趋势内的昨天。

三个历史开关相互独立，默认均关闭。yesterdaySameTimeIncome 永不覆盖昨日全天。来源冲突时明确的 yesterdayIncome/dayBeforeYesterdayIncome 优先于趋势销售额，并记录警告。不根据比例反推历史金额。

## 命令

安装新增依赖：

```powershell
python -m pip install -r requirements.txt
```

不联网，生成本次源数据的写入预览：

```powershell
python -m mabang_sync feishu --input output/20260911_155842_7ce54843
```

输出同目录的 feishu_plan.json。它包含每张表每个日期将更新的字段、历史标记、来源冲突及跳过原因。

配置完成后实际写入：

```powershell
python -m mabang_sync feishu --input output/20260911_155842_7ce54843 --write
```

输出 feishu_sync_report.json，记录新增、更新、无变化、旧快照跳过及失败状态。离线运行日期从源 currentTimestamp 按 business_timezone（北京时间）推导，不使用执行命令的当天；飞书日期值仍按 timezone 编码。旧 UTC+8 原始目录重新导入时可显式加 --date 2026-09-11 指定原业务日期。跨统计日响应会拒绝生成计划。

需要今后 collect 自动上传，将 feishu.enabled 设为 true：

```powershell
python -m mabang_sync collect
```

enabled=false 时 collect 仍生成写入预览，但不访问飞书。显式 --write 不受 enabled 限制。

## 更新边界

按日期查找，有则更新、无则新增；同一表同日期重复记录会停止写入，需先合并。更新只传变化字段，不抹掉历史其他指标。数值 304、304.0、"304.00" 视为相同；未变化时不刷新更新时间。「更新时间(巴西)」记录源批次最新响应时间，不代表所有历史字段均已得到验证。

正常返回少于五名时，旧排名多余位置会清空；结构错误时跳过该表并记录错误，不清空旧表。未知平台留在本地，不把未返回平台填零。不同表不保证原子写入，失败后依据报告重跑；每次都会重新读取记录。写入超时结果未知时不自动重试创建，避免重复新增。

请勿并发执行多个飞书写入进程；按日期查找再创建不是服务端唯一约束。日期是唯一业务键，因此这些表不能混放其他币种或看板筛选范围。scope 仅标记预览中的口径，不会自动操控看板筛选；改变范围时应使用另一组表。

较旧快照不会覆盖更新时间更晚的行；手工改动金额若仍符合当前同步日期，后续同步会按源数据修正。历史比对只修正明确返回的字段，不保证历史整行成为最终日报。店铺/商品/管理员/平台没有昨日明细，始终只更新今天。

接口参考：[更新记录](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/update)、[列出字段](https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list)、[自建应用访问凭证](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)。

当前经营汇总中已有的毛利率、退款率及变化比例公式保留。飞书公式与马帮可能采用不同的分母或比较周期，请以「马帮-…」列核对原接口值。公式结果不会被上传程序覆盖。

飞书行日期按北京时间标记；看板实际统计日期按 source_timezone（UTC-3）。例如北京 9月12日09:55 采集时，行日期=9月12日，源 today 日期=9月11日，昨日纠正行=9月11日。历史趋势回补按本批次行日期与源日期的差值平移，避免与昨日纠正写入不同的行；原始 JSON 的日期不改。

「更新时间(巴西)」优先于旧名称。文本字段写入例如 2026-09-11T22:55:00.000-03:00；日期字段写入同一真实时刻的毫秒时间戳，显示时区取决于飞书设置。字段改名本身不会改变日期类型的显示时区。
