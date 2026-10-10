# 麦当劳官方 MCP 工具速查

> 对齐官方仓库 `M-China/mcd-mcp-server` **v1.0.9**（共 33 个 Tool，本项目用其中 15 个）。
> 详细接入说明见仓库根目录 `docs/02-mcp-integration.md`。

## 1. 连接信息

| 项 | 值 |
|---|---|
| 地址 | `https://mcp.mcd.cn` |
| 传输 | Streamable HTTP（POST，**不支持 WebSocket**） |
| 认证 | `Authorization: Bearer <MCP_TOKEN>` |
| 协议版本 | 仅支持 MCP `2025-06-18` 及之前 |
| 限流 | **600 次 / 分钟 / Token**（超限 429） |
| 错误码 | `401` Token 无效 / 失效 ｜ `429` 触发限流 |

### 实测到的服务端行为（2026-10-10 探针）

| 请求形态 | HTTP | 响应体 |
|---|---|---|
| 不带 `Authorization` 头 | `403` | `{"code":"0001","msg":"系统错误","data":null}`（网关信封） |
| Bearer 无效 Token | `401` | `当前鉴权码不存在或已失效!`（业务纯文本） |

> 两种形态都要能解析——**只处理 JSON 会在 401 时拿到纯文本而崩掉**。
> 客户端已分别适配，见 `mcp_client._raise_for_status()`。

### 调用三步曲

```
① initialize 握手      → 响应头取 Mcp-Session-Id 存起来
② notifications/initialized
③ tools/call           → 每次带上 Mcp-Session-Id
```

**响应解析有两个坑**：

1. 服务端可能返回 JSON，也可能返回 **SSE**（`text/event-stream`）——要取 `data:` 行再解一次。
2. 结果体是 `{"content":[{"type":"text","text":"<json字符串>"}]}`，
   `content[0].text` **还要再 `json.loads` 一次**才是真正的业务数据。

---

## 2. 本项目用到的 15 个 Tool

| 方法 | 官方 Tool | 入参 | 返回要点 | 用途 |
|---|---|---|---|---|
| `now_time_info()` | `now-time-info` | — | 日期 / 星期 / 小时 | 时间上下文（深夜 / 周一 / 发薪日） |
| `query_nearby_stores()` | `query-nearby-stores` | `address` | 门店列表 + `supportsDelivery` | 选门店 |
| `query_meals()` | `query-meals` | `storeId` | 名称 / 分类 / 编码 / 价格 / 可售 | ⭐ 菜单主体 |
| `query_meal_detail()` | `query-meal-detail` | `mealCode` | 组件 + 可选规格 | 详情（MVP 未深用） |
| `list_nutrition_foods()` | `list-nutrition-foods` | — | **toon 紧凑格式文本** | 热量，喂 RP 的热量分量 |
| `available_coupons()` | `available-coupons` | — | 可领券列表 | 展示「还能领几张」 |
| `auto_bind_coupons()` | `auto-bind-coupons` | — | 已领券 ID 列表 | ⭐ 一键领满（必须在算价前） |
| `query_store_coupons()` | `query-store-coupons` | `storeId` | 本店可用券 | ⭐ 真正能用的券 |
| `query_my_coupons()` | `query-my-coupons` | — | 全账户券 | 备用 |
| `calculate_price()` | `calculate-price` | `storeId` `dineMode` `items[]` `couponIds[]` | 商品金额 / 配送费 / 优惠 / **应付** | ⭐ 唯一金额来源 |
| `create_order()` | `create-order` | 门店 + 商品 + 券 + `confirm` | 订单号 / 支付链接 / 取餐码 | ⚠️ 写操作 |
| `query_order()` | `query-order` | `orderId` | 订单状态 | 下单后跟踪 |
| `query_my_account()` | `query-my-account` | — | 积分 / 可用 / 即将过期 | 积分过期提醒 |
| `campaign_calendar()` | `campaign-calendar` | — | 活动日历 | 营销节点 |
| `order_list()` | `order-list` | — | 历史订单 | 月底「续命账单」（路线图） |

### `calculate-price` 返回结构（本地 `PriceResult`）

```
subtotal       商品金额
discount       优惠金额
delivery_fee   配送费
payable        应付总价  ← 唯一可信金额
```

