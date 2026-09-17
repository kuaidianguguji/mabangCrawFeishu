# 马帮 ERP → 飞书多维表格

使用 DrissionPage 登录马帮云 BI，监听 8 个接口，保存原始 JSON、清洗数据，并按日期写入六张飞书多维表。支持北京时间每日定时运行、立即执行、离线预览、只读检查及历史数据校正。

**看板使用巴西 UTC-3，飞书「日期」按北京时间填写。** 默认配置只生成本地预览；完成飞书配置并启用同步后，采集任务才会自动上传。

## 安装与配置

### Windows 一键启动（推荐）

从 GitHub 下载或克隆完整项目后，双击根目录的 [start.cmd](start.cmd)。脚本会在 CMD 窗口中完成准备并启动任务：

1. 优先复用项目的 `.venv`。首次运行会通过 PATH、Python 启动器及注册表查找 Python 3.11+；只有一个可用版本时自动选择，多个可用版本时列出编号供选择。
2. 未找到兼容 Python 时提示安装，也可手动输入已安装的 `python.exe` 完整路径。脚本不会自动安装系统 Python。
3. 自动创建 `.venv`，按 `requirements.txt` 安装并检查依赖。已有环境损坏时先保留为 `.venv.backup-*`，再重建；安装失败会停止并保留错误提示，下次运行可重试。
4. 首次生成 `config.toml`，打开记事本让你填写马帮账号、飞书凭证、表 ID 等配置；保存关闭后回到 CMD 按回车继续。需要自动上传时设置 `feishu.enabled = true`。已有配置不会被覆盖。
5. 默认立即采集一次，随后按配置中的北京时间每日运行。保持 CMD 窗口开启，按 `Ctrl+C` 停止。

后续双击会复用环境；`requirements.txt` 变化或环境检查失败时会重新安装检查，正常时跳过下载。电脑仍需安装 Chrome / Chromium。首次安装依赖需要联网，实际下载源遵循电脑的 pip 配置。

在 CMD 中也可使用：

```bat
start.cmd
start.cmd -ScheduledOnly
start.cmd -SetupOnly
```

`-ScheduledOnly` 只等待下一个定时时间，不立即采集；`-SetupOnly` 只准备环境、生成并验证配置，不启动采集、不打开配置编辑器。启动失败时窗口会保留错误信息。辅助脚本位于 [scripts/start.ps1](scripts/start.ps1)，使用 Windows 自带的 PowerShell；从 GitHub 下载时请保留整个项目目录。

### 手动安装

需要 Python 3.11+ 和 Chrome / Chromium。以下命令在 Windows PowerShell 的项目根目录执行，直接使用虚拟环境中的 Python，无需先激活环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path -LiteralPath config.toml)) {
    Copy-Item -LiteralPath config.example.toml -Destination config.toml
}
```

编辑 `config.toml`。配置模板见 [config.example.toml](config.example.toml)。已有配置文件会保留；更新项目后，应对照模板补充新增选项。

| 配置节 | 主要内容 |
| --- | --- |
| `account` | 马帮用户名、密码 |
| `browser` | 浏览器路径、固定用户目录、调试端口、窗口大小、退出时是否关闭 |
| `mabang` | 登录与看板地址、登录元素、时区选择器 |
| `timing` | 元素、页面、登录和采集等待时间，采集重试次数 |
| `retry` | 按钮、页面、飞书请求、获取数据前后整体流程的独立重试次数和间隔 |
| `output` | 本地输出目录 |
| `schedule` | 每日北京时间、轮询间隔、调度状态目录 |
| `feishu` | 同步开关、应用凭证、多维表格标识、请求超时、原值字段前缀 |
| `feishu.tables` | 六张表的 `table_id` |
| `feishu.sync` | 日期口径、币种、筛选标签及历史校正开关 |

账号可通过 `MABANG_USERNAME` / `MABANG_PASSWORD` 覆盖；飞书应用凭证可通过 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 覆盖。`app_token` 是多维表格标识，区别于应用的 `app_id`。

配置中的浏览器用户目录、输出目录及调度状态目录，相对路径以配置文件所在目录为基准。命令行 `--input` / `--output` 的相对路径以当前工作目录为基准。

### 依赖说明

| 依赖 | 用途 |
| --- | --- |
| `DrissionPage==4.1.1.4` | 浏览器控制与网络响应监听，固定已适配版本 |
| `requests>=2.32,<3` | 飞书 HTTP API 请求 |
| `tzdata>=2024.1` | 为 `zoneinfo` 提供时区数据，尤其适用于 Windows |
| `filelock>=3.15,<4` | 防止同一状态目录启动多个常驻调度进程 |

配置解析、定时调度及单元测试使用 Python 标准库，无需额外安装调度或测试框架。更新依赖声明后可执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
```

