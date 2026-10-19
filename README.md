---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: fc86345ddf1fef5fce9dd0a8c35a5c51_9d7ee834cb4a11f18e0e52540064ee0f
    ReservedCode1: z8VvZdOyxwWYqNJOozsj2X2ZBbmIq8QdYh2vX9PQYpWOoLwDXc0Jm7V1rKJaTP8vWLQHoV/xTsV4j4B6oms/aon6znJup7bPrv62BOJREcyV34KznZyrfdWXy2KH7ftZQ86qpHBi+Cy/F+svUvVl/QRGT/gfDi8TCcL5X9J/c/a43/AorBQvqQsDzCY=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: fc86345ddf1fef5fce9dd0a8c35a5c51_9d7ee834cb4a11f18e0e52540064ee0f
    ReservedCode2: z8VvZdOyxwWYqNJOozsj2X2ZBbmIq8QdYh2vX9PQYpWOoLwDXc0Jm7V1rKJaTP8vWLQHoV/xTsV4j4B6oms/aon6znJup7bPrv62BOJREcyV34KznZyrfdWXy2KH7ftZQ86qpHBi+Cy/F+svUvVl/QRGT/gfDi8TCcL5X9J/c/a43/AorBQvqQsDzCY=
---

# 绿茵竞猜场 · 2026 世界杯模拟下注系统

纯本地运行的模拟竞猜站点：真实 2026 世界杯 104 场赛程与比分 + 虚拟金币下注 + 用户端 + 后台管理端。
**不涉及任何真实资金、充值、提现，也不对接任何博彩平台，仅用于娱乐与产品演示。**

---

## 一、快速启动

双击 `start.bat`，或在 `D:\WorldCupBetArena` 下执行：

```
python app.py
```

| 入口 | 地址 |
| --- | --- |
| 用户端 | http://127.0.0.1:8848 |
| 管理端 | http://127.0.0.1:8848/admin |
| 管理员账号 | `admin` / `admin123`（首次启动自动创建，登录后建议改密） |

端口可通过环境变量覆盖：`set WC_PORT=9000 && python app.py`

---

## 二、数据落点（重要）

