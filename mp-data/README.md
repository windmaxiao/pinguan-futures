# 品观（mp-data）

一个把「公开行情 + 手工录入的产业数据」变成**盘前一页纸**的小系统。
零第三方依赖（纯 Python 标准库），静态页面，定时抓取。

- 设计说明：[../数据源设计.md](../数据源设计.md)
- 部署说明：[../部署方案.md](../部署方案.md)

## 目录结构

```
mp-data/
├── app/
│   ├── config.json    # 合约、指标、阈值配置
│   ├── db.py          # SQLite 存取
│   ├── contracts.py   # 合约按交割月滚动
│   ├── sources.py     # 新浪财经（行情/外盘/历史日线）
│   ├── fetch.py       # 抓取入口
│   ├── manual.py      # 手工数据录入（命令行）
│   ├── render.py      # 生成静态页面
│   ├── api.py         # 网页录入接口（仅 127.0.0.1）
│   └── backup.py      # 数据库备份
├── deploy/            # systemd 单元
├── db/                # SQLite 数据
├── raw/               # 原始响应留档
├── site/              # 生成的静态页（对外目录）
└── install.sh         # 服务器安装
```

## 本地试跑

```bash
# 1. 回填历史（第一次跑，可选但建议）
python app/fetch.py --backfill M2701 M2705 M2709

# 2. 录入手工数据（示例值，换成你看到的真实数据）
python app/manual.py set A4.spot 3080
python app/manual.py set B1.inventory 134.69 --date 2026-11-16
python app/manual.py set B2.soy_inventory 786.87 --date 2026-11-16
python app/manual.py set B3.crush_rate 52.17 --date 2026-11-16

# 3. 抓当日行情并生成页面
python app/fetch.py
python app/render.py

# 4. 本地预览
python -m http.server 8000 --directory site
# 浏览器打开 http://127.0.0.1:8000/
```

## 部署到服务器

```bash
# 上传后在服务器执行
sudo bash install.sh
```

### 部署前必改：把占位域名换成你自己的

仓库里的 `data.example.com` 是占位符，涉及这几处：

| 文件 | 位置 |
|---|---|
| `app/api.py` | `ALLOWED_ORIGIN` 默认值 |
| `app/brief.py` | `SITE_URL` |
| `app/health.py` | `SITE` |
| `app/news.py` | 告警正文里的看板链接 |
| `app/render.py` | 录入页页脚的提示 |
| `deploy/mp-data-api.service` | `MP_ALLOWED_ORIGIN` 环境变量 |

一条命令替换（把 `data.example.com` 换成你的域名）：

```bash
grep -rl 'data\.example\.com' . | xargs sed -i 's/data\.example\.com/你的域名/g'
```

`MP_ALLOWED_ORIGIN` 必须和实际访问域名**完全一致**（含协议），否则网页录入会被拒。

装完即启用两个定时器：

| 定时器 | 时间 | 作用 |
|---|---|---|
| `mp-data.timer` | 每天 08:35、15:10、17:30 | 抓行情 + 重生成页面 |
| `mp-data-news.timer` | 每天 08:00–23:00 每 30 分钟 | 只抓快讯，命中关键词即推送 |
| `mp-data-brief.timer` | 每天 07:30 | 抓隔夜资讯 + 生成简报 + 发邮件 |
| `mp-data-backup.timer` | 每天 03:20 | 备份数据库，保留 30 份 |

| 常驻服务 | 监听 | 作用 |
|---|---|---|
| `mp-data-api.service` | `127.0.0.1:8765` | 网页录入写入接口，经 nginx 反代 |

## 告警

每次抓取结束后会自动做一次数据体检（`health.py`），有问题就发邮件。

**凭据位置**：`/etc/mp-data/notify.json`（权限 600，属主 mpdata，不进 git）

```json
{
  "mail": {"enabled": true, "host": "smtp.qq.com", "port": 465,
           "user": "xxx@qq.com", "password": "SMTP授权码", "to": ["xxx@qq.com"]},
  "serverchan": {"enabled": true, "sendkey": "SCT开头的SendKey"}
}
```

