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

也可用 `MABANG_USERNAME` / `MABANG_PASSWORD` 环境变量覆盖账号配置。遇到验证码，在登录等待时间内手动完成。登录只提交一次，防止密码错误时持续尝试。首页存在 `//a[@id="login-btn"]` 时点击登录入口；存在 `//div[@id="mb-user"]` 即确认登录成功（`logged_in_xpath`），不依赖 URL。两个标志都不存在时继续等待，不将按钮消失当成登录成功。确认后先启动监听，再跳转数据看板。`logged_in_url_pattern` 仅保留兼容旧配置，不再参与判断。当前代码按所给 XPath 操作同一标签页；若真实网站改为新标签页/iframe，需要据实际页面调整登录适配器。

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