- 全部数据、代码、日志均在 **D 盘**：`D:\WorldCupBetArena`
- 数据库：`D:\WorldCupBetArena\data\arena.db`（SQLite，WAL 模式）
- 真实赛程：`D:\WorldCupBetArena\data\matches_raw.json`
- 运行日志：`D:\WorldCupBetArena\logs\`
- **不会向 C 盘写入任何文件**（Python 自身缓存除外，与本项目无关）

---

## 三、真实数据来源

- 赛程 / 比分 / 进球者 / 进球分钟：`openfootball/worldcup` 公开数据集（2014-2026 版）
- 104 场，轮次覆盖小组赛、1/16、1/8、1/4、半决赛、三四名、决赛
- 18 个场馆、真实当地时间与时区
- 决赛：2026-07-19，西班牙 vs 阿根廷，90 分钟 0-0，加时 1-0（Ferran Torres 106'）
- 数据原样导入，未做任何修改

**结算口径**：胜平负 / 大小球 2.5 / 双方进球 / 正确比分，均按 **90 分钟（含伤停补时）** 比分结算；加时与点球不计入（与主流赛前盘口一致）。

---

## 四、玩法与机制

### 账户
- 注册即送 **10,000 虚拟金币**
- 每日签到 **+1,000**
- 余额低于 200 时可领 **破产救济金 500**（每日一次）

### 盘口（由模拟实力模型实时生成，非真实赔率）
| 盘口 | 选项 |
| --- | --- |
| 胜平负 | 主胜 / 平局 / 客胜 |
| 大小球 2.5 | 大球 / 小球 |
| 双方进球 | 是 / 否 |
| 正确比分 | 8 个高频比分 |

赔率由泊松双变量模型计算（按球队模拟实力、赛事阶段降低进球期望），返还率约 92%。

### 赛季回放引擎
真实 2026 世界杯已结束，系统以「回放」方式重现：按真实开球时间顺序逐场推进。

1. **下注阶段**（默认 30s）：赔率开放，玩家可在当前场次下注
2. **比赛阶段**（默认 60s）：按真实进球分钟线性映射到 96 分钟时间轴，比分实时滚动，直播中封盘
3. **场间**（默认 3s）：结算本场全部注单，派彩入账，自动开下一场
4. 104 场走完，赛季结束

批量参数可在管理端实时修改并立即生效。

---

## 五、管理端功能

- **核心指标**：玩家数、流通金币总量、注单数、累计投注额、累计派彩、平台净额、待结算、今日投注、近 48 小时金币变动趋势
- **赛季控制**：开始 / 暂停 / 开当前场 / 立刻开赛 / 立刻结算 / 下一场 / 跳转任意场次 / 重置赛季（清空注单与流水）
- **注单监控**：全部玩家最新 300 条注单，含盘口、选项、赔率、结算状态、返还
- **用户管理**：金币人工调整（写流水，可填原因）、封禁 / 解封
- **视频源管理**：为指定场次或全局配置 embed / HLS / MP4 播放源；支持编辑、启停、连通性探测（状态码 / Content-Type / 是否 HLS 清单）、删除
- **管理员改密**：顶部「修改密码」→ 校验原密码 + 强度校验（8-64 位、须含字母和数字、拒绝常见弱口令）+ 两次确认；改密成功后所有旧会话（含当前）立即失效
- **参数设置**：下注时长、比赛时长、场间间隔、自动推进开关

---

## 六、赛事视频

用户端比赛详情页提供两层：

1. **官方持权渠道入口**（内置，无需配置）
   FIFA+ / FIFA 官网 / CCTV-5 / 小红书 / 咪咕视频 / 抖音体育 / YouTube FIFA 频道 / Sofascore
2. **站内播放器**（需管理员在后台配置**您拥有合法授权**的源）
   - `embed`：官方嵌入页（iframe）
   - `hls`：`.m3u8` 直播或回放（前端自动加载 hls.js）
   - `mp4`：直链视频

**视频画面接口**：用户端打开比赛详情时调用 `GET /api/video/play?match_id=<场次ID>`（或 `?num=<场次号>`），接口按「本场源优先 → 全局源兜底」返回启用中的播放源列表、`mode`（embed / hls / mp4 / none）、`live`、`autoplay`、`refresh_ms`、`hls_js` 等配置，前端据此渲染播放器；配置多个源时播放器右上角出现源切换下拉。

> 系统不内置、不代理、不解析任何第三方盗链或未授权转播源。能否内嵌播放取决于目标站点是否允许 iframe（`X-Frame-Options` / CSP）。

---

## 七、接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/register` | 注册（送 10000 金币） |
| POST | `/api/login` | 用户登录 |
| POST | `/api/admin/login` | 管理员登录 |
| GET | `/api/state` | 赛季状态 + 当前场次 + 倒计时 |
| GET | `/api/matches` | 104 场赛程（含赔率与赛果） |
| GET | `/api/match/{id}` | 单场详情 + 进球 + 视频源 |
| POST | `/api/bet` | 下注 |
| GET | `/api/bets` | 我的注单 |
| GET | `/api/wallet` | 金币流水 |
| GET | `/api/leaderboard` | 排行榜 |
| POST | `/api/checkin` | 每日签到 |
| POST | `/api/relief` | 破产救济 |
| GET | `/api/admin/overview` | 后台总览 |
| GET | `/api/admin/users` `/api/admin/bets` `/api/admin/videos` | 后台列表 |
| POST | `/api/admin/season` | 赛季控制（start/pause/open/kickoff/settle/next/goto/reset） |
| POST | `/api/admin/user/coins` `/api/admin/user/ban` | 调币 / 封禁 |
| POST | `/api/admin/videos` `/api/admin/videos/delete` | 视频源增删 |
| GET | `/api/video/play?match_id=&num=` | 视频画面接口：返回本场可用源（本场优先 + 全局兜底）、播放模式与播放器配置 |
| GET | `/api/admin/videos/test?id=` | 视频源连通性探测（状态码 / Content-Type / 是否 HLS 清单） |
| POST | `/api/admin/videos/update` | 编辑视频源（关联场次 / 标题 / 类型 / 地址 / 启用） |
| POST | `/api/admin/videos/toggle` | 启用 / 停用视频源 |
| POST | `/api/admin/password` | 管理员改密（校验原密码 → 换盐重哈希 → 旧会话全部失效） |
| POST | `/api/admin/settings` | 时长与自动推进设置 |

鉴权：`Authorization: Bearer <token>`，HMAC-SHA256 签名，7 天有效；密码加盐 SHA-256 存储。
改密后该账号 `token_ver` 自增，所有旧 token 立即失效，需用新密码重新登录。

---

## 八、目录结构

```
D:\WorldCupBetArena\
├─ app.py              # HTTP 服务与全部 API 路由
├─ core.py             # 数据模型、赔率引擎、结算、赛季推进线程
├─ start.bat           # 一键启动
├─ data\
│  ├─ matches_raw.json # 真实赛程与比分（104 场）
│  ├─ arena.db         # SQLite 数据库
│  └─ secret.key       # Token 签名密钥（自动生成）
├─ static\
│  ├─ index.html       # 用户端
│  └─ admin.html       # 管理端
└─ logs\
   ├─ server.log
   └─ server.err.log
```

---

## 九、免责声明

本项目为**模拟娱乐**产品：金币无任何现实价值，不可购买、不可兑换、不可转让；不提供任何真实投注渠道；比分与赛程来自公开数据，用于回放演示。请勿将本系统用于任何形式的真实博彩活动。
*（内容由AI生成，仅供参考）*