## 常用命令

### 采集一次

```powershell
.\.venv\Scripts\python.exe -m mabang_sync collect --config config.toml
```

依次执行登录、采集、清洗和飞书映射。`feishu.enabled = true` 时实际同步；为 `false` 时只生成预览。

### 常驻运行

```powershell
.\.venv\Scripts\python.exe -m mabang_sync schedule --config config.toml
```

立即执行一次，然后继续定时运行：

```powershell
.\.venv\Scripts\python.exe -m mabang_sync schedule --config config.toml --now
```

模板中的每日时间为北京时间 09:55，实际运行以本地 `config.toml` 为准：

```toml
[schedule]
times = ["09:55"] # 支持多个时间，例如 ["09:00", "18:00"]
poll_interval = 5
state_dir = "runtime/scheduler"
```

- `--now` 完成后等待下一个未来时间点；启动时不会自动补跑已经错过的时间。
- 单次任务失败会记录原因，常驻进程继续等待后续任务。采集内部仍按 `timing` 配置重试。
- 调度状态在执行前登记，避免重启后重复触发同一时间点；中断的任务需要手动补跑。
- 调整定时时间或更新代码后，按 `Ctrl+C` 停止并重新启动。建议保留 `browser.close_on_exit = true`。
- 这是前台常驻进程。定时运行期间需保持终端开启、电脑唤醒且网络可用；不会自动注册开机服务。

### 处理已采集的数据

将下面路径替换为实际运行目录。`clean` 接收直接存放 8 个 JSON 的目录；`feishu` 支持运行目录或其 `raw` 子目录。

```powershell
$runDir = "output/20260912_111614_7b10d41d"

# 重新清洗，生成新的输出目录，不打开浏览器
.\.venv\Scripts\python.exe -m mabang_sync clean --input "$runDir/raw" --output output

# 生成本地写入计划，不访问飞书
.\.venv\Scripts\python.exe -m mabang_sync feishu --input $runDir --config config.toml

# 联网检查字段和记录差异，不修改飞书记录
.\.venv\Scripts\python.exe -m mabang_sync feishu --input $runDir --config config.toml --check

# 实际写入飞书
.\.venv\Scripts\python.exe -m mabang_sync feishu --input $runDir --config config.toml --write
```

`--check` 与 `--write` 互斥。离线命令显式添加 `--write` 后会写入，即使配置中的 `feishu.enabled` 为 `false`。默认根据源数据时间戳确定行日期，重跑旧目录不会自动改成执行当天；需要指定行日期时可添加 `--date YYYY-MM-DD`。

## 飞书表结构与写入规则

首次接入请按 [飞书配置说明](docs/feishu_sync.md) 配置应用、表权限及字段。完整字段清单见 [feishu_schema.json](docs/feishu_schema.json)，源数据类型和含义见 [数据字典](docs/data_dictionary.md)。

六张表均采用「每日一行」的宽表结构：

| 配置键 | 表名 | 主要内容 |
| --- | --- | --- |
| `products` | 每日商品榜单 | 销售额榜前 5 名的商品名、销售额、销量 |
| `shops` | 每日店铺表现 | 前 5 名的店铺名、销售额、订单数、销量原值、毛利润 |
| `managers` | 每日管理员表现 | 前 5 名的姓名、销售额、订单数 |
| `platforms` | 每日平台销售 | 虾皮、美客多的销售额、订单数及排名 |
| `summary` | 每日经营汇总 | 销售额、订单收入、订单数、销量、毛利润、支出、退款及变化比例 |
| `hourly` | 每日每时销售额 | `0时` 至 `23时` 的销售额 |

国家销售、品类销售不写入飞书，原始接口数据仍保存在本地。商品表使用销售额榜，不与销量榜混排；店铺「销量原值」保留接口 `skuNum` 口径。平台映射固定为虾皮与美客多，其他平台会产生警告并保留在本地数据中。

