# 🍔 McD_MentalCare_Buddy · 麦麦「打工人精神续命」搭子

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square)

![MCP](https://img.shields.io/badge/MCP-麦当劳官方%20v1.0.9-FFC72C?style=flat-square)



![Deps](https://img.shields.io/badge/依赖-零第三方-97C459?style=flat-square)

![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)

![Contest](https://img.shields.io/badge/Contest-M--CODE%202026-DA291C?style=flat-square)

> ### 你不是一个人在饿，你是一个人在崩溃。
>
> 一个**听得懂你今天有多惨**，并**给你算好券**的麦当劳 AI 搭子。  
> 它不是点餐助手。它是那个会在你说「我不想活了」的时候，  
> 回你一句「先吃个麦辣鸡腿堡，我给你算完券只要 23 块」的朋友。

---

## 🤖 这是一个 Skill

**[`skill/SKILL.md`](skill/SKILL.md) 是本项目的大赛核心交付物** —— 情绪矩阵、MCP 调用顺序铁律、
安全红线、降级策略都在里面。想直接看"它作为 Skill 怎么用"，从那里开始。

## ⚡ 30 秒上手（无需 Token、无需网络）

```bash
git clone https://github.com/<your-github-username>/McD_MentalCare_Buddy.git
cd McD_MentalCare_Buddy

python main.py "还有40个bug要改 我想死"
# 等价于 Skill 入口：python skill/scripts/main.py "还有40个bug要改 我想死"
```

就这么简单。**零第三方依赖**，默认走内置 Mock 数据，完整跑通从情绪识别到发疯文学的全链路。

```bash
python main.py --list-moods                              # 看看支持哪些精神状态
python main.py "发工资了！！！" --budget 60 --style flex   # 限预算 + 凡尔赛文体
python main.py "想摸鱼" --json                            # 输出 JSON 便于调试
python main.py "被气死了" --confirm                       # 真正下单（默认不会）
python main.py --doctor --live                            # 体检真实 MCP 链路
python main.py "加班" --live --auto-mock --verbose        # 接真实 MCP，断网自动降级
```

> 🛡️ **默认 `--dry-run`，永不自动下单。** 只有显式 `--confirm` 才会调用 `create-order`。
>
> 🔌 **`MCD_MCP_TOKEN` 配好就能 `--live`。** 传输层（握手 / SSE / toon / 限流 / 会话自愈）  
> 已按官方 v1.0.9 完整实现，并对 `https://mcp.mcd.cn` 做过实机探针验证。

---

## 💡 为什么做这个

所有麦当劳 AI 都在回答 **「吃什么」**。  
但打工人真正的问题是 —— **「我今天这么惨，我该吃什么才对得起自己？」**

我们把它变成了一道**可计算的题**：

```
你有多惨（失血值 BS）  ×  这顿饭有多治愈（续命值 RP）
        ↓                          ↓
   情绪识别引擎              套餐 + 最优券组合
        └──────────┬───────────────┘
                   ↓
        一键领券 → 算实付 → 你点头才下单
                   ↓
              🎴 发疯文学文案
```

**一句话差异**：传统点餐是「人找券」，我们是「**情绪找套餐 + 券 + 情绪出口**」。

---

## 🫂 它会拒绝给你推荐

一个主动拥抱负面情绪的产品，最危险的地方就在这里。
用户说「我不想活了」，回一句「麦辣鸡腿堡券后 23 块」——这不是贴心，是把求救当消费场景。

所以 `mcd_mood_buddy/safety.py` 在**任何 MCP 调用之前**做一次危机检测：

| 档位 | 触发 | 系统行为 |
|---|---|---|
| `CRISIS` | 告别式 / 绝望 / 自伤词且无吐槽对象 | **短路**：零 MCP 调用、零推荐，只给援助热线 |
| `CONCERN` | 自伤词 + 明确吐槽语境（「改 bug 改到想死」） | 不阻断，顶部附关怀 + 热线，**强制改用克制文体** |
| `NONE` | — | 正常流程 |

```bash
$ python main.py "我不想活了"
# 🍔 麦麦精神续命搭子
## 🫂 先停一下
我听到你说的话了，而且我认真对待。
🆘 全国 24 小时心理援助热线：12356
...
> 本次未调用任何麦当劳 MCP 接口，未生成任何点餐推荐。
```

**为什么不「见词就阻断」**：加班吐槽里「想死」是高频修辞，每次都弹热线，
真正的求救信号反而会被稀释（狼来了效应）。但**告别式表达永不降档**——
「bug 改不完了，以后不用管我了」照样判 `CRISIS`。

这条线有 **55 项断言**守着，失败即 P0。规则全文见 `skill/references/safety-rules.md`。

---

## ✨ 五个亮点

|  #  | 亮点             | 说明                                               |
| :-: | -------------- | ------------------------------------------------ |
| 🫂 | **心理危机干预**     | ⭐ 检测到求救信号时**闭嘴、不点餐、先给热线**——见下方专节             |
|  🩸 | **续命值系统**      | `RP = 口味×40 + 热量×20 + 功能×20 + 优惠暴击×20`，四项可拆解成进度条 |
| 🗺️ | **12 种精神状态**   | 每种状态一个四维口味向量 + 功能诉求（咖啡因 / 咀嚼泄愤 / 糖分疗愈）           |
|  🎯 | **凑单悖论求解**     | 自动算出「多买一个反而更便宜」的反直觉最优解                           |
|  🤯 | **发疯文学生成**     | 三种文体（发疯 / 日报 / 凡尔赛），点完单直接发朋友圈                    |
|  🔌 | **真实 MCP 已打通** | 握手 / SSE / toon / 限流退避 / 会话自愈，并按实施探针校准过错误处理      |
|  🧂 | **本地口味标注层**    | 官方菜单不带口味数据？我们补。实测 RP 从 **2.4 → 55.2**            |
| 🛡️ | **断网也能跑**      | 内置 Mock 数据 + `--auto-mock`，无 Token 无网络照样完整演示     |

### 🔌 真实链路已就绪（不是 TODO）

```bash
export MCD_MCP_TOKEN="<你的鉴权码>"
python main.py --doctor --live
```

```text
🩺 MCP 链路体检  ——  https://mcp.mcd.cn
   模式 live ｜ Token ***9f3a
--------------------------------------------------------------
✅ MCP 握手              耗时 128ms，会话 已建立
✅ 协议版本              服务端 2025-06-18 / 本地 2025-06-18
✅ now-time-info         服务端时间 2026-10-10 23:47:00（RTT 61ms）
✅ Tool 清单校验         服务端共 33 个 Tool，本项目用 15 个，全部可用 ✅
✅ 限流配额              本分钟剩余 537/540 次（官方上限 600/分钟）
--------------------------------------------------------------
结论：链路健康，可以跑 `--live` 了 ✅
```

实机探针记录（2026-10-10 对 `https://mcp.mcd.cn`）：

| 请求形态                 | HTTP  | 响应体                                        |
| -------------------- | ----- | ------------------------------------------ |
| 不带 `Authorization` 头 | `403` | `{"code":"0001","msg":"系统错误","data":null}` |
| Bearer 无效 Token      | `401` | `当前鉴权码不存在或已失效!`                            |

### 🧂 隐藏的第五层：口味标注

官方 `query-meals` 只给「名称/分类/编码/价格」，**没有口味向量**。  
直接喂给 RP 公式，所有商品都是零向量 → 余弦相似度恒为 0 → 推荐彻底退化。

```text
同一份菜单、同一句「加班到想死」的实测对照：
    未标注  →  RP 2.4    （等于随机推荐）
    已标注  →  RP 55.2   （准确识别出「汉堡 + 冰拿铁」的提神组合）
```

解法是 `mcd_mood_buddy/annotation.py` + `data/mappings/flavor_map.json`，  
匹配优先级 **精确名 → 关键词 → 分类 → 默认**，规则写在 JSON 里（调参不用改代码），  
每个标注的来源写进 `item.tags`（如 `anno:kw:拿铁`），可审计、可覆盖。

### 凑单悖论（实测输出）

```
> 🎯 凑单提示：加购「麦辣鸡翅(2块)」（¥13.00）后触发满减，
   实付反而少 ¥2.00 —— 多拿一个还更便宜。
```

---

## 🧮 续命值 RP 公式

```
RP = 40 × taste_fit    # 口味契合度（情绪向量与套餐平均向量的余弦相似度）
   + 20 × energy_fit    # 热量契合度（按整餐总热量判断）
   + 20 × func_fit      # 功能契合度（咖啡因提神 / 咀嚼泄愤 / 糖分疗愈）
   + 20 × value_fit     # 消费取向（抠门看每元热量，犒赏看仪式感）
   ─────────────────
   + 20 × deal_crit     # 优惠暴击（折扣率 40% 即满分，由 deal_calculator 补齐）
```

**匹配目标**：`RP ≥ 失血值 BS`，且 `实付 ≤ 预算`。

> ⚠️ **关键设计**：RP 必须在**套餐维度**计算，不能平均单品分数。  
> 反例：加班需要咖啡因，而汉堡的 caffeine = 0。若按单品平均，  
> 「汉堡 + 冰美式」会被汉堡拖死 —— 但提神明明由那杯咖啡负责。  
> 所以口味用**平均**向量、热量用**总量**、功能用**最大**能力值。  
> 见 `mood_engine.score_bundle()`，测试 `tests/test_smoke.py::test_bundle_caffeine`。

---

## 🔌 MCP 接入

对接**麦当劳中国官方 MCP Server v1.0.9**（共 33 个 Tool，本项目使用 15 个）。

| 项   | 值                               |
| --- | ------------------------------- |
| 地址  | `https://mcp.mcd.cn`            |
| 协议  | Streamable HTTP                 |
| 认证  | `Authorization: Bearer <TOKEN>` |
| 限流  | 600 次 / 分钟 / Token              |
| 错误码 | `401` Token 无效 ｜ `429` 触发限流     |

### 用到的官方 Tool

```
now-time-info          query-nearby-stores      query-meals           query-meal-detail
query-store-coupons    available-coupons        auto-bind-coupons     query-my-coupons
calculate-price        create-order             query-order           list-nutrition-foods
query-my-account       campaign-calendar        order-list
```

### 三个反直觉决策

1. **先 `auto-bind-coupons` 领满券，再 `calculate-price` 算价** —— 顺序反了算出来是「没券的价格」
2. **按依赖切两批并行**（时间+门店+营养 ‖ 菜单+可领券+一键领券）—— **7 次往返压缩到 3 次**
3. **绝不本地算钱** —— 本地只搜索券组合，金额一律以 `calculate-price` 返回为准
4. **content 要 `json.loads` 两次** —— 信封一层 + 业务数据一层，只解一次会拿到字符串

### 降级容错

| 故障                  | 兜底                                           |
| ------------------- | -------------------------------------------- |
| 无 Token / 401 / 403 | `--auto-mock` 时切 Mock 并 warn；否则直接报错，**不藏问题** |
| 429 限流              | 听服务端 `Retry-After`，否则指数退避（0.5→1→2→4s）        |
| 会话失效 404            | **自动重新握手后重试一次**，无需人工干预                       |
| 券被抢完                | 排除该券重算，最多 3 轮                                |
| 断网                  | 只读接口降级到 Mock；**写接口（算价/下单）绝不降级**              |

> 🎤 答辩话术：**"我们假设 MCP 会挂 —— 但挂的方式不止一种。"**  
> 可以当场断网演示，也可以当场演示「会话过期自动重连」。

详细接入说明见 [`docs/02-mcp-integration.md`](docs/02-mcp-integration.md)。

---

## 📁 目录结构

```
McD_MentalCare_Buddy/
├── skill/                       # 🤖 WorkBuddy Skill 本体（大赛核心交付物，放最前）
│   ├── SKILL.md                 # ⭐ Skill 主入口 —— 评审第一个看的
│   ├── references/
│   │   ├── mood-matrix.md       # 12 种情绪矩阵（由代码自动生成）
│   │   ├── mcp-tools.md         # 15 个官方 Tool 速查 + 不用哪些及为什么
│   │   ├── copywriting-bank.md  # 发疯文言语料库（由代码自动生成）
│   │   └── safety-rules.md      # ⭐ 心理危机干预 + 安全红线
│   └── scripts/
│       ├── main.py              # Skill 入口（转发到根目录，单一实现源）
│       └── gen_references.py    # 一键重新生成参考文档，避免文档与代码脱节
├── main.py                      # ⭐ CLI 主入口（放根目录，评委好找）
├── mcd_mood_buddy/
│   ├── __init__.py
│   ├── safety.py                # ⭐ 心理危机检测（三档）+ 干预，任何 MCP 调用之前短路
│   ├── models.py                # 数据模型（MCP 结构与算法结构的中间层）
│   ├── mcp_client.py            # ⭐ 15 个官方 Tool 封装 + Streamable HTTP 全实现
│   ├── annotation.py            # ⭐ 本地口味标注层（真实菜单不含口味向量时的救星）
│   ├── mood_engine.py           # ⭐ 情绪识别 + RP 套餐维度打分
│   ├── deal_calculator.py       # ⭐ 券组合搜索 + 凑单悖论优化
│   ├── copywriting.py           # 发疯文学 / 续命日报 / 反向凡尔赛
│   └── renderer.py              # Markdown 渲染
├── data/
│   ├── mock/                    # Mock 数据（menu / coupons / stores / nutrition）
│   └── mappings/flavor_map.json # 口味映射规则（调参不用改代码）
├── docs/
│   ├── PRD.md                   # 完整需求文档
│   ├── 02-mcp-integration.md    # MCP 接入说明（大赛硬性要求）
│   └── 03-github-release.md     # 发布与提交指南
├── tests/
│   ├── run_all.py               # ⭐ 一键跑全部套件（共 125 项断言）
│   ├── test_safety.py           # ⭐ 心理危机干预（55 项，安全底线）
│   ├── test_smoke.py            # 业务逻辑冒烟（27 项）
│   ├── test_live_transport.py   # MCP 传输层协议（30 项，含本地假服务端）
│   └── test_e2e_fake_server.py  # 拟真服务端上的完整点餐链路（13 项）
├── NOTICE.md                    # 参赛声明与商标声明
└── LICENSE                      # MIT（+ 商标例外说明）
```

---

## 🧪 测试

```bash
python tests/run_all.py          # 全部四套，共 125 项断言
```

| 套件                        |  断言 | 覆盖                                                                         |
| ------------------------- | :-: | -------------------------------------------------------------------------- |
| `test_safety.py`          |  55 | ⭐ 心理危机三档判定、告别式不降档、**CRISIS 不得产生点餐内容**、文体强制降级、热线完整性 |
| `test_smoke.py`           |  27 | 情绪识别（直述/反讽/时间上下文）、RP 套餐打分（咖啡因反例）、券组合与凑单、安全闸门                               |
| `test_live_transport.py`  |  30 | 握手、纯 JSON / SSE 双信封、429 退避重试、会话失效自愈、toon 解析、tools/list 差分、doctor 自检、写接口禁降级 |
| `test_e2e_fake_server.py` |  13 | 在拟真服务端（菜单不带口味字段）跑完整 `main.run()`，验证标注层把 RP 从 2.4 拉到 55.2                   |

> 后两套用**本地假 MCP Server** 复刻官方协议，不需要 Token、不需要网络，  
> 还能故意返回 429 / 404 逼出重试与自愈路径。

---

## 🗺️ 路线图

- [x] **P0** 情绪识别 · 菜单/券匹配 · 领券 · 算价 · 下单确认 · 发疯文学
- [ ] **P1** LLM 情绪兜底（低置信度时）· 海报 PNG 渲染 · 积分/活动彩蛋
- [ ] **P2** 记忆模块 · 续命周报/账单 · Web Demo

### ✅ 已完成（前两个 TODO 已关闭）

- [x] `mcp_client._call()` — `initialize` 握手 + `tools/call` + SSE/JSON 双形态解析 + 会话自愈
- [x] `list_nutrition_foods()` — toon 紧凑格式解析器（含 kJ→kcal 换算、带引号逗号容错）
- [x] 口味标注层 — 真实菜单零口味字段时救活 RP（2.4 → 55.2）
- [x] `verify_tools()` — 与服务端 `tools/list` 做差集，官方改名立刻报警
- [x] `--doctor` — 六项链路体检
- [x] 并行抓取 — 7 次往返压缩到 3 次

### ⬜ 剩余 TODO

- [ ] `StackPolicy` — 改为从券详情读取 `stackable` 字段，替掉硬编码假设
- [ ] `calculate_price` — 与本地估算做一致性校验（差异 > 0.01 元则中止下单）
- [ ] `create_order` 真实响应结构确认（拿到真 Token 后回填 `orderId` / `payUrl` / `pickupCode` 字段）

搜一下就能全找到：

```bash
grep -rn "TODO(real-mcp)" mcd_mood_buddy/
```

---

## ⚖️ 参赛声明

- 本项目为 **2026 麦当劳程序员创意开发大赛（M-CODE）参赛作品**，**非麦当劳官方产品**。
- 菜单、价格、优惠券、订单数据均通过麦当劳官方 MCP 实时获取；Mock 数据仅用于离线演示并已显著标注。
- **不构成对麦当劳及其关联方商标的任何授权**。
- 情绪相关内容为**娱乐化表达**，不构成心理诊疗或医疗建议。
- 详见 [`NOTICE.md`](./NOTICE.md)。

---

<div align="center">  
<b>每个打工人，都值得被好好喂一顿。</b>  
  
<i>© 2026 · 价格与库存以麦当劳 MCP 实时返回为准</i>  
</div>
