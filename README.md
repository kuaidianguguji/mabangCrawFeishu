# 马帮 ERP 看板采集与数据清洗

Python 3.11+，DrissionPage 4.1.1.4。当前范围：登录 → 监听 8 个接口 → 原始响应落盘 → 清洗 → 输出字段字典及运行报告。已支持六张飞书表的每日宽表映射、预览及按日期更新。首次接入见 [飞书配置说明](docs/feishu_sync.md)。默认只生成预览，配置凭证和表 ID 后可启用同步。

## 快速使用（PowerShell，项目根目录）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item config.example.toml config.toml
```

编辑 `config.toml` 的用户名、密码和浏览器路径，再运行：

```powershell
.\.venv\Scripts\python.exe -m mabang_sync collect --config config.toml
```

也可用 `MABANG_USERNAME` / `MABANG_PASSWORD` 环境变量覆盖账号配置。遇到验证码，在登录等待时间内手动完成。登录只提交一次，防止密码错误时持续尝试。首页存在 `//a[@id="login-btn"]` 时点击登录入口；存在 `//div[@id="mb-user"]` 即确认登录成功（`logged_in_xpath`），不依赖 URL。两个标志都不存在时继续等待，不将按钮消失当成登录成功。确认登录后先启动监听，再进入数据看板，按下面的 UTC-3 流程处理响应。`logged_in_url_pattern` 仅保留兼容旧配置，不再参与判断。当前代码按所给 XPath 操作同一标签页；若真实网站改为新标签页/iframe，需要据实际页面调整登录适配器。

## 看板时区：巴西 UTC-3

每轮进入看板后检查 `//section//div[@data-filter="timezone"]//button` 的文字：

- 包含 UTC-3：保留进入页面前启动的监听，直接使用初次加载响应，不刷新页面。
- 不包含 UTC-3：取消首次监听并丢弃其响应，开启新监听，点击时区按钮，滚动下拉列表并选择 `//section//div[@role="listbox"]//button[contains(@title, "UTC-3")]`。

每轮都先启动监听再进入看板；时区按钮的文字如果暂时是 `cbt,mla,mlb,mlu,br` 等内部编码，程序会继续按轮询间隔重新读取，直到同一 XPath 的内容出现 `UTC` 后才决定分支。只有首次检查不是 UTC-3 时才弃用初次响应。选项已在 DOM 但位于滚动区域外时滚动到该元素；选项未加载时向下滚动列表寻找。进入/切换后以及本轮响应保存前均确认按钮含 UTC-3，失败则丢弃本轮响应并按配置重试。选择器与匹配文字位于 config.toml 的 mabang 配置节。

看板读取 UTC-3 数据，但按用户约定，飞书「日期」使用北京时间的采集日期。`feishu.sync.business_timezone="Asia/Shanghai"` 决定行日期，`timezone="Asia/Shanghai"` 决定飞书日期编码/查询；`source_timezone="Etc/GMT+3"` 记录看板实际统计时区。例如北京时间 9月12日09:55 采集的数据，飞书日期写 9月12日，实际看板 today 为巴西 9月11日；yesterday 校正北京标签 9月11日。历史趋势回补也按该批次的日期差平移标签，以免与昨日校正错行；raw/cleaned 中原始统计日期保持不变。plan 中同时保存 business_date 和 source_business_date 供核对。重新导入旧口径目录时请先核对日期，必要时用 --date 明确行日期。

更新字段统一采用「更新时间(巴西)」，同时兼容尚未改名的「更新时间」。若字段为文本，写入带 -03:00 的 ISO 巴西时间；若字段为日期，写入标准毫秒时间戳，展示时区由飞书的日期显示设置决定，程序不会通过减去 11 小时伪造时间戳。优先匹配新名称，不会自动删除旧列。

## 常驻运行：每天北京时间 09:55

`config.toml` 已增加：

```toml
[schedule]
times = ["09:55"]
poll_interval = 5
state_dir = "runtime/scheduler"
```

在项目目录启动：

```powershell
python -m pip install -r requirements.txt
python -m mabang_sync schedule --config config.toml
```

启动后会显示下一次执行时刻，每天北京时间（Asia/Shanghai，UTC+8）到点执行一次完整采集、清洗和飞书同步，不受 Windows 当前时区影响。飞书是否上传仍由 `feishu.enabled` 控制。`collect` 命令仍只运行一次。

立即执行一次，然后保持常驻：

```powershell
python -m mabang_sync schedule --now
```

`--now` 受同一个常驻进程锁保护；每次显式启动都会执行一次，结果保存到 `immediate_state.json`，不覆盖每日定时的防重状态。执行成功或失败后均继续等待下一个尚未到达的配置时刻；立即运行期间错过的定时时刻不补跑。仅想立即执行一次并退出，仍使用 `python -m mabang_sync collect`。

