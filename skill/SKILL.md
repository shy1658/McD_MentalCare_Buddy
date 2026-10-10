---
name: mcd-mentalcare-buddy
version: 0.1.0
display_name: "麦麦精神续命搭子"
display_name_en: "McD MentalCare Buddy"
description: "麦麦「打工人精神续命」搭子 —— 一个基于麦当劳官方 MCP 的情绪化点餐 Skill。用户一句话描述精神状态（加班到崩溃 / 被甲方气死 / 发工资了 / 想摸鱼 / emo / 月末吃土），自动识别情绪与失血值，调用麦当劳 MCP 拉取实时菜单、门店、优惠券与营养数据，用「续命值 RP」算法匹配最能治愈当下状态的套餐，搜索最优券组合（含凑单悖论求解），最后生成发疯文学 / 续命日报 / 反向凡尔赛三种文体的朋友圈文案。当用户说累、加班、被气、想死（口头禅）、发工资、摸鱼、失恋、赶时间、减肥破防、月末没钱，或想点麦当劳、想知道吃什么、想凑单省钱、想发朋友圈吐槽时，应使用本技能。"
description_zh: "基于麦当劳官方 MCP 的「情绪化点餐」Skill。输入一句精神状态描述，输出带续命值评分的套餐方案 + 最优优惠券组合 + 可发朋友圈的文案。覆盖 12 种打工人精神状态（加班崩溃、被气死、摸鱼、发工资、周一综合征、周五快乐、emo、赶时间、减肥破防、被卷麻木、月末吃土、周日恐惧），支持反讽识别（「我笑了」判定为爆表而非开心）与时间上下文（深夜 / 周一 / 发薪日）。核心算法「续命值 RP」= 口味×40 + 热量×20 + 功能×20 + 取向×20 + 优惠暴击×20，在套餐维度计算而非单品平均。内置 Mock 数据，无 Token、断网也能完整演示；默认干跑，绝不自动下单。"
description_en: "An emotional-state-aware McDonald's ordering Skill built on the official McDonald's MCP server. Describe your mental state in one sentence (burned out from overtime, furious at a client, just got paid, slacking off, heartbroken, broke at month-end) and it identifies the mood, pulls live menu/store/coupon/nutrition data via MCP, scores combos with a proprietary 'Revival Points' (RP) algorithm, searches the optimal coupon stack including add-to-qualify paradox solving, and generates shareable social copy in three tones. Sarcasm detection ('I'm fine 😊') and time context (late night / Monday / payday) supported. Works fully offline with bundled mock data; dry-run by default and never places an order without explicit confirmation."
author: "McD_MentalCare_Buddy Team"
tags: [mcdonalds, mcp, mood, food-ordering, coupon-optimizer, emotional-ai, 麦当劳, 情绪识别, 点餐, 优惠券, 凑单, 打工人, 发疯文学, skill, hackathon]
---

# 🍔 麦麦精神续命搭子 · McDonald's MentalCare Buddy

> **一句话**：麦当劳 MCP 不只能点餐，还能**续命**。
> 你说一句「还有 40 个 bug 要改，我想死」，它给你算失血值、配套餐、凑满减、写发疯文学。

**执行手册**：严格按 Step 0 → Step 6 顺序执行，不跳步。每步都有精确命令和固定产出。

> **路径约定**：下文 `<SKILL_DIR>` 指本 SKILL.md 所在目录（即 `skill/`），`<ROOT>` 指仓库根目录（`<SKILL_DIR>` 的上一级）。
> **Python 命令**：Windows 一般用 `python`（也可用 `py -3`），macOS/Linux 一般用 `python3`。
> **零第三方依赖**：只用标准库，装好 Python 3.10+ 即可运行，无需 `pip install`。

---

## 0. 何时触发

用户出现以下任一信号时，加载本技能：

| 信号类型 | 例子 |
|---|---|
| 情绪倾诉 | 「累成狗」「被甲方气死」「emo 了」「周日晚上好慌」「减肥第 3 天破防」 |
| 打工人节点 | 「发工资了」「月末吃土」「周一又要上班」「终于周五了」 |
| 点餐意图 | 「中午吃什么」「想点麦当劳」「有什么优惠」 |
| 省钱意图 | 「怎么凑单最划算」「这两张券能叠加吗」 |
| 社交需求 | 「帮我写个朋友圈」「来点发疯文学」 |

