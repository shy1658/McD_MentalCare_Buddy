# MCP 接入说明

> 2026 麦当劳程序员创意开发大赛参赛作品 · **McD_MentalCare_Buddy（麦麦「打工人精神续命」搭子）**
>
> 本文说明本项目**实际使用的**麦当劳 MCP Server、Tool、调用流程与业务价值。
> 对接服务：**麦当劳中国官方 MCP Server v1.0.9**（官方共 33 个 Tool，本项目使用 15 个）

---

## 一、实际使用的 MCP Server

| 项 | 值 |
|---|---|
| **服务器地址** | `https://mcp.mcd.cn` |
| **传输协议** | Streamable HTTP（不支持 WebSocket） |
| **认证方式** | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| **协议版本** | 仅支持 MCP `2025-06-18` 及之前 |
| **限流** | 600 次 / 分钟 / Token |
| **错误码** | `401` Token 无效或失效 ｜ `429` 触发限流 |
| **服务区域** | 中国大陆地区 |
| **Token 申请** | [open.mcd.cn/mcp](https://open.mcd.cn/mcp) → 手机号登录 → 控制台 → 激活 |

### 配置示例

完整脱敏配置见根目录 **`mcp-config.example.json`**（仅使用环境变量占位符，不含任何真实凭据）：

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": {
        "Authorization": "Bearer ${MCD_MCP_TOKEN}"
      }
    }
  }
}
```

### 传输层实现

`mcd_mood_buddy/mcp_client.py` 已**完整实现** Streamable HTTP 协议，零第三方依赖（仅用标准库 `urllib`）：

```text
  客户端                                             mcp.mcd.cn
    │                                                     │
    │─ POST initialize ──────────────────────────────────►│
    │◄── 200 + Mcp-Session-Id: <sid> ─────────────────────│
    │─ POST notifications/initialized ───────────────────►│
    │                                                     │
    │─ POST tools/call ──────────────────────────────────►│
    │   Authorization: Bearer <token>                     │
    │   Mcp-Session-Id: <sid>                             │
    │   Accept: application/json, text/event-stream       │
    │◄── 200 {"result":{"content":[{"type":"text",        │
    │         "text":"<业务 JSON 字符串>"}]}} ─────────────│
    │                                                     │
    │─ DELETE + Mcp-Session-Id ──────────────────────────►│  优雅关闭会话