> 🔒 **绝不自己算钱。** 本地 `deal_calculator` 只负责「选哪套券 + 要不要凑单」的**搜索**，
> 最终金额一律取 `calculate-price` 的返回值。这是为了避免与官方价格不一致引发客诉。

---

## 3. 我们**没用**哪些，以及为什么

主动说明不用的接口 = 有边界感 = 专业。这一节在答辩时很加分。

| 官方 Tool | 不用 | 原因 |
|---|---|---|
| `delivery-create-address` | ❌ | 地址**写操作**，风险高，MVP 不碰 |
| `delivery-query-address` | ❌ | 涉及隐私，且本项目以到店取餐为主 |
| `query-party-*` 系列 | ❌ | 派对/团餐业务，与「一人续命」场景不符 |
| `mall-*` 系列 | ❌ | 商城实物商品，超出点餐范畴 |
| `query-order-detail` | ❌ | `query-order` 已够用 |

---

## 4. 调用时序（⚠️ 顺序是铁律）

```
第一批（并行，无依赖）
  ├─ now_time_info()
  ├─ query_nearby_stores(address)
  ├─ query_meals(store_id)
  └─ list_nutrition_foods()          ← TTL 24h 缓存

第二批（有依赖，必须串行）
  ├─ available_coupons()
  ├─ auto_bind_coupons()      ⭐ 先领满
  └─ query_store_coupons()    ⭐ 再取本店可用

算价与下单
  ├─ calculate_price(...)     ← 必须在领券之后
  └─ create_order(...)        ← 必须 --confirm
```

> **顺序反了会怎样**：先算价再领券 → 算出来的是「没券的价格」，省钱的爽感直接归零。
> 并行后数据抓取从 7 次串行往返压到 3 次。

---

## 5. 降级容错

| 故障 | 检测 | 兜底 |
|---|---|---|
| Token 失效 / 未配置 | `401` | 切 Mock，输出横幅提示 |
| 触发限流 | `429` | 读 `Retry-After` + 指数退避（0.5→1→2→4s），最多 4 次 |
| 会话过期 | `404` / 含 session 字样 | **自动重新握手**重试一次 |
| 网络不可达 | 连接超时 | `--auto-mock` 降级 |
| 券被抢完 | 算价时券不可用 | 排除该券重算，最多 3 轮 |
| 门店售罄 | 可售标记 | 直接过滤，不进候选集 |

**限流**：官方 600/分钟，本地滑动窗口保守设 **540**（留 10% 水位）。

**缓存 TTL**：菜单 10min ｜ 门店 30min ｜ 活动 1h ｜ 营养 24h ｜ **券不缓存**（状态变化快）。

### 🔒 写接口永不降级

`calculate-price` 与 `create-order` **绝不降级到 Mock** —— 宁可报错，也不能用假数据冒充真实价格。
这条线有单元测试守着（`test_live_transport.py`）。

---

## 6. 自检命令

```bash
python skill/scripts/main.py --doctor              # 六项链路体检
python skill/scripts/main.py --doctor --live       # 针对真实 MCP
```

体检项：Token 存在性 → 握手 → 协议版本 → 业务接口连通 → **Tool 清单差分** → 限流余量。

其中 `verify_tools()` 会拿服务端 `tools/list` 与本地 `REQUIRED_TOOLS` 做差集——
**官方改名或下线时能第一时间发现，而不是等到调用时才炸。**

---

## 7. 待真实数据回填的点

| 项 | 现状 | 影响 |
|---|---|---|
| `create_order` 返回键名 | `orderId` / `payUrl` / `pickupCode` 为推测 | 下单后展示字段可能错位 |
| `now_time_info` 返回格式 | 假设含 `datetime` 键，`%Y-%m-%d %H:%M:%S` | 解析失败会静默兜住，但时间上下文失效 |
| 券 `stackable` 字段 | 硬编码 True | 若实际不可叠加，**算出的价格是错的** |
| 营养表匹配 | 按商品名精确匹配 | 真实菜单带规格后缀时可能缺失 |

> 拿到真 Token 后跑一次 `python main.py "加班" --live --verbose`，
> 把真实响应 dump 下来，这四项可一次性对齐。