⚠️ **不要**在用户明确只要查门店地址、查历史订单、或咨询营养过敏信息时强行推荐套餐——那时直接用对应 MCP 工具即可。

---

## 1. 执行手册总览

| Step | 动作 | 命令/函数 | 产出 |
|---|---|---|---|
| **0** | 环境体检 | `--doctor` | 链路六项检查报告 |
| **1** | 情绪识别 | `detect_mood(text)` | 情绪 ID + 失血值 BS + 口味向量 |
| **2** | 抓取实时数据 | 4 个 MCP 只读接口（并行两批） | 门店 / 菜单 / 券 / 营养 |
| **3** | 本地口味标注 | `annotate_menu(menu)` | ⭐ 关键中间层，见 §5 |
| **4** | 打分 + 组方案 | `rank_items()` → `build_bundles()` | 3 个候选方案 + RP 分 |
| **5** | 券优化 + 算价 | `optimize()` → `calculate_price()` | 最终实付金额 |
| **6** | 渲染 + 文案 | `render()` / `generate()` | Markdown 卡片 + 朋友圈文案 |

---

## 2. Step 0 —— 环境体检（首次使用必做）

```bash
python <SKILL_DIR>/scripts/main.py --doctor
```

六项检查依次为：Token 存在性 → MCP 握手 → 协议版本 → 业务接口连通 → Tool 清单差分 → 限流余量。

| 检查结果 | 含义 | 处理 |
|---|---|---|
| 全绿 | 真实 MCP 可用 | 直接进 Step 1 |
| `401 当前鉴权码不存在或已失效!` | Token 无效 | 换 Token；或加 `--mock` 用离线演示 |
| `403 {"code":"0001"...}` | 请求未带 Authorization 头 | 检查环境变量是否生效 |
| 连接超时 | 网络不可达 | 加 `--auto-mock` 降级 |

> 💡 **没 Token 也能完整演示**：`--mock` 模式用 `data/mock/*.json` 跑通全链路，答辩断网时的保命开关。

---

## 3. Step 1 —— 情绪识别

```bash
python <SKILL_DIR>/scripts/main.py --list-moods     # 先看支持哪些精神状态
```

内置 **12 种精神状态**，每种带失血值 BS（Bleed Score，你有多惨）和口味向量：

| mood_id | 标签 | 失血值 | 功能诉求 | 口味取向 |
|---|---|---|---|---|
| `overtime_crash` | 加班到崩溃 | 85 | 咖啡因 | 咸 + 提神 |
| `pissed_off` | 被气死 | 90 | 耐嚼（要咬东西泄愤） | 咸 + 酥脆 |
| `slacking` | 摸鱼 | 25 | — | 清淡（**低热量**，怕吃困） |
| `payday` | 发工资 | 15 | — | 全面放开 + 贵价 |
| `monday_blues` | 周一综合征 | 70 | 咖啡因 | 甜 + 提神 |
| `friday_joy` | 周五快乐 | 20 | — | 咸香 + 放开 |
| `emo` | emo / 失恋 | 75 | 糖分 | 高甜 |
| `rushed` | 赶时间焦虑 | 60 | 咖啡因 + 出餐快 | 咸 |
| `diet_broken` | 减肥第 3 天破防 | 65 | 耐嚼 | 全都要 |
| `numb` | 被卷到麻木 | 80 | — | 均衡（随便） |
| `broke` | 月末吃土 | 55 | — | 咸 + 高性价比 |
| `sunday_dread` | 周日晚上恐惧 | 72 | 糖分 | 甜咸 |

三个识别增强（这是本 Skill 区别于关键词匹配的地方）：

1. **反讽识别** —— 「甲方要五彩斑斓的黑，**我笑了**」命中 `pissed_off` 的反讽标记，判定为爆表而非开心。规则见 `references/mood-matrix.md` 的 `sarcasm_markers` 列。
2. **时间上下文** —— 取 `now-time-info`：深夜过滤早餐、周一触发 `monday_blues`、每月 10 日前后触发 `payday`、周日晚触发 `sunday_dread`。
3. **强度系数** —— 感叹号数量、极端词（「想死」「爆炸」）会放大失血值，上限 95（**不设 100**，否则所有方案都判「不够用」，体验很挫败）。