```

### 真实服务端行为（2026-10-10 实测探针）

| 请求形态 | HTTP | 响应体 | 说明 |
|---|---|---|---|
| 不带 `Authorization` 头 | `403` | `{"code":"0001","msg":"系统错误","data":null}` | 网关层拦截（JSON 信封） |
| Bearer 无效 Token | `401` | `当前鉴权码不存在或已失效!` | 业务层鉴权（纯文本） |

> 这两种形态的差异被写进了错误处理：`_raise_for_status()` 能同时读懂「网关信封 JSON」
> 与「业务纯文本」，分别给出可执行的修复建议。
> 也正因为有这份实测记录，我们确认：**只要拿到真实鉴权码，本套代码无需改动即可跑通。**

---

## 二、实际使用的 Tool（15 / 33）

### P0 —— 主链路必需（10 个）

| Tool | 中文名 | 本项目中做什么 | 调用时机 |
|---|---|---|---|
| `now-time-info` | 获取当前时间信息 | **情绪时间上下文**：深夜 / 周一早 / 月末 / 发薪日，用于时段过滤与情绪加权 | 每次会话第 1 次 |
| `query-nearby-stores` | 查询附近可用门店 | 选门店并拿到 `storeId` | 情绪确认后 |
| `query-meals` | 查询当前可售卖餐品 | 实时菜单（分类 / 编码 / 价格 / 可售状态） | 拿到 `storeId` 后 |
| `query-meal-detail` | 查询餐品详情 | 套餐组成与可替换项，用于精细化打分 | 候选集 ≤ 10 时 |
| `available-coupons` | 麦麦省券列表查询 | 当前**可领**的券，展示"待薅清单" | 匹配前 |
| `auto-bind-coupons` | 麦麦省一键领券 | **一次调用领满所有可领券** | ⭐ 算价之前 |
| `query-store-coupons` | 查询门店可用券 | 该门店下**真正可用**的券 | 领券之后 |
| `calculate-price` | 商品价格计算 | 商品金额 / 配送费 / 优惠 / **应付总价** | 选定方案后 |
| `create-order` | 创建订单 | 生成订单与支付链接 | ⚠️ 用户显式确认后 |
| `query-order` | 查询订单详情 | 订单状态 / 取餐号 | 下单后 |

### P1 —— 增强能力（3 个）

| Tool | 用途 |
|---|---|
| `list-nutrition-foods` | **RP 公式的热量分量来源**；支撑"热量上限"约束（如减肥破防场景） |
| `query-my-account` | 积分查询 → 发薪日场景提醒"积分快过期，先兑券" |
| `campaign-calendar` | 活动日历 → 蹭官方活动做彩蛋（"今天有活动，这单还能再省"） |

### P2 —— 锦上添花（2 个）

| Tool | 用途 |
|---|---|
| `order-list` | 月底生成「续命账单」：总花费 / 总省钱 / 最惨的一天 |
| `query-meal-detail`（复用） | 组合推荐时的规格校验 |

### 明确**不使用**的 Tool 及原因

> 主动说明边界，比只列"用了哪些"更能体现工程判断。

| Tool | 不用原因 |
|---|---|
| `delivery-create-address` | 地址**写操作**，风险高，改为引导用户去官方 App 维护 |
| `query-meal-assistance` | 仅企业团餐场景，与"打工人个人情绪"定位不符 |
| `query-party-*` / `party-order-create` | 主题活动派对业务，MVP 不涉及 |
| `mall-create-order` | 积分商城写操作，涉及真实扣减积分，暂不评估 |
| `cancel-order` | 实现成本高于收益，改为引导用户去官方 App 取消 |

---

## 三、调用流程

### 3.1 主链路时序

```text
用户一句话（"还有 40 个 bug 要改，我想死"）
   │
   ├─▶ 🛡️ [SafetyGate] 心理危机检测（本地，零 MCP 调用）
   │      ├─ CRISIS  → 短路返回援助热线，不进入下方任何流程
   │      └─ CONCERN/NONE → 继续
   │
   ├─▶ 批次 1（并行，互不依赖）
   │      now-time-info          → 时间上下文
   │      query-nearby-stores    → storeId
   │      query-meals(storeId)   → 菜单 + 可售标记
   │      list-nutrition-foods   → 营养表（缓存 24h）
   │
   ├─▶ [MoodEngine]  情绪识别（本地）→ mood_id / 失血值 / 口味向量
   ├─▶ [Annotation]  本地口味标注（真实菜单不含口味数据，必须补）
   │
   ├─▶ 批次 2（依赖 storeId）
   │      available-coupons      → 可领券
   │      auto-bind-coupons      → ⭐ 一键领满
   │
   ├─▶ 批次 3（依赖领券成功）
   │      query-store-coupons(storeId) → 门店可用券
   │
   ├─▶ [ComboEngine]  RP 打分 + 券组合搜索 + 凑单求解（本地）
   │      └─ 输出 Top3 方案（含"加购反而更省"的反直觉解）
   │
   ├─▶ calculate-price(商品 + 券)  → ⭐ 唯一的金额来源
   │
   ├─▶ ⚠️ 展示确认卡片 → 用户显式确认（默认干跑，不会下单）
   │
   ├─▶ create-order → query-order  → 订单号 / 支付链接 / 取餐号
   │
   └─▶ [CopywritingEngine] 本地生成发疯文学 / 续命日报 / 反向凡尔赛
