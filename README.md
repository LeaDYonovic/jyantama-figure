# 雀魂 Mortal 牌谱分析器

面向 Windows 的雀魂四人半庄赛后复盘工具，配套 Tampermonkey 脚本导出本人牌谱，并通过 Mortal 生成 Rating、一致率、恶手率和 PT 趋势图。

本项目在 [myouo/batchmortal](https://github.com/myouo/batchmortal) 的基础上开发，保留命令行批量分析能力，并新增 Windows 桌面界面、网页安全导出、断点续跑、结果缓存和筛选统计。

## Windows 桌面版

本分支提供中文桌面程序 `desktop.py`，用于四人半庄的赛后分析。桌面版包含：

- 在主界面直接导入网页脚本生成的 JSON/TXT；
- 将本地牌谱和分析结果按雀魂玩家 ID 保存；主界面显示玩家列表，点击即可切换查看，互不混用断点与统计；
- 重复导入同一 ID 时按牌谱 UUID 合并，不覆盖已有记录；升级程序后玩家库和结果仍会保留；
- 启动时自动检查程序固定数据目录和系统“下载”文件夹，识别本工具生成的牌谱 JSON，无需记忆路径；
- 输入牌谱屋玩家 ID，通过日常浏览器正常完成牌谱屋验证后，导入金/玉/王座之间的公开四人南场；
- 手动粘贴雀魂分享链接分析；
- 从当前已登录的雀魂网页导出自己最近 100 局或全部四人半庄，并可按段位场/友人场、房间、时间筛选；
- Mortal 4.1a / 4.1b / 4.1c 模型；
- 三层趋势图：Rating/加权 AI 一致率、5%/10% 恶手率、单场/累计 PT；
- 自动排除分析失败记录，并显示中间 50% Rating 区间、均值和连续对局时段；
- 每完成一局立即原子保存，并在再次运行前生成 `results.backup.xlsx` 滚动备份；
- 导入后预先显示“已完成 / 待分析”，按完整牌谱 UUID 跳过成功局，“本次最多新增分析”只从未完成局中计数；
- “仅补全 PT”可把当前网页 JSON 中的 PT、顺位、终局点数和段位进度写回已有结果并刷新图表，不调用 Mortal，也不修改 Rating/一致率/恶手率；
- Mortal 明确拒绝的永久无效牌谱会记为 `INVALID`，下一次自动跳过；临时网络、限流失败仍记为 `ERROR`，以后可以重试；
- 开始前检测残留的旧版分析核心，避免两个版本同时请求 Mortal 网站；
- “停止”会先请求分析核心保存并关闭浏览器；若网页卡住超过 15 秒，再清理整个分析进程树；
- CSV/XLSX 导入、牌谱明细、结果链接和 PNG 导出；
- 分析前可选择“仅段位场 / 仅友人场 / 全部可分析”，结果图和明细还可独立按类型或段位房间筛选；
- 后台任务日志、停止与断点续跑。

### Mortal 网页人机验证

程序不会破解或伪造 Cloudflare Turnstile 令牌。默认使用可见 Chrome，等待网页正常签发
令牌；如果出现人机验证，请直接在该 Chrome 窗口中手动完成，程序检测到令牌后会自动
继续。后台模式无法处理需要点击的验证，因此批量分析时建议取消勾选“后台运行 Chrome”。

提交默认保持至少 10 秒间隔，连续验证或限流失败后冷却 120 秒。不要同时运行多个分析
核心；网站持续限流时应停止并稍后从断点续跑。完整原理、配置和故障处理见
[`docs/mortal-verification.md`](docs/mortal-verification.md)。

Windows 用户可双击：

```text
run_desktop.cmd
```

首次启动会创建 `.venv` 并安装依赖，之后直接打开桌面窗口。也可以手动运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe desktop.py
```

### 从已登录的雀魂网页导出自己的牌谱

项目附带 Tampermonkey 用户脚本 `majsoul_recent_100_export.user.js`。0.8.0 版支持 2026 年网页使用的 `fetchGameRecordListV2` / `fetchNextGameRecordList` 协议和 `game.mahjongsoul.com` 域名，并增加牌谱屋与桌面版的安全文件交接。它只使用当前网页已经建立的正常会话读取牌谱列表，可选择“最近 100 局”或“全部四人半庄”，并可选择“仅段位场 / 仅友人场 / 段位场 + 友人场”。段位场还能按铜/银/金/玉/王座之间筛选，两类对局都可按最近 30/90/180/365 天筛选。脚本自动跳过东风、三麻、赛事场、活动场及其他 Mortal 不支持的模式。导出的安全 JSON 会保留对局类型、开局/结束时间、顺位、终局点数、单局 PT 和四麻段位进度，但不会保存 OAuth、Cookie、access token、昵称或原始登录帧。

1. 在 Chrome 安装 Tampermonkey，新建脚本并粘贴 `majsoul_recent_100_export.user.js` 的全部内容后保存。
2. 打开雀魂网页版并刷新一次，正常登录；页面右侧会出现“自己的牌谱”面板。
3. 选择读取范围、对局类型、房间和时间，点击“读取最近 100 局”或“读取全部”，完成后点击“下载给 Windows”。“最近 100 局”会继续翻页直到找到最多 100 局符合筛选的半庄；“全部”会一直翻页到历史末尾。友人场只保留四人东南战，房间筛选只适用于段位场。下载的是只含本人牌谱链接与必要元数据的安全 JSON，不是账号凭据。
4. 打开 Windows 桌面版，点击左侧的“导入并同步本地数据…”，可一次选择一个或多个下载文件。文件内含玩家 ID 时会自动归入对应玩家；旧文件没有 ID 时，程序会询问要归入哪个 ID。
5. 选择“本次分析模式”、本次最多分析的局数和 Mortal 模型，然后点击“开始分析”。分析完成后可在趋势图上方继续按段位场、友人场或具体段位房间查看。

桌面版会完整导入新脚本导出的所有记录，不再把 JSON 截断为 100 局；主界面的“本次最多新增分析”仅控制这一次提交给 Mortal 的数量，未分析部分保留给下次断点续跑。桌面版也能直接导入旧版 `majsoul-reviewer-capture-v1` 抓包 JSON：程序会在内存中解析其中已有的牌谱列表、自动识别本人账号、提取本人 PT 和段位/友人类型等对局元数据，并跳过已知非半庄模式。此类旧抓包可能包含 OAuth/access token；不要分享，导入后建议删除。新的 0.8.0 用户脚本不会生成这类原始抓包。旧版安全导出 JSON 没有保存对局类型，桌面版会标成“未知/旧导入”；重新用 0.8.0 网页脚本导出并导入一次后，可按 UUID 为已有结果补全类型而无需重跑 Mortal。

若面板一直显示“未检测到大厅连接”，先确认用户脚本已启用，再完整刷新雀魂页面。网页协议以后若有变化，脚本会停止并显示解析错误，不会尝试绕过登录或访问别人的牌谱。100 局经 Mortal 串行分析通常耗时较长，可在桌面版把“本次最多分析”设为 10 局先试跑。

### 从牌谱屋按玩家 ID 导入公开牌谱

桌面版 0.8.0 起，左侧提供“从牌谱屋按玩家 ID 导入…”：

1. 在牌谱屋玩家页网址中找到数字玩家 ID，填入窗口。
2. 确认 Chrome 中已经安装项目附带的 0.8.0 版 Tampermonkey 脚本，然后选择金/玉/王座之间的四人南场，以及最近 100/300/500/1000 局或全部公开历史。
3. 点击“打开牌谱屋并导入”。程序会用默认浏览器打开牌谱屋；页面正常完成验证后，用户脚本会下载本次专用安全 JSON，桌面版在默认“下载”文件夹识别到它后自动导入。若浏览器拦截自动下载，可点击网页右上角的“再次下载安全 JSON”。
4. 导入完成后直接点击“开始分析”。已有成功结果和 `INVALID` 记录仍按完整牌谱 UUID 自动跳过。

下载结果会以安全 JSON 自动保存在 `%LOCALAPPDATA%\MajsoulMortalDesktop\imports`。其中只包含公开对局链接和分析所需元数据，不保存牌谱屋的 CAP 令牌、Cookie 或浏览器登录信息。牌谱屋目前只公开金之间及以上段位场且数据可能延迟；自己的友人场仍应使用 Tampermonkey 脚本从雀魂登录会话导出。

### 按玩家 ID 管理本地数据

桌面版 0.10.0 起，主界面左侧提供固定的玩家列表。导入带玩家 ID 的牌谱 JSON 后，会自动合并保存到：

```text
%LOCALAPPDATA%\MajsoulMortalDesktop\players\<玩家ID>\paipu.json
```

每个 ID 的分析结果分别保存在 `results\majsoul\玩家_<玩家ID>`。程序每次启动都会自动检查自己的固定数据目录，以及 Windows“下载”文件夹内由本工具生成的 `majsoul-*.json`、`koromo-bridge-*.json` 和旧抓包；识别到玩家 ID 后自动归档并重建玩家列表，无需用户记住任何路径。也可随时点击“自动查找数据”重新扫描，或使用“导入并同步本地数据…”一次选择多个文件。

点击玩家列表中的玩家即可切换牌谱、断点、PT 和图表。分析某个 ID 时只会检查该 ID 的结果，不会误用其他玩家的数据。重复导入时会自动按玩家 ID 和牌谱 UUID 合并，并保留已有元数据；无法可靠确定玩家 ID 的文件不会被猜测归类。

0.8.0 及更早版本生成的 `直接牌谱` 结果不会被移动或删除，它们会显示为“未分组 / 旧数据”。首次给某个 ID 导入牌谱时，程序会按 UUID 把能够确认属于该 ID 的旧结果复制到新目录，因此已经成功分析的对局可以继续跳过。若手头还有单独保存的 CSV/XLSX，可从“文件 → 导入已有分析结果到玩家…”指定 ID 归档；源文件仍保留。

命令行也支持直接链接或文本文件：

```powershell
python main.py --paipu-url "https://game.maj-soul.com/1/?paipu=……" --player "显示名称"
python main.py --paipu-file .\paipu-links.txt --player "显示名称"
```

桌面版数据默认保存在：

```text
%LOCALAPPDATA%\MajsoulMortalDesktop\results
```

该目录独立于 EXE 和解压目录，升级桌面程序时不会被覆盖。再次导入相同牌谱后，程序会按牌谱 UUID 自动跳过已有正常 Rating 和 `INVALID` 记录；`ERROR` 或空 Rating 会继续重试。不要在升级时删除 `%LOCALAPPDATA%\MajsoulMortalDesktop`，即可跨版本断点续跑。

需要构建可分发的 Windows 程序时，运行 `build_windows.ps1`。输出目录中需要同时保留 `MajsoulMortalDesktop.exe` 与 `batchmortal-cli.exe`。

> 本工具只用于赛后复盘。Rating、一致率和恶手率是筛选信号，不构成作弊结论，也不应被用于实时出牌、自动操作或绕过第三方服务的访问控制。

## 环境要求

- Python 3.10+
- Google Chrome
- 能够访问所选数据源及 `mjai.ekyu.moe` 的网络环境

安装：

```bash
git clone https://github.com/myouo/batchmortal.git
cd batchmortal
pip install -r requirements.txt
```

## 推荐：使用配置文件

项目提供完整示例配置 [`config.example.yaml`](config.example.yaml)。先编辑其中的 `mode` 和对应玩家 ID，再运行：

```bash
python main.py --config config.example.yaml
```

建议先开启 `dry_run: true`，确认玩家、模式和提取出的牌谱链接正确；确认后改回 `false` 进行 Mortal 分析。

### 数据源配置

```yaml
# 同时只能选择一个数据源：mj/0 为雀魂，th/1 为天凤
mode: "th"

# mode: mj 时只读取这一段
mj:
  nickname: ""
  # account_id: 12345678
  limit: 10
  modes: "12"

# mode: th 时只读取这一段
th:
  nickname: ""
  limit: 10
  modes: "4p-south"
```

`mj:` 与 `th:` 可以同时保存两套参数，但每次运行只读取顶层 `mode` 选中的一段：

- `mode`：数据源开关，推荐填写 `mj` 或 `th`；也兼容 `0` 或 `1`。
- `modes`：当前数据源内部的对局类型筛选，与顶层 `mode` 含义不同；天凤固定使用 `4p-south`。
- 雀魂可使用 `nickname`，也可使用数字 `account_id`；天凤必须使用 `nickname`。
- `limit` 按实际对局模式限制获取数量。

### 对局模式

| 数据源 | `modes` 示例 | 说明 |
| :--- | :--- | :--- |
| 雀魂 `mj` | `9,12,16` | amae-koromo 的数字模式 ID，例如 9 四人金南、12 四人玉南、16 四人王座南 |
| 天凤 `th` | `4p-south` | 仅接受四人半庄（四麻南场）牌谱 |

天凤的 `4p-south` 是本项目根据 Nodocchi 返回的 `playernum` 和 `playlength` 生成的统一筛选名，并非 Nodocchi API 直接返回的字符串。三麻和东风牌谱不在本项目的天凤分析范围内。

### 常用公共配置

完整字段和注释请直接查看 [`config.example.yaml`](config.example.yaml)，常用字段包括：

| 配置项 | 作用 |
| :--- | :--- |
| `review_language` | 分析页面语言：`zh-CN`, `en`, `ja`, `ko` |
| `review_ui` | 结果页样式：`classic` 或 `killerducky`；默认保持 `classic` 兼容性 |
| `model_tag` | Mortal 模型版本 |
| `headless` | 是否无界面运行浏览器 |
| `dry_run` | 只提取并打印牌谱链接，不启动浏览器 |
| `retry` | 单条牌谱失败后的重试次数 |
| `analyze_bad_move_rate` | 是否统计 5%/10% 两档恶手率 |
| `save_screenshot` | 是否保存分析结果截图 |
| `save_local_paipu` | 是否保存 Mortal 结果页 HTML |
| `output` | `csv` 或 `xlsx` |
| `plot` | `none`, `html`, `png`, `both` |
| `plot_limit` | 图表只使用最近 N 条结果；不填表示全部 |

## 命令行覆盖

配置文件是推荐入口；临时参数可以在命令行中覆盖：

```bash
# 使用配置文件，但临时切换到天凤并只提取链接
python main.py --config config.example.yaml --mode th -p ププリン --modes 4p-south --limit 10 --dry-run

# 使用配置文件，但临时分析指定雀魂玩家
python main.py --config config.example.yaml --mode mj -p 言乾 --modes 12 --limit 10
```

主要参数：

| 参数 | 说明 |
| :--- | :--- |
| `--config` | 指定 YAML/TOML 配置文件 |
| `--mode` | 唯一数据源：`mj`/`0` 或 `th`/`1` |
| `-p`, `--player` | 当前数据源的玩家昵称 |
| `-a`, `--account-id` | 雀魂数字账号 ID；天凤不支持 |
| `--modes` | 逗号分隔的对局模式 |
| `--limit` | 每个实际模式最多获取的记录数 |
| `--review-ui` | 结果页样式：`classic` 或 `killerducky` |
| `--dry-run` | 只打印牌谱 URL |
| `--headless` | 切换无头浏览器设置 |
| `--badmove` | 开启恶手率统计 |
| `--save-local` | 保存 Mortal 结果页 HTML |
| `--save-screenshot` | 保存结果截图 |
| `--plot` | 生成 HTML/PNG 图表 |

KillerDucky 页面将 Rating 和 AI 一致率显示在 About 中。项目实际从该页面引用的
`/report/*.json` 结构化数据读取这些字段；开启 `analyze_bad_move_rate` 后，也会根据每个
决策的 `actual_index` 与实际选择概率计算 5%/10% 恶手率。两种 UI 的恶手率口径一致。

旧参数 `--source majsoul|tenhou` 仍可兼容使用，但不能和 `--mode` 同时出现；新配置统一推荐 `mode: mj|th`。

## 浏览器提交模式

- 默认：单个持久浏览器串行处理，使用提交间隔和失败冷却，稳定性最好。
- `prewarm_standby: true`：使用两个持久窗口轮流处理任务；仍是受控提交，不保证更快。
- `unsafe_parallel_review: true`：绕过受控提交协调，不代表真正的多线程并发，可能更容易触发 Turnstile 或限流。
- `submit_interval`：受控模式下两次提交的最小间隔秒数。
- `submit_cooldown`：连续提交失败后的冷却秒数。

## 输出目录

雀魂与天凤结果按来源并列保存：

```text
results/
├── majsoul/<nickname>/
└── tenhou/<nickname>/
```

常见文件：

- `results.xlsx` 或 `results.csv`
- `mode_<id>/<uuid>.png`
- `mode_<id>/<uuid>_error.png`
- `mode_<id>/<uuid>.html`
- `report_<nickname>.html` / `report_<nickname>.png`

导出结果包含 `source` 字段，用于标识 `majsoul` 或 `tenhou`。天凤模式目录示例为 `mode_4p-south`。

可视化报告包含关键指标卡、Rating 单半庄值与半庄移动平均、按决策数加权的 AI 一致率、Rating 分布和低 Rating 牌谱检讨入口。缺失指标显示为 `—`，不会按 0 计入图表或汇总。

从旧版本升级时，请将原有的 `results/<nickname>/` 雀魂目录移动到 `results/majsoul/<nickname>/`；否则程序无法从新目录识别以前已经处理的牌谱。

## 注意事项

- Nodocchi 返回的 `tw` 是压缩座位排列；脚本会解码目标玩家视角，并且只接受 `tenhou.net` 正式牌谱 URL。
- Nodocchi 中没有 `url` 或 `tw` 的历史统计记录不会进入 Mortal 分析队列。
- 已成功写入结果文件的牌谱会跳过；失败记录仍可在后续运行中重试。
- `--badmove`、本地 HTML 和截图只会应用于新执行的分析，不会自动重跑已经成功的牌谱。
- 总耗时通常取决于浏览器提交、Cloudflare Turnstile 和远端分析生成速度。

## License

MIT