字段名称必须完全一致，例如 `排名1-店铺名` 与 `排名1-店铺名1` 是不同字段。每张表都需要 `日期`、`更新时间(巴西)` 和文本字段 `数据说明`。`日期` 使用日期字段；金额、数量及源比例使用数字字段，比例以小数存储，例如 `-64.5%` 对应 `-0.645`。缺失值与数值 `0` 分开处理。

经营汇总中的原有公式字段保留，马帮原值写入对应的 `马帮-…` 数字列，前缀由 `formula_source_prefix` 配置。请预先建好这些列。销售额与订单收入、退款率与退款变化比例是不同指标，不能互相替代。

### 更新行为

- 写入前检查所有目标表的字段及类型；预检查失败时停止本批写入，报告具体表名、缺失字段和相似字段。
- 按「日期」查找记录：无记录则新增，已有记录只更新变化字段；同一天存在重复记录时停止，避免写错行。
- 数据相同则记录 `unchanged`，不单独刷新更新时间；源快照早于已有更新时间时记录 `skip_older_snapshot` 并跳过。
- 有效榜单不足 5 名时清空多余名次，避免保留旧榜单；源结构异常则报告错误，不把异常当成空榜单。
- 多表写入不是整体事务，中途失败可能已有部分表写入成功。按报告处理后可重新运行，由程序重新比对。

同一组表应保持一致的币种、筛选条件和日期口径。`scope` 只是记录标签，不会切换页面筛选；程序自动调整的页面筛选是时区。避免同时启动多个手动写入命令，以免并发新增同一天的记录。

### 昨日比对与历史校正

| `feishu.sync` 开关 | 启用后的作用 |
| --- | --- |
| `compare_yesterday` | 用 `hourly` 的 `yesterday` 校正昨日各小时销售额，用昨日销售额校正昨日经营汇总 |
| `correct_day_before_yesterday` | 用明确的前日销售额校正前日经营汇总 |
| `backfill_sales_trend` | 使用销售趋势中的历史销售额、订单数回补经营汇总 |

三个开关默认均为 `false`，彼此独立。即使关闭昨日比对，启用历史趋势回补仍可能修改昨日数据。

校正仅更新接口明确提供的历史字段；一致时不修改，不一致时采用本次返回的历史值。目标日期没有记录时可新增部分字段，其余保持空白。明确的昨日/前日销售额优先于趋势值；昨日同时段数值不用于全天校正，也不会通过变化比例反推历史榜单。

## 时区与浏览器流程

### 日期口径

| 设置 | 值及用途 |
| --- | --- |
| 调度时区 | 固定北京时间 `Asia/Shanghai` |
| `feishu.sync.business_timezone` | `Asia/Shanghai`：根据源时间戳确定飞书行日期 |
| `feishu.sync.timezone` | `Asia/Shanghai`：飞书日期值编码及记录日期识别 |
| `feishu.sync.source_timezone` | `Etc/GMT+3`：看板统计时区，表示固定 UTC-3 |

例如北京时间 9 月 12 日 09:55 采集，巴西为 9 月 11 日 22:55：飞书「日期」写 9 月 12 日，数据来自看板巴西 9 月 11 日的 `today`；本次 `yesterday` 校正飞书 9 月 11 日的行。历史趋势按同一批次的日期差平移标签，本地原始和清洗数据保留源统计日期。计划中的 `business_date` 与 `source_business_date` 可用于核对。

更新时间优先写入 `更新时间(巴西)`，兼容旧名称 `更新时间`。文本字段写入带 `-03:00` 的时间；日期字段写入时间戳，界面显示时区由飞书设置决定，仅改字段名称不会改变显示时区。

### 登录与监听

1. 使用固定浏览器配置及 `profile_dir` 打开首页。存在 `//a[@id="login-btn"]` 时点击登录入口，等待账号、密码输入框后提交；检测到 `//div[@id="mb-user"]` 才确认登录成功。验证码可在登录等待时间内手动完成。
2. 登录成功后，先启动接口监听，再进入云 BI。轮询 `//section//div[@data-filter="timezone"]//button` 的内容并输出日志；内容不含 `UTC` 时继续等待渲染，不将内部编码当成时区。
3. 内容包含 `UTC-3` 时保留进入页面时的监听结果，不刷新。否则弃用首次响应、重启监听，再打开下拉框并滚动寻找、点击 `//section//div[@role="listbox"]//button[contains(@title, "UTC-3")]`。切换后和本轮保存前均确认时区，校验失败则丢弃本轮响应并重试。