---

## 4. Step 2 —— 抓取实时数据（⚠️ 顺序是铁律）

```
第一批（并行，无依赖）
  ├─ now_time_info()          → 时间上下文
  ├─ query_nearby_stores()    → 门店（真实场景必须传 --address）
  ├─ query_meals(store_id)    → 菜单
  └─ list_nutrition_foods()   → 营养表（TTL 24h）

第二批（有依赖，必须串行）
  ├─ available_coupons()      → 还能领几张（仅展示）
  ├─ auto_bind_coupons()      → ⭐ 一键领满
  └─ query_store_coupons()    → ⭐ 这家店真正能用的
```

> ⚠️ **顺序铁律：`auto_bind_coupons` 必须在 `calculate_price` 之前。**
> 反了算出来的是「没券的价格」，省钱的爽感直接归零。
> 同理，`query_store_coupons` 必须在 `auto_bind_coupons` 之后，不能塞进第一批并行。

并行后数据抓取从 7 次串行往返压到 3 次。

**限流**：官方 600 次/分钟/Token，本地滑动窗口保守设 540（留 10% 水位），429 读 `Retry-After` + 指数退避（0.5→1→2→4s）。

---

## 5. Step 3 —— ⭐ 本地口味标注（最容易踩的坑）

**官方 `query-meals` 只返回名称 / 分类 / 编码 / 价格，没有口味向量。**
直接喂进 RP 公式 → 全是零向量 → 余弦相似度恒为 0 → **推荐彻底退化成按热量乱排**。

实测对照（同一份菜单、同一句「加班到想死」）：

```
未标注  →  RP 2.4    （等于随机推荐）
已标注  →  RP 55.2   （准确命中「汉堡 + 冰拿铁」）
```

所以必须有这一层：`data/mappings/flavor_map.json`，匹配优先级 **精确名 > 关键词 > 分类 > 默认值**，
来源写进 `item.tags`（形如 `anno:kw:拿铁`）便于审计。

```bash
python <SKILL_DIR>/scripts/main.py "加班" --verbose
# stderr 会打出：[anno] 标注覆盖 26/26 (100%)
```

覆盖率低于 80% 就要补映射表规则了——这是接真实接口后第一个要盯的指标。

---

## 6. Step 4–5 —— 续命值 RP 与券优化

### 续命值公式

```
RP = 口味×40 + 热量×20 + 功能×20 + 取向×20 + 优惠暴击×20     （上限 100）
判定：RP ≥ 失血值 BS  →  「够用，能撑住」✅
     RP < 失血值 BS  →  「不太够，但这是最能救的了」⚠️
```

**⭐ RP 必须在套餐维度算，不能取单品平均分。**
加班要咖啡因，但汉堡 `caffeine=0`；按单品平均会把「汉堡 + 冰美式」直接误杀。
正确做法：**口味取平均向量、热量取总量、功能取最大能力值**。

### 券优化（含凑单悖论）

`deal_calculator.optimize()` 做券组合搜索，并会主动提示：

> 🎯 凑单提示：加购「麦辣鸡翅(2块)」（¥13.00）后触发满减，实付反而少 ¥2.00

凑单品**不按最便宜选**，而是「超出差额 / 3」分档后再优先情绪分高的——凑单也要好吃。

**🔒 绝不自己算钱**：本地只做券组合**搜索**，最终金额一律以 `calculate-price` 返回为准，避免与官方价格不一致。

---

## 7. Step 6 —— 输出与文案

### 三种文体

| `--style` | 文体 | 适用 |
|---|---|---|
| `crazy`（默认） | 发疯文学 | 被气死、加班崩溃 |
| `report` | 打工人续命日报 | 周五、发工资 |
| `flex` | 反向凡尔赛 | 发工资、emo |

### 完整命令参考