两个通道**按需选用**，不再是全部同时发：

| 事件 | 邮件 | Server酱 |
|---|---|---|
| 每日简报 | ✅ | ❌ |
| 重要快讯（USDA/关税/罢工…） | ✅ | ❌ |
| 数据体检异常 | ✅ | ❌ |
| **抓取任务崩溃** | ✅ | ✅ |

Server酱（微信）免费额度只有 **5 条/天**，所以只留给「系统挂了」这类必须立刻知道的事，
日常推送一律显式传 `channels=("mail",)`。

QQ 邮箱用的是 SMTP **授权码**（不是登录密码）。

> Server酱版本区分：`SCT` 开头是 Turbo 版（`sctapi.ftqq.com`），
> `sctp` 开头是 Server酱³（端点不同）。本项目只对接 Turbo 版。

**告警规则**：

| 触发条件 | 说明 |
|---|---|
| 抓取任务抛异常 | 立即发，附完整 traceback |
| 关键序列超期 | 现货 >4 天、库存/开机率/利润 >12 天 |
| 出现非正结算价 | 近 7 天内 value ≤ 0 |
| 基差绝对值 > 400 | 可能是口径或数据错误 |

同一组问题 **12 小时内只发一次**（状态存在 `db/notify_state.json`）。

手动测试：

```bash
sudo -u mpdata python3 /srv/mp-data/app/notify.py      # 发一封测试邮件
sudo -u mpdata python3 /srv/mp-data/app/health.py      # 跑一次体检
```

查看状态：

```bash
systemctl list-timers 'mp-data*'
journalctl -u mp-data.service -n 50
tail -f /srv/mp-data/logs/fetch.log
```

## 页面结构

三个静态页，共用一套 CSS，顶部互相链接：

| 页面 | 内容 | 用途 |
|---|---|---|
| `index.html` | 一句话结论 + 四维卡片 + 行情/价差（左栏）+ 资讯/日历（右栏）+ 折叠体检 | 每天盘前看一眼 |
| `input.html` | 7 个录入框 + 最近 12 条录入记录 | 每周录入 |
| `weekly.html` | 10 个序列的历史表 | 查历史 |
| `brief.html` | 豆粕简报列表，按日期倒序 | 回看历史 |
| `brief/YYYY-MM-DD.html` | 豆粕简报：AI 导语 + 昨日数据 + 快讯 + 日历 | 单日复盘 |
| `macro.html` | 全局简报列表 | 早上扫一眼全市场 |
| `macro/YYYY-MM-DD.html` | 全局简报：AI 概览 + 分板块要闻 + 汇率 | 防黑天鹅 |

### 每日简报（两份，一封邮件）

每天 07:30 生成，一份邮件里放两个导语摘要 + 两个页面链接：

- **豆粕简报**：人话导语 + 四维 + 昨日行情 + 豆粕快讯 + 今日日历
- **全局简报**：100 字市场概览 + 八个板块要闻（宏观/黑色/有色/能化/农产品/贵金属/股指/汇率）+ 汇率

全局简报只做**新闻 + 汇率**，暂不接其他品种行情。

AI 摘要走 OpenAI 兼容接口，凭据在 `/etc/mp-data/llm.json`：

```json
{"api_key": "sk-...", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"}
```

**失败降级**：接口不可用时，导语位置改显示规则生成的结论，简报照常生成。
不会因为模型挂了就没有简报。

布局是纯 CSS Grid：≥900px 两栏、四维横排；以下单栏、四维 2×2。
**不依赖任何 JS 库**，`index.html` 和 `weekly.html` 双击就能看。

> ⚠️ **改完 `render.py` 必须重启 API 服务**：
> `systemctl restart mp-data-api.service`
> 因为它是常驻进程，启动时就 import 了 render，不重启会用旧代码重新生成页面
> （2026-11-18 踩过：接口保存一次，新布局被旧代码覆盖了）。

## 手工录入

### 网页录入（推荐，手机也能填）