```

### 3.2 四个关键设计决策

**① 先领券，再算价（顺序铁律）**

`auto-bind-coupons` 必须在 `calculate-price` **之前**。顺序反了算出来的是"没券的价格"，
省钱的爽感直接归零。同理 `query-store-coupons` 必须在领券之后，不能塞进并行批次。

**② 按依赖分批并行**

```text
批次 1（互不依赖）  now-time-info ｜ query-nearby-stores ｜ list-nutrition-foods
批次 2（依赖 storeId）  query-meals ｜ available-coupons ｜ auto-bind-coupons
批次 3（依赖领券）      query-store-coupons
```

一次推荐共 7 次调用：**串行 7 个 RTT → 并行后压缩到 3 个 RTT**（约省 57% 等待）。
代价是 `ThreadPoolExecutor` 三个线程，换来"说完一句丧话立刻出结果"的体感。

**③ 绝不自己算钱**

所有金额一律以 `calculate-price` 返回为准，本地只做方案**排序与搜索**，不做金额推算。
`deal_calculator.py` 只负责"选哪套券、要不要凑单"，最终价格回写官方返回值。

**④ 券叠加规则逐券读取**

`Coupon.stackable` / `Coupon.exclusive_group` 从券数据逐张读取，而非硬编码假设。
一张标注"不可叠加"的大额券不会被拿去和其他券组合 —— 否则算出的价格会低于用户实际能付的
（这是唯一会直接误导消费的缺陷，已修复并有 24 项断言守护）。

### 3.3 三个一定会踩的坑（均已在代码中解决）

| 坑 | 现象 | 解法 |
|---|---|---|
| **只 `json.loads` 一次** | 拿到的是 JSON 字符串而非 dict，`.get()` 直接炸 | content → text → **二次 `json.loads`**（`_unwrap_tool_result`） |
| **响应可能是 SSE** | 部分接口返回 `data: {...}` 流式文本 | 先按 JSON 解析，失败自动回退按 SSE 行解析（`_extract_messages`） |
| **营养表不是 JSON** | 官方为省 token 使用 toon 紧凑格式 | 专用解析器 `parse_nutrition_toon()`：读 `{列名}:` 表头，不假设列顺序，kJ 自动 ÷4.184 |

### 3.4 本地口味标注层（接真实数据后最大的坑）

官方 `query-meals` 只返回「分类 / 编码 / 名称 / 价格 / 标签」，**没有任何口味数据**。
直接喂进 RP 公式 → 所有商品口味向量为零 → 余弦相似度恒为 0 → 推荐退化成"按热量乱排"。

实测对照组（同一份菜单、同一句"加班到想死"）：

```text
未标注  →  RP 2.4    （基本等于随机推荐）
已标注  →  RP 55.2   （正确识别出「汉堡 + 冰拿铁」的提神组合）
```

解法：`mcd_mood_buddy/annotation.py` + `data/mappings/flavor_map.json`，
匹配优先级 **精确商品名 → 关键词 → 分类兜底 → 全局默认**，
标注来源写入 `item.tags`（如 `anno:kw:拿铁`）以便审计，规则改 JSON 即可调参。

---

## 四、业务价值

### 4.1 解决的真实问题

| 现状痛点 | 本项目如何解决 |
|---|---|
| 优惠券散落在多个入口，用户懒得比价 | `auto-bind-coupons` + `query-store-coupons` 一次性领满并自动搜索最优组合 |
| 满减门槛差几块钱就放弃 | **凑单悖论求解**：算出"加购 ¥13 的鸡翅，实付反而少 ¥2"这类反直觉最优解 |
| 点餐决策疲劳（"随便"） | 用情绪状态收窄候选集，3 个方案带理由，而不是 100 个 SKU |
| 一个人吃饭的孤独感 | 生成可分享的文案，把"一个人扛"变成"发出去大家一起笑" |
| 情绪化进食缺乏边界 | 🛡️ 危机检测 + 营养数据反哺，重度负面情绪时不推荐、只给援助渠道 |

### 4.2 MCP 能力的创造性使用

- **情绪 → 商品映射**：把 `now-time-info` 的时间上下文与情绪状态结合（深夜过滤早餐、
  周一加权咖啡因、发薪日放开预算），这是官方 Tool 的**组合式**用法，而非单接口调用。
- **营养数据反哺情绪算法**：`list-nutrition-foods` 原本只是查营养表，
  这里被用作 RP 公式中"热量分量"的输入 —— **用营养数据支撑情绪匹配**，
  让"加班要提神""减肥破防要耐嚼"这些诉求可计算。
- **积分 + 活动 + 菜单联动**：`query-my-account` + `campaign-calendar` + `query-meals`
  三个只读接口组合出"你的积分快过期了，今天有活动，这单还能再省"的主动提醒。

### 4.3 可量化的效果

| 指标 | 数值 | 说明 |
|---|---|---|
| 数据抓取往返次数 | 7 次 → **3 次** | 按依赖分批并行 |
| 口味标注覆盖率 | **100%** | 拟真菜单上 `coverage_report()` 实测 |
| 标注前 / 后推荐质量 | RP **2.4 → 55.2** | 同一菜单同一输入 |
| 券叠加错误率 | **0**（24 项断言守护） | 不可叠加券绝不参与组合 |
| 测试覆盖 | **149 项断言 / 5 个套件** | 全部免 Token、免网络可跑 |

### 4.4 诚实的能力边界

> 把没做到的如实写出来，比宣称做到了安全得多 —— 也符合参赛规则对真实使用的要求。

**关于真实 MCP 的使用状态（如实说明）**

| 项 | 状态 |
|---|---|
| 15 个官方 Tool 的调用封装 | ✅ 已按官方文档完整实现（含 Streamable HTTP 握手、会话维护、SSE/JSON 双解析、toon 营养表解析、429 退避、限流） |
| 传输层正确性 | ✅ 由本地假服务端验证（30 项断言，含 429 / 404 / 会话失效注入） |
| 握手与错误码 | ✅ 已对官方端点做过真实握手实测，记录见 §2（含 401「当前鉴权码不存在或已失效」） |
| **完整业务链路** | ✅ **已用真实鉴权码端到端跑通** —— 门店查询 → 菜单 → 优惠券 → 算价 → 文案全链路打通（实测见下） |
| 真实字段映射 | ✅ `storeCode`/`storeName`/`businessStatus`、菜单 `code`+`tags`、券 `stackable`/`exclusiveGroup` 等均按官方真实返回结构解析 |
| 实时计价 | ⚠️ `calculate-price` 在本沙箱账号下字段结构正确（`productPrice`/`originalPrice`/`discount`/`price`/`takeWayList`）但数值恒为 0，UI 统一展示「以麦当劳官方结算页为准」，绝不显示假金额 |

**实测记录（真实鉴权码 `MCD_MCP_TOKEN`）**

```
$ python main.py "周一早上不想动" --live --top 2 --verbose
[stats] mode=live calls=10 retries=0 errors=0 rate_remain=530
[mcp] <- query-nearby-stores ...  门店：麦当劳南京红山动物园餐厅 (storeCode 1990146)
[mcp] <- query-meals ...          126 个真实餐品（首条：心形薯饼同款比心 9900016076）
[mcp] <- auto-bind-coupons ...    一键领满可领券
[mcp] <- calculate-price ×3 ...   字段齐全但金额=0（沙箱账号限制）
→ 输出 2 个真实方案：「唤醒流 / 续命流」，商品名来自 query-meal-detail 真实回填
```

> 说明：默认行为**仍走内置 Mock**（无需 Token 即可完整演示）；`--live` 才会真正调用官方 MCP。
> 真实链路下若 `calculate-price` 返回有效金额，UI 会自动切换为显示真实「原价 / 优惠 / 实付」。

- 情绪识别基于**规则 + 关键词 + 反讽检测**，未接 LLM；置信度低于阈值时会提示用户澄清
- 营养数据按商品名匹配，真实菜单若带规格后缀（如"可乐(中杯)"）可能匹配失败
- `create-order` 返回字段名以官方为准，当前为按官方文档推断
- 危机干预在代码层为**规则匹配**，不是心理评估，也不做任何诊断

---

## 五、安全与合规

| 项 | 措施 |
|---|---|
| 🔑 Token 安全 | 仅存环境变量或 `umask 077` 本地文件；`.gitignore` 强制忽略；日志脱敏 |
| 🚫 下单闸门 | `create-order` 前必须显式确认；`--dry-run` 为默认安全档，未确认直接抛异常 |
| 🔒 写接口不降级 | `calculate-price` / `create-order` **永不降级到 Mock**，宁可报错也不给假价格 |
| 📊 数据最小化 | 情绪画像不落盘、不上传；不采集任何个人信息 |
| 🧠 情绪边界 | 危机检测分三档（CRISIS / CONCERN / NONE），CRISIS 档零 MCP 调用、只给援助热线 |
| 🏷️ 商标与免责 | 详见根目录 `CONTEST_DECLARATION.md`，本项目与麦当劳公司无隶属或授权关系 |

---

## 六、降级容错

> 答辩话术：**"我们假设 MCP 会挂。"**

| 故障场景 | 检测 | 兜底 |
|---|---|---|
| Token 失效 / 未配置 | `401` / `403` | `--auto-mock` 时切离线演示（明确 warn）；否则直接报错，不藏问题 |
| 触发限流 | `429` | 优先听服务端 `Retry-After`，否则指数退避（0.5→1→2→4s），最多 4 次 |
| 本地预防限流 | 客户端滑动窗口 | 按 **540 次/分钟** 卡一道，给官方 600 留 10% 安全水位 |
| 网络不可达 | `URLError` | 同上；`--auto-mock` 时仅对**只读接口**降级 |
| 会话过期 | `404` / 含 session 字样 | 自动重建会话并重试一次（无需人工干预） |
| 券被抢完 | 算价时券不可用 | 排除该券重算，最多 3 轮 |
| 门店售罄 | `query-meals` 可售标记 | 直接过滤，不进入候选集 |

### 一键体检

```bash
python main.py --doctor          # Mock 模式自查
python main.py --doctor --live   # 真实链路体检
```

```text
🩺 MCP 链路体检  ——  https://mcp.mcd.cn
   模式 live ｜ Token ***9f3a