```bash
# 基础
python <SKILL_DIR>/scripts/main.py "累成狗"
python <SKILL_DIR>/scripts/main.py "发工资了！！！" --budget 60 --style flex
python <SKILL_DIR>/scripts/main.py "甲方要五彩斑斓的黑，我笑了" --style crazy

# 进阶
python <SKILL_DIR>/scripts/main.py "想摸鱼" --json            # 输出 JSON 便于调试
python <SKILL_DIR>/scripts/main.py "加班" --live --verbose     # 接真实 MCP + 调用埋点
python <SKILL_DIR>/scripts/main.py "被气死了" --confirm        # ⚠️ 真正下单
```

| 参数 | 作用 |
|---|---|
| `--budget N` | 预算上限（元） |
| `--address "xxx"` | 门店地址关键词 |
| `--top N` | 展示几个方案（默认 3） |
| `--style` | `crazy` / `report` / `flex` |
| `--json` | 输出 JSON 而非 Markdown |
| `--live` | 接真实 MCP（需 `MCD_MCP_TOKEN`） |
| `--mock` | 强制离线演示 |
| `--auto-mock` | 真实接口失败时自动降级 |
| `--confirm` | **允许下单**（默认永不） |
| `--verbose` | 调用埋点 + 耗时打到 stderr |

---

## 8. 🛡️ 安全红线（不可绕过）

| # | 红线 | 实现 |
|---|---|---|
| 1 | **默认干跑，永不自动下单** | 未传 `--confirm` 时 `create_order()` 直接抛 `OrderNotConfirmedError` |
| 2 | **写接口永不降级到 Mock** | `calculate-price` / `create-order` 失败就报错，绝不用假数据冒充真实价格 |
| 3 | **绝不本地算钱** | 金额一律取 `calculate-price` 返回值 |
| 4 | **只读降级需显式开启** | 只有 `--auto-mock` 才降级，且计数进 `stats["mock_fallbacks"]` 可观测 |
| 5 | **Mock 输出必须标明** | 输出顶部有「⚠️ 当前为离线演示模式」横幅 |
| 6 | **心理危机优先于点餐** | 见 `references/safety-rules.md`，检测到自伤倾向先给援助热线，再谈吃什么 |

> 第 6 条不是可选项。用户说「不想活了」时，**任何推荐都是不恰当的**。

---

## 9. 降级容错

| 故障 | 兜底 |
|---|---|
| 无 Token / 401 | 提示后切 Mock，输出横幅说明 |
| 429 限流 | 指数退避 + 菜单缓存 TTL 10min |
| 券被抢完 | 排除该券重算，最多 3 轮 |
| 会话过期（404） | 自动重新握手重试一次 |
| 断网 | `--auto-mock` 完整跑通全流程 |

> 🎤 答辩话术：**「我们假设 MCP 会挂。」** —— 可以当场断网演示。

---

## 10. 测试

```bash
python <ROOT>/tests/run_all.py       # 70 项断言，免 Token、免网络
```

三个套件：`test_smoke.py`（业务逻辑 27 项）、`test_live_transport.py`（本地假服务端验传输协议 30 项，含 429/404 故障注入）、`test_e2e_fake_server.py`（拟真服务端跑完整流程 13 项）。

---

## 11. 参考文档（按需加载）

| 文件 | 何时读 |
|---|---|
| `references/mood-matrix.md` | 需要调情绪规则、新增情绪、查关键词与反讽标记时 |
| `references/mcp-tools.md` | 需要查某个 Tool 的入参/返回、新增接口时 |
| `references/copywriting-bank.md` | 需要扩写文案、调整文体风格时 |
| `references/safety-rules.md` | ⭐ 涉及心理危机、未成年人、过敏、隐私时**必读** |

---

## 12. 已知限制（诚实清单）

- `stackable` 目前硬编码为 True，真实券若不可叠加需从券详情读取（接真 Token 后第一优先级）
- 营养表按商品名精确匹配，真实菜单带规格后缀（如「可乐(中杯)」）时可能缺失
- `create_order` 返回的 `orderId` / `payUrl` / `pickupCode` 键名待真实数据回填
- 海报 PNG、续命账单、Web Demo 在路线图中，未实现

---

*McDonald's® 是麦当劳公司的注册商标，本项目为独立开发的技术演示，与麦当劳公司无隶属或授权关系。详见仓库根目录 `NOTICE.md`。*