- 可配置多个时刻，例如 `times = ["09:55", "18:00"]`；必须为两位 HH:MM，不可重复。
- 修改每日时间、轮询间隔或状态目录后，需要 Ctrl+C 停止并重新启动。账号、飞书开关等任务配置在每次触发前重新读取。
- 启动时若今天的 09:55 已过，则等待明天，不自动补跑。需要立即运行时先使用 `collect`，完成后再启动 `schedule`。
- 运行中的任务顺序执行，不重叠。任务失败或返回不完整状态会记录日志，继续等待下一时刻；同一时刻不会无限重试。采集内部原有重试仍生效。
- 运行期间电脑睡眠后在当天恢复，会补执行已等待的那次任务；若已跨日则跳过旧日期，避免拿今天的数据作为昨日定时结果。任务执行期间错过的其他时刻不排队补跑。
- `runtime/scheduler/state.json` 保存最后触发时刻与结果，`scheduler.log` 保存北京时间日志并轮转。触发前先保存状态，避免崩溃后重复自动提交；强制中断的任务需检查输出并手动补跑。
- 同一状态目录只允许一个常驻进程。不要在常驻任务正在采集时另开 `collect`，二者共用浏览器端口。
- 必须保持 `browser.close_on_exit=true`，每次结束关闭专用浏览器，Cookie 用户目录仍保留。验证码仍可能需要人工处理。

这是项目内常驻命令，不是 Windows 服务，也不会自动开机启动或唤醒电脑。保持电脑开机、联网、不休眠，并让 PowerShell 进程持续运行；Ctrl+C 可停止。仅修改项目不会自动在后台启动进程。

仅处理已有 JSON，不打开浏览器、不需要密码：

```powershell
.\.venv\Scripts\python.exe -m mabang_sync clean --input C:\Users\hqt\Desktop --output output
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 会话与配置

固定 `profile_dir` 保存 Cookie、Local Storage 和浏览器设置；固定端口、语言、窗口大小，可指定固定浏览器程序和 UA。配置路径相对 `config.toml` 所在目录解析。服务器仍可使 Cookie 过期，过期后会重新登录。浏览器自动更新、系统字体/时区等不由此程序冻结。

默认退出时关闭专用浏览器但保留用户目录。若设 `close_on_exit=false`，再次运行前需关闭专用浏览器；检测到端口占用会拒绝接管。不要并发运行同一 profile。调整启动参数后需重新启动浏览器。

`config.toml`、`runtime/`、`output/`、`.env` 均已加入 `.gitignore`。真实账号、Cookie 和业务样例不应进入 Git。项目已有暂存的 `.idea` 文件未修改；`.gitignore` 不会自动取消已有跟踪。

## 输出与完整性

每次生成独立 `output/时间_唯一标识/`：

- `raw/*.json`：验证成功后立即保存完整接口响应，不保存请求头和 Cookie。
- `cleaned/*.json`：清洗表格、源响应时间戳、运行开始时间、状态、警告、未映射数据。
- `report.json`：8 个接口是否齐全、缺失接口、错误及每张表行数。

退出码 `0` 表示采集及清洗完整，`2` 表示缺接口、未知结构或清洗校验失败，`1` 表示配置/登录等运行失败。清洗命令的完整性与飞书同步独立；collect 还会检查飞书映射/同步完整性。

八个接口均已按最新真实响应补齐清洗；国家和品类按要求不写飞书，只保留源数据。旧的重复样例不符合真实结构时会标记 schema_error。

所有接口是实时请求，重试只补缺失模块，所以跨接口不保证同一事务快照。`source_timestamp_ms` 是服务器返回的时间戳；`run_started_at` 是本次运行开始时间，不是订单日期。监听仅收集页面实际发出的请求，不会自动修改看板筛选或点击未展示模块。

金额与比例通过 Decimal 校验，JSON 以十进制字符串保存，避免进一步引入浮点运算误差；浏览器已解析浮点数的原始精度无法恢复。缺失值保留为 null，真实零保留。日期保留原始日期，店铺 ID 保留字符串。字段字典见 [docs/data_dictionary.md](docs/data_dictionary.md)。

## 结构与扩展

| 文件 | 职责 |
| --- | --- |
| `config.py` | 配置加载、环境变量、校验 |
| `browser.py` | 固定浏览器环境、登录确认 |
| `collector.py` | URL 白名单、监听、响应校验、重试 |
| `cleaners.py` | 纯函数清洗、字段映射、结构异常保护 |
| `storage.py` | 本地 JSON 输出及报告 |
| `__main__.py` | 命令行流程编排 |

新增接口：在 `MODULES` 添加名称，在 `clean_module()` 添加对应分支及字段规则，再添加结构测试。`feishu_plan.py` 负责六表映射和历史纠正计划；`feishu.py` 负责鉴权、字段预检、按日期读取和差异写入。修改飞书逻辑不影响浏览器登录。完整字段清单见 docs/feishu_schema.json。

实现参考官方 [监听文档](https://www.drissionpage.cn/browser_control/listener/) 和 [浏览器启动配置](https://www.drissionpage.cn/browser_control/browser_options/)。