打开 `https://data.example.com/`，页面底部「数据录入」表格，
填了值的项会保存，留空的跳过，保存后自动刷新。

- 由 `mp-data-api.service` 提供，**只监听 127.0.0.1:8765**，外网只能经 nginx（带密码）访问；
- 校验：指标白名单、数值上下限、日期格式、`Content-Type`、`Origin`；
- 保存后自动调用 `render.py` 重新生成页面。

### 命令行录入

```bash
sudo -u mpdata python3 /srv/mp-data/app/manual.py list        # 看有哪些指标
sudo -u mpdata python3 /srv/mp-data/app/manual.py set A4.spot 3450
sudo -u mpdata python3 /srv/mp-data/app/manual.py set B1.inventory 130.5 --date 2026-11-20
sudo -u mpdata python3 /srv/mp-data/app/manual.py show B1.inventory 10
```

**每周一条命令**（库存/大豆库存/开机率/到港一次录完，没给的项自动跳过）：

```bash
sudo -u mpdata python3 /srv/mp-data/app/manual.py weekly \
    --inv 125.23 --soy 795.39 --rate 48.88 --date 2026-11-13
sudo -u mpdata python3 /srv/mp-data/app/render.py
```

数据来源建议固定用**同一家同一口径**（如 Mysteel「主要油厂」），
口径混用会让 5 年分位和同比失真。

## 指标编号对照

| 编号 | 含义 | 来源 |
|---|---|---|
| `A1.<合约>.settle/close/oi/volume` | 期货结算价/收盘/持仓/成交 | 新浪 |
| `A3.S.close` | CBOT 美豆收盘 | 新浪 |
| `A4.spot` | 豆粕现货价（华东，手工） | 手工 |
| `A4.spot.100ppi` | 豆粕现货基准价（生意社，**仅作对照**） | 生意社 |
| `A5.basis.main` | 基差 = 现货 − 主力结算 | 派生 |
| `A6.spread.<近月>-<远月>` | 月差，如 `A6.spread.M2701-M2703` | 派生 |
| `A7.spread.M-RM` | 豆菜粕价差 | 派生 |
| `B1.inventory` | 油厂豆粕库存 | 手工 |
| `B2.soy_inventory` | 油厂大豆库存 | 手工 |
| `B3.crush_rate` | 开机率 | 手工 |
| `B4.arrival` | 周度到港 | 手工 |
| `B6.soy_cost` | 进口大豆含税到港成本 | 派生 |
| `B6.crush_margin` | 盘面压榨利润 | 派生 |
| `C1.hog_profit` | 生猪自繁自养头均利润 | 手工 |
| `C2.hog_future.main` | 生猪期货主力 | 派生 |
| `A8.USDCNY` | 美元兑人民币中间价 | 新浪 |
| `D1.wasde_stock` | 美豆期末库存 | 手工 |

### 压榨利润口径

```
到港成本 = (美豆美分/蒲 + 升贴水) × 0.367437 + 海运费(美元/吨)
          × 汇率 × (1+关税) × (1+增值税) + 港杂费
压榨利润 = 豆粕价×0.785 + 豆油价×0.185 − 到港成本 − 加工费
```

参数在 `config.json` 的 `crush` 段：出粕率 78.5%、出油率 18.5%、加工费 150、
港杂 100、海运费 35 美元/吨、关税 13%（美豆）、增值税 9%。

> 这些是**行业常用默认值**，不同机构差异不小（尤其海运费和升贴水）。
> 页面上显示的压榨利润是「按本系统口径」，用来判断方向（亏还是赚、趋势变化），
> 不要当成绝对值去和别人对账。

> 现货有两个口径：`A4.spot`（华东，手工，用于算基差）和
> `A4.spot.100ppi`（生意社全国基准价，自动抓取，只用于交叉校验）。
> 页面「数据体检」里会并排显示两个数，**基差一律以华东价为准**。

## 合约滚动规则

`config.json` 里的合约不再写死，而是按交割月自动滚动：

