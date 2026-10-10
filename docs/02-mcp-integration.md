# 🔌 MCP 接入说明

> 2026 麦当劳程序员创意开发大赛（M-CODE）参赛作品
> 对接服务：**麦当劳中国官方 MCP Server v1.0.9**（共 33 个 Tool，本项目使用 15 个）

---

## 1. 接入信息

| 项 | 值 |
|---|---|
| **服务器地址** | `https://mcp.mcd.cn` |
| **传输协议** | Streamable HTTP（不支持 WebSocket） |
| **认证方式** | 请求头 `Authorization: Bearer <YOUR_MCP_TOKEN>` |
| **Token 申请** | [open.mcd.cn/mcp](https://open.mcd.cn/mcp) → 手机号登录 → 控制台 → 激活 |
| **限流** | 600 次 / 分钟 / Token |
| **错误码** | `401` Token 无效或过期 ｜ `429` 触发限流 |
| **协议版本** | 仅支持 MCP `2025-06-18` 及之前 |
| **覆盖范围** | 中国大陆（不含港澳台） |

### 配置 JSON（WorkBuddy / Cursor / Cherry Studio / TRAE 通用）

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

### CLI 方式

```bash
export MCD_MCP_TOKEN="你的Token"
# 或
mkdir -p ~/.config/mcd && umask 077 && printf '%s' '你的Token' > ~/.config/mcd/mcp_token
```

> 🔒 **安全红线**：Token 只存环境变量或 `umask 077` 的本地文件；
> `.gitignore` 强制忽略 `.env` 与 `~/.config/mcd/`；README 与代码注释中绝不出现真实 Token。

---

## 2. 工具选型（本项目使用 15 / 33）

### 2.1 P0 —— 主链路必需（10 个）

| Tool 名称 | 中文名 | 本项目中干什么 | 何时调用 |
|---|---|---|---|
| `now-time-info` | 获取当前时间信息 | **情绪时间上下文**：深夜 / 周一早 / 月末，用于过滤时段不可售商品、加权情绪强度 | 每次会话第 1 次调用 |
| `query-nearby-stores` | 查询附近可用门店 | 到店取餐场景选门店，拿到 `storeId` | 情绪确认后 |
| `query-meals` | 查询当前可售卖餐品列表 | 拿实时菜单（分类 / 餐品编码 / 标签 / 可售状态） | 拿到 `storeId` 后 |
| `query-meal-detail` | 查询餐品详情 | 看套餐组成、可替换选项（用于 RP 精细打分） | 候选集 ≤ 10 个时 |
| `available-coupons` | 麦麦省券列表查询 | 查看当前**可领**的券，给用户看"待薅清单" | 匹配前 |
| `auto-bind-coupons` | 麦麦省一键领券 | **一次调用领满所有可领券**（无需指定 couponId） | 匹配前，先领后算 |
| `query-store-coupons` | 查询门店可用券 | 拿**当前门店下**真正可用的券 | 领券后 |
| `calculate-price` | 商品价格计算 | 算商品金额 / 配送费 / 优惠金额 / **应付总价** | 用户选定方案后 |
| `create-order` | 创建订单 | 生成订单，返回订单详情与支付链接 | ⚠️ **用户显式确认后** |
| `query-order` | 查询订单详情 | 拿订单状态、取餐号、取餐柜二维码 | 下单后 |

### 2.2 P1 —— 增强能力（3 个）

| Tool 名称 | 中文名 | 用途 |
|---|---|---|
| `list-nutrition-foods` | 餐品营养信息列表 | **RP 公式的 `energy_fit` 分量**；支持"热量上限"约束（如减肥破防场景） |
| `query-my-account` | 我的积分查询 | 发工资场景：查可用积分 / 即将过期积分 → 提醒先兑券 |
| `campaign-calendar` | 活动日历查询 | 蹭官方活动做彩蛋（"今天有 XX 活动，这单还能再省"） |

### 2.3 P2 —— 锦上添花（2 个）

| Tool 名称 | 中文名 | 用途 |
|---|---|---|
| `order-list` | 查询历史订单 | 月底生成「续命账单」：总花费 / 总省钱 / 最惨的一天 |
| `draw-lottery` | 积分抽奖 | emo 场景彩蛋："要不抽个奖转运？" |

### 2.4 明确不使用的 Tool

| Tool | 不用原因 |
|---|---|
| `delivery-create-address` | 地址写操作，风险高，改为引导用户去 App 维护 |
| `query-meal-assistance` | 仅企业团餐场景，与"打工人个人情绪"定位不符 |
| `query-party-*` / `party-order-create` | 主题活动派对，MVP 不碰 |
| `mall-create-order` | 积分商城写操作，P2 再评估（涉及真实扣积分） |
| `cancel-order` | 实现成本高于收益，改为引导用户去 App 取消 |

---

## 3. 调用链路

### 3.1 主链路时序

```
用户一句话
   │
   ├─▶ now-time-info ─────────────► 时间上下文（深夜 / 周一 / 月末）
   │
   ├─▶ [MoodEngine] 情绪识别（本地 LLM，不调 MCP）
   │      输出: mood_id / bleed_score / taste_vector
   │
   ├─▶ 并行 ► query-nearby-stores     → storeId
   │         query-meals(storeId)     → 菜单 + 可售标记
   │         available-coupons         → 可领券列表
   │         list-nutrition-foods      → 营养表（缓存 24h）
   │
   ├─▶ auto-bind-coupons             → 一键领满所有券
   ├─▶ query-store-coupons(storeId)  → 门店可用券
   │
   ├─▶ [ComboEngine] 本地 RP 打分 + 硬约束过滤
   │      约束: 预算 / 忌口 / 售罄 / 时段 / 热量上限
   │      输出: Top3 方案 + 人话理由
   │
   ├─▶ calculate-price(商品 + 券)    → 实付金额
   │
   ├─▶ ⚠️ 展示确认卡片 → 用户显式确认
   │
   ├─▶ create-order                  → 订单号 + 支付链接
   ├─▶ query-order                   → 取餐号 / 取餐柜二维码
   │
   └─▶ [SocialEngine] 本地生成发疯文学 + 海报 PNG
```

### 3.2 三个关键设计决策（答辩可讲）

**① 先领券，再算价**
`auto-bind-coupons` 必须在 `calculate-price` 之前调用，否则算出来的是"没券的价格"，省钱的爽感直接消失。

**② 并行拉取，而非串行**
按依赖切成两批：

```text
批次 1（互不依赖，并行）  now-time-info ｜ query-nearby-stores ｜ list-nutrition-foods
批次 2（依赖 storeId）   query-meals ｜ available-coupons ｜ auto-bind-coupons
批次 3（依赖领券成功）    query-store-coupons
```

一次推荐共 7 次调用，**串行是 7 个 RTT，并行后压缩到 3 个 RTT**（约省 57% 等待时间）。
代价是 `ThreadPoolExecutor` 三个线程，换来的是「说完一句丧话立刻出结果」的体感。

**③ 绝不自己算钱**
所有金额一律以 `calculate-price` 返回为准，本地只做方案排序，不做金额推算 —— 避免与官方价格不一致引发客诉。

---

## 3.5 传输层实现：握手 → 调用 → 解析

`mcp_client.py` 已完整实现 Streamable HTTP，零第三方依赖（标准库 `urllib`）。

```text
  客户端                                             mcp.mcd.cn
    │                                                     │
    │─ POST initialize ──────────────────────────────────►│
    │   {"jsonrpc":"2.0","id":1,"method":"initialize",    │
    │    "params":{"protocolVersion":"2025-06-18",...}}   │
    │◄── 200 + Mcp-Session-Id: <sid> ─────────────────────│
    │─ POST notifications/initialized ───────────────────►│  （无需响应）
    │                                                     │
    │─ POST tools/call ──────────────────────────────────►│
    │   Authorization: Bearer <token>                     │
    │   Mcp-Session-Id: <sid>                             │
    │   Accept: application/json, text/event-stream       │
    │   {"jsonrpc":"2.0","id":2,"method":"tools/call",    │
    │    "params":{"name":"query-meals","arguments":{}}}  │
    │◄── 200  {"result":{"content":[{"type":"text",       │
    │          "text":"<业务 JSON 字符串>"}]}} ────────────│
    │                                                     │
    │─ DELETE + Mcp-Session-Id ──────────────────────────►│  优雅关闭
```

### 真实服务端行为（2026-10-10 实测探针）

| 请求形态 | HTTP | 响应体 | 说明 |
|---|---|---|---|
| **不带 Authorization 头** | `403` | `{"code":"0001","msg":"系统错误","data":null}` | 网关层拦截，`Content-Type: application/json` |
| **Bearer 无效 Token** | `401` | `当前鉴权码不存在或已失效!` | 业务层鉴权，`Content-Type: text/plain` |

> 📌 **这段代码里已经针对性处理了**：`_err_snippet()` 能同时读懂「网关信封 JSON」和
> 「业务纯文本」两种错误形态，并分别给出可执行的修复建议。
>
> 也正因为有了这份探针记录，我们确认：**只要拿到真实鉴权码，这套代码无需改动即可跑通**。

### 三个一定会踩的坑（都已在代码里解决）

| 坑 | 现象 | 解法 |
|---|---|---|
| **只 json.loads 一次** | 拿到的是 JSON 字符串而非 dict，`.get()` 直接炸 | content → text → 二次 `json.loads`（`_unwrap_tool_result`） |
| **响应可能是 SSE** | 某些接口返回 `data: {...}` 流式文本 | 先按 JSON 解析，失败自动回退按 SSE 行解析（`_extract_messages`） |
| **`list-nutrition-foods` 不是 JSON** | 官方为省 token 用 toon 紧凑格式 | 专用解析器 `parse_nutrition_toon()`：读 `{列名}:` 表头，不假设列顺序，kJ 自动 ÷4.184 |

---

## 3.6 本地口味标注层：让 RP 公式在真实数据上活过来

> ⚠️ **这是接真实接口后才会暴露的最大问题，也是最容易被忽略的一层。**

官方 `query-meals` 只返回「分类 / 编码 / 名称 / 价格 / 标签」，**没有任何口味数据**。
如果直接喂给 RP 公式，所有商品的 `FlavorVector` 都是零向量 → 余弦相似度恒为 `0`
→ 口味分量恒为 `0` → 推荐退化成「按热量乱排」。

实测对照组（同一份菜单、同一句"加班到想死"）：

```text
未标注  →  RP 2.4   （基本等于随机推荐）
已标注  →  RP 55.2  （正确识别出「汉堡 + 冰拿铁」的提神组合）
```

解法：`mcd_mood_buddy/annotation.py` + `data/mappings/flavor_map.json`

```text
匹配优先级：精确商品名 → 关键词（自上而下首个命中）→ 分类兜底 → 全局默认
  "exact":    {"板烧鸡腿堡": {flavor: {...}, prep_seconds: 160}}
  "keyword":  [{"contains": ["拿铁","美式"], flavor: {caffeine: 1.0}}]
  "category": {"汉堡": {flavor: {salty: .85, chewy: .7}}}
```

- 规则写在 JSON 里，**调参不需要改 Python**（答辩现场可以当面调"麦旋风有多甜"）
- 标注来源会写进 `item.tags`（如 `anno:kw:拿铁`），出了问题一秒定位到规则
- `coverage_report()` 输出覆盖率，本项目在拟真菜单上为 **100%**

---

## 4. 降级容错策略

> 🎤 答辩话术：**"我们假设 MCP 会挂。"**

| 故障场景 | 检测 | 兜底方案 |
|---|---|---|
| **Token 失效 / 未配置** | 返回 `401` / `403` | `--auto-mock` 时自动切离线演示（并明确 warn）；否则直接报错，不藏问题 |
| **触发限流** | 返回 `429` | 优先听服务端 `Retry-After`，否则指数退避（0.5→1→2→4s），最多 4 次 |
| **本地预防限流** | `_SlidingWindowLimiter` | 客户端侧按 **540 次/分钟** 卡一道，给官方 600 的额度留 10% 安全水位 |
| **网络不可达 / 超时** | URLError | 同上；`--auto-mock` 时对**只读接口**降级 |
| **会话过期** | `404` / 含 session 字样 | 自动重建会话后重试一次（**无需人工干预**） |
| **券被抢完** | `calculate-price` 返回券不可用 | 从券池移除该券，换次优券重算，最多 3 轮 |
| **门店售罄** | `query-meals` 可售标记 | 直接过滤，不进入候选集 |
| **接口字段变更** | 解析异常 | `mcp_client.py` 适配层取默认值，上层不崩，日志告警 |

> 🔒 **降级铁律**：`calculate-price` / `create-order` 等**写操作一律不允许降级**。
> 宁可报错，也不能把 Mock 价格当成真实价格骗用户下单（已有单元测试守这条线）。

### 一键体检

```bash
python main.py --doctor          # Mock 模式自查
python main.py --doctor --live   # 真实链路体检
```

```text
🩺 MCP 链路体检  ——  https://mcp.mcd.cn
   模式 live ｜ Token ***9f3a
--------------------------------------------------------------
✅ MCP 握手
     耗时 128ms，会话 已建立
✅ 协议版本
     服务端 2025-06-18 / 本地 2025-06-18
✅ now-time-info
     服务端时间 2026-10-10 23:47:00（RTT 61ms）
✅ Tool 清单校验
     服务端共 33 个 Tool，本项目用 15 个，全部可用 ✅
✅ 限流配额
     本分钟剩余 537/540 次（官方上限 600/分钟）
```

检查项依次为：**Token → 握手 → 协议版本 → 业务接口 → Tool 清单差分 → 限流余量**。
`Tool 清单校验` 会拿服务端 `tools/list` 与本地写死的 15 个 Tool 做差集 ——
官方一旦改名 / 下线，这里第一时间报警，而不是等到调用时才炸。

### 缓存策略

| 数据 | TTL | 理由 |
|---|---|---|
| 菜单 `query-meals` | 10 min | 售罄状态会变，但不能太短（限流） |
| 营养表 `list-nutrition-foods` | 24 h | 静态数据 |
| 门店 `query-nearby-stores` | 30 min | 位置不常变 |
| 券 `query-my-coupons` | **不缓存** | 券状态实时性要求最高 |

### Mock 模式（默认，也是答辩保命开关）

```bash
python main.py "发工资了！！！"              # 默认就是离线演示，无需 Token
python main.py "被气死了" --live --auto-mock # 真接口挂了自动降级（仅只读接口）
```

数据来源 `data/mock/*.json`（menu / coupons / stores / nutrition / campaigns），
**无需 Token、无需网络，完整走通从情绪识别到凑单推荐的全链路**。

### 测试策略：不依赖真 Token 也能证明是对的

| 套件 | 命令 | 覆盖 |
|---|---|---|
| 业务逻辑 | `python tests/test_smoke.py` | 情绪识别 / RP 打分 / 凑单 / 安全闸门 |
| 传输协议 | `python tests/test_live_transport.py` | 握手 / SSE / 429 退避 / 会话自愈 / toon / Tool 差分 |
| 全链路 | `python tests/test_e2e_fake_server.py` | 在拟真服务端上跑完整 `main.run()` |
| **一键全跑** | `python tests/run_all.py` | 以上五套，**共 149 项断言** |

> 后两个套件用 **本地假 MCP Server**（`ThreadingHTTPServer`）复刻官方协议行为，
> 包括故意返回 429 / 404 来逼出重试与自愈逻辑 —— 所以 CI、断网、答辩现场都能跑。

---

## 5. 安全与合规

| 项 | 措施 |
|---|---|
| 🔑 Token 安全 | 环境变量或 `umask 077` 文件；`.gitignore` 忽略；不落日志（打印时脱敏为 `sk-***abcd`） |
| 🚫 下单闸门 | `create-order` 前必须经「确认卡片」+ 显式同意；`--dry-run` 为默认安全档 |
| 📊 数据最小化 | 情绪画像仅存本地 JSON（`~/.mcd-mood-buddy/profile.json`），支持一键导出 / 删除；**不上传任何服务器** |
| 💰 价格声明 | 所有金额以 `calculate-price` 实时返回为准，UI 显著标注"价格以实际下单为准" |
| 🧠 情绪边界 | 重度负面情绪（自伤倾向关键词）→ 停止玩笑话术 + 提供心理援助热线 |
| 🏷️ 商标 | 见 `NOTICE.md`，不构成麦当劳商标授权 |

---

## 6. 参考

- 官方仓库：<https://github.com/M-China/mcd-mcp-server>
- 开放平台：<https://open.mcd.cn/mcp>
- WorkBuddy Connector：<https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector>
- 本协议遵循麦当劳中国《使用条款》及《麦当劳 MCP 服务规则》