固定用户目录会保留 Cookie 等浏览器状态，服务端登录失效后仍需重新登录。请勿让其他浏览器进程占用同一用户目录或调试端口；程序不会直接接管已占用的端口。

### 登录状态目录

默认将登录资料集中保存到项目下的 `storage_logininfo/`：

```text
storage_logininfo/
├── browser-profile/   # Chrome 原生用户资料：Cookie、localStorage 等
└── login_state.json   # Cookie（包括会话 Cookie）及同源 sessionStorage 快照
```

每次启动先加载固定 Chrome 用户资料，再用快照补充缺失且未过期的 Cookie，并在首页脚本执行前恢复同源 sessionStorage 的缺失键。登录成功后及浏览器关闭前保存快照。Cookie 过期或服务端会话失效后仍会进入正常登录流程。

`browser.profile_dir` 与 `browser.login_info_dir` 控制上述路径；`browser.legacy_profile_dir` 指向旧的 `runtime/browser-profile`。新用户目录不存在时，程序会在首次启动时复制旧资料，保留原目录；目标目录已存在则不覆盖。迁移前关闭旧专用浏览器。快照包含登录凭证信息，整个目录的运行内容已加入 Git 忽略规则。

### 分层重试

本地 `config.toml` 和模板均提供以下配置，`count` 表示额外重试次数，`interval` 表示两次尝试之间等待的秒数：

| 配置节 | 默认 count / interval | 重试范围 |
| --- | --- | --- |
| `retry.button` | 2 / 1 秒 | 点击异常或返回 `False` 后重新定位元素；已达到目标状态则跳过重复点击 |
| `retry.page` | 2 / 3 秒 | 首页、看板页面加载异常或返回 `False`；每次重进看板前重启监听 |
| `retry.feishu` | 3 / 5 秒 | 飞书请求网络异常、HTTP 408/429/5xx 及已识别的临时业务错误 |
| `retry.before_capture` | 2 / 10 秒 | 八个 JSON 收齐前，重建本次浏览器并重跑登录、时区判断和采集 |
| `retry.after_capture` | 2 / 10 秒 | JSON 收齐后，使用同一批源数据重跑落盘、清洗和同步，不重新采集 |

例如设置 `count = 2`，表示最多执行 3 次；设为 `0` 则该层不重试。单次页面、按钮及飞书请求等待时间仍分别由 `timing.page_timeout`、`timing.element_timeout`、`feishu.timeout` 控制。`timing.retry_count` / `retry_interval` 保留为采集内部补齐接口的重试配置，各层预算独立，整体重试会重新获得内部预算，总耗时可能叠加。

日志会显示阶段、当前尝试次数、失败类型、下次等待秒数及重试是否成功。缺少账号、字段不匹配、权限或参数错误等需要人工处理的问题不盲目重试；源结构导致的清洗/映射不完整仍通过报告反馈。`Ctrl+C` 会直接中断。

飞书新增记录携带固定的 `client_token`，同一目标和载荷在请求重试、整体重试和离线重跑时保持一致；整体重跑也会重新读取飞书记录并比较差异。参数位置依据[官方新增记录 SDK](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/api/bitable/v1/model/create_app_table_record_request.py)。已成功的写入不会回滚；最终失败后仍可根据本地报告补跑。

监听模块：`sales-overview`、`hourly`、`amount-category`、`manager-refund`、`hot-product`、`statistics`、`order-metrics`、`shop-ranking`。响应通过接口状态和时区校验后保存，再分别清洗。

## 输出、日志与排错

每次采集或清洗生成独立的 `output/日期_时间_随机标识/` 目录：

| 文件 | 用途 |
| --- | --- |
| `raw/*.json` | 通过校验的原始接口响应 |
| `cleaned/*.json` | 清洗结果，金额与比例以十进制字符串保留精度 |
| `report.json` | 接口完整性、清洗完整性及各模块问题 |
| `capture_failure.json` | 获取数据前失败的阶段和异常类型；每次整体尝试使用独立目录 |
| `feishu_plan.json` | 六表待写字段、日期口径、映射错误及警告 |
| `feishu.log` | 飞书流程详细日志，每次执行追加 |
| `feishu_check_report.json` | `--check` 的只读检查结果 |
| `feishu_sync_report.json` | 实际同步的阶段、逐条操作与完成状态 |