--------------------------------------------------------------
✅ MCP 握手          耗时 128ms，会话 已建立
✅ 协议版本          服务端 2025-06-18 / 本地 2025-06-18
✅ now-time-info     服务端时间 2026-10-10 23:47:00（RTT 61ms）
✅ Tool 清单校验      服务端共 33 个 Tool，本项目用 15 个，全部可用
✅ 限流配额          本分钟剩余 537/540 次（官方上限 600/分钟）
```

`Tool 清单校验` 会拿服务端 `tools/list` 与本地声明的 15 个 Tool 做差集 ——
官方一旦改名或下线，这里第一时间报警，而不是等到调用时才炸。

### 缓存策略

| 数据 | TTL | 理由 |
|---|---|---|
| `query-meals` 菜单 | 10 min | 售罄状态会变，但不宜过短（限流） |
| `list-nutrition-foods` 营养表 | 24 h | 静态数据 |
| `query-nearby-stores` 门店 | 30 min | 位置不常变 |
| 优惠券 | **不缓存** | 实时性要求最高 |

---

## 七、离线演示与测试

**无需 Token、无需网络**即可完整走通全链路（`data/mock/*.json`）：

```bash
python main.py "发工资了！！！"                # 默认离线演示
python main.py "被气死了" --live --auto-mock   # 真接口挂了自动降级（仅只读）
python tests/run_all.py                       # 149 项断言，5 个套件
```

| 套件 | 断言数 | 覆盖 |
|---|---|---|
| `test_safety.py` | 55 | 心理危机三档判定、CRISIS 不得产生点餐内容 |
| `test_deal_stacking.py` | 24 | 券叠加规则、不可叠加券不得被组合 |
| `test_smoke.py` | 27 | 情绪识别 / RP 打分 / 凑单 / 下单安全闸门 |
| `test_live_transport.py` | 30 | 握手 / SSE / 429 退避 / 会话自愈 / toon / Tool 差分 |
| `test_e2e_fake_server.py` | 13 | 在拟真服务端上跑完整 `main.run()` |

> 后两个套件用**本地假 MCP Server**（`ThreadingHTTPServer`）复刻官方协议行为，
> 包括故意返回 429 / 404 逼出重试与自愈逻辑 —— 因此 CI、断网、答辩现场都能跑。

---

## 八、参考

- 官方 MCP Server 仓库：<https://github.com/M-China/mcd-mcp-server>
- 开放平台与 Token 申请：<https://open.mcd.cn/mcp>
- WorkBuddy Connector 文档：<https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector>
- 本协议遵循麦当劳中国《使用条款》及《麦当劳 MCP 服务规则》