```json
"contracts": {
  "M":  {"months": [1, 3, 5, 7, 9, 11], "count": 5},
  "RM": {"months": [1, 3, 5, 7, 9, 11], "count": 1},
  "Y":  {"months": [1, 3, 5, 7, 9, 11], "count": 1}
}
```

- `months`：该品种挂牌的合约月份（豆粕/豆油/菜粕都是 1/3/5/7/9/11）；
- `count`：页面上展示几个近期合约；
- **进入交割月的合约自动退出展示**，数据仍留在库里（`A1.M2611.*` 之类不会删除）；
- 主力合约 = 当前展示合约里持仓量最大的那个，页面上带「主力」标记。

比如 2026 年 11 月看到的是 `M2701 M2703 M2705 M2707 M2709`；
到 2027 年 1 月，`M2701` 进入交割月退出，`M2711` 补进来。


## 资讯（金十 MCP）

页面底部「相关资讯」和「本周重要数据」来自金十数据 MCP 开放平台。

**Token**：`/etc/mp-data/jin10.json`（权限 600，属主 mpdata）

```json
{"token": "你的金十 MCP Token"}
```

Token 获取：https://mcp.jin10.com/app/ 登录后点「管理TOKEN」。

**配置**（`config.json` 的 `news` 段）：

| 字段 | 说明 |
|---|---|
| `keywords` | 快讯检索词，逐个调 `search_flash` |
| `news_keywords` | 深度资讯检索词，逐个调 `search_news` |
| `alert_keywords` | 命中即推送（如 USDA、关税、罢工） |
| `max_age_hours` | 快讯保留窗口（默认 72h） |
| `news_max_age_days` | 深度资讯窗口（默认 30 天，因为这类文章少） |
| `calendar_star` / `calendar_keywords` | 财经日历的最低星级与关键词过滤 |

**实现说明**：`app/mcp.py` 是自己写的极简 MCP Streamable HTTP 客户端
（urllib 实现，不依赖 requests/MCP SDK），从 `E:\DSH\futures-daily` 的
`src/mcp_client.py` 移植而来，逻辑等价、依赖归零。

> 金十的行情接口不含豆类（97 个代码里没有任何农产品），所以它只用来取
> 快讯/资讯/日历，补不了「美豆历史」这个缺口。

### 为什么新浪 7x24 默认关闭

代码里保留了新浪财经 7x24 采集（`sources.sina_flash`），但 `sina_flash.enabled = false`。

实测数据（2026-11-18）：

| 指标 | 结果 |
|---|---|
| 产出速度 | 约 100 条/小时（150 条只覆盖 1.5 小时） |
| 豆类信息密度 | **500 条里 1 条**（0.2%），且与豆粕弱相关 |
| 完整覆盖 24 小时的代价 | 约 2400 条、48 次请求 |

结论：它是综合财经洪流，豆粕这种小众品种属于长尾，**信号噪音比 1:500**。
若要重新启用，建议同时把采集频率提到每 30 分钟一次，否则两头都覆盖不到。

## 已知约束

1. **大商所官网有 WAF（HTTP 412）**，行情统一走新浪，不用官方接口。
2. **现货价、油厂库存无公开稳定接口**，必须手工录入（这也是数据可信度最高的方式——你知道数字从哪来）。
3. **新浪外盘只给当日快照**，美豆历史需另想办法，所以「成本端」状态在头几天可能是中性。
4. **5 年分位需要历史样本**，样本不足时页面明确标「分位待积累」，不编造。
5. 页面所有数字都标了来源与日期，手工项在「数据体检」里单独列出，不会静默混入。

## 故障排查

| 现象 | 处理 |
|---|---|
| 页面没有任何行情 | `fetch.py` 失败，看 `logs/fetch.log`；确认服务器能访问 `hq.sinajs.cn` |
| 基差显示 `—` | 没录现货价，跑 `manual.py set A4.spot <价格>` |
| 「过期」提示 | 手工数据超过 10 天未更新，补录即可 |
| 中文乱码 | 终端设 `PYTHONIOENCODING=utf-8`；页面本身是 UTF-8，不受影响 |