仅执行 `clean` 不生成飞书文件；流程提前失败时，后续阶段文件可能尚未生成。常驻运行日志另存于配置的调度状态目录下 `scheduler.log`，并保存调度防重状态。

**「接口完整: True；清洗完整: True」只说明采集和清洗成功。** 判断飞书是否更新，应查看飞书同步日志及 `feishu_sync_report.json`。预览或只读检查成功不表示已上传。

飞书日志包含执行模式、认证结果、接口请求与响应状态、分页读取、字段预检查、日期匹配、变化字段、记录操作及失败阶段；凭证和令牌会脱敏。

| 现象 | 检查方法 |
| --- | --- |
| 提示「飞书未上传：当前仅生成预览」 | 自动采集设置 `feishu.enabled = true`；离线写入添加 `--write` |
| `actions` 为空，预检查失败 | 查看报告的 `phase`、表名和 `error`；按日志核对字段名称、类型及 `马帮-…` 数字列 |
| 缺少 `排名1-店铺名` 等字段 | 名称需逐字一致，去掉误加的序号、空格等字符；先用 `--check` 验证 |
| `unchanged` | 本次业务值与飞书已有值一致，无需更新 |
| `skip_older_snapshot` | 本地源数据早于已写入快照，避免旧值覆盖新值 |
| `would_create` / `would_update` | 只读检查预计会新增/更新，实际写入需使用 `--write` |
| 飞书接口返回错误 | 查看 HTTP 状态、业务错误码和提示，核对应用凭证、表权限、`app_token` 与 `table_id` |
| 接口完整但清洗完整为 `False` | 查看 `report.json` 中失败模块及对应原始响应，通常是源结构未适配 |
| 时区内容一直不含 `UTC` | 查看日志中的实际文字和 XPath，检查页面渲染及等待时间；不会直接接受该批数据 |
| 浏览器端口占用 | 关闭占用该端口的项目浏览器，或为独立配置指定不同端口和用户目录 |

单次命令成功返回 `0`；报告不完整通常返回 `2`；捕获到的运行错误返回 `1`。返回 `0` 的预览或检查仍不会写入。PowerShell 可通过 `$LASTEXITCODE` 查看退出码。

## 代码结构与验证

| 模块 | 职责 |
| --- | --- |
| `config.py` | TOML 配置加载、默认值及校验 |
| `browser.py` / `dashboard.py` | 浏览器启动、登录、时区确认与切换 |
| `login_state.py` / `retry.py` | 登录资料持久化与迁移、分层重试及按钮/页面适配 |
| `collector.py` | 接口监听、响应校验、重试 |
| `cleaners.py` / `storage.py` | 数据清洗、原始数据与报告落盘 |
| `feishu_plan.py` | 源数据到六张表的映射及历史校正计划 |
| `feishu.py` | 飞书 API、字段检查、差异比对及写入 |
| `scheduler.py` | 北京时间调度、进程锁、状态与常驻日志 |
| `diagnostics.py` | 飞书错误整理与敏感内容脱敏 |
| `__main__.py` | 命令行入口及流程编排 |

新增接口时扩展采集模块和对应清洗器；修改飞书字段时同步调整映射、字段清单和测试。新增清洗字段应在数据字典中记录名称、类型和业务含义。

```powershell
# 单元测试
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 可选：真实浏览器的本地模拟看板测试，需要 Chrome 和空闲端口 9448
.\.venv\Scripts\python.exe tests/browser_dashboard_smoke.py

# 可选：浏览器重启后的登录状态恢复测试，使用空闲端口 9449
.\.venv\Scripts\python.exe tests/browser_login_state_smoke.py
```

浏览器模拟测试使用临时用户目录和本地模拟接口，验证初始 UTC-3 响应保留、下拉滚动及切换流程。

提交 GitHub 使用 `config.example.toml`；`config.toml`、`.env`、`storage_logininfo/` 内的登录资料、运行日志、`output/` 和 `.venv/` 已通过 [.gitignore](.gitignore) 排除。
