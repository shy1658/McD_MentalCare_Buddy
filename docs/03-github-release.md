# 📦 发布到 GitHub + MCP 接入说明建议

> 面向 2026 麦当劳程序员创意开发大赛（M-CODE）的提交指南

---

## 一、发布前的自检清单

在 `git push` 之前，逐项确认（**漏掉任何一项都可能被扣分甚至违规**）：

| # | 检查项 | 命令 / 位置 | 不做的后果 |
|:--:|---|---|---|
| 1 | **没有 Token 入库** | `git grep -n "Bearer " ; git status` | 🔴 严重违规 |
| 2 | `.env` 被忽略 | `git check-ignore -v .env` | 🔴 泄露凭证 |
| 3 | Mock 数据已标注 | `data/mock/*.json` 顶部注释 + 运行时横幅 | 🟡 被误认为伪造数据 |
| 4 | 离线可跑通 | `python main.py "累成狗"`（不设 Token） | 🟡 评委跑不起来 |
| 5 | 冒烟测试全绿 | `python tests/test_smoke.py` | 🟡 完成度存疑 |
| 6 | 参赛声明齐 | `NOTICE.md` + `docs/02-mcp-integration.md` | 🔴 大赛硬性要求 |
| 7 | 无真实用户数据 | 检查 `.mcd_profile.json` 是否已删 | 🟡 隐私问题 |

### 一条命令做安全自检

```bash
# 检查是否有疑似 Token 的字符串（40 位以上字母数字）
git grep -nE "[A-Za-z0-9]{40,}" -- . ':!docs' || echo "未发现疑似 Token ✅"
```

---

## 二、首次发布流程

### 1. 本地初始化

```bash
cd McD_MentalCare_Buddy

git init
git add .
git status          # ⚠️ 确认没有 .env / mcp_token / 真实 Token 被 add 进去

git commit -m "feat: 麦麦精神续命搭子 v0.1.0 — 情绪驱动的点餐搭子

- mcp_client: 15 个麦当劳官方 MCP Tool 封装 + Mock 降级
- mood_engine: 12 种打工人精神状态识别 + RP 套餐维度打分
- deal_calculator: 券组合搜索 + 凑单悖论优化
- copywriting: 发疯文学 / 续命日报 / 反向凡尔赛
- 已实现 --dry-run 安全闸门与 --mock 离线演示"
```

### 2. 创建远程仓库并推送

```bash
# 用 GitHub CLI（推荐）
gh repo create McD_MentalCare_Buddy \
  --public \
  --source=. \
  --description="🍔 麦麦「打工人精神续命」搭子 · 听得懂你今天有多惨，并给你算好券 · 麦当劳 MCP + Agent Skill" \
  --push

# 或手动
git remote add origin https://github.com/<your-github-username>/McD_MentalCare_Buddy.git
git branch -M main
git push -u origin main
```

### 3. 仓库设置（网页端）

- **Topics**（影响被发现）：`mcdonalds` `mcp` `agent-skill` `hackathon` `m-code-2026` `python` `ai-agent` `llm`
- **About**：填一句话简介 + 官网链接（你的 Demo 视频或 README）
- **默认分支**：`main`
- 勾选 Issues / Discussions（评审可能会留言）

### 4. 打 Tag 并发布 Release

大赛通常要求固定版本，建议打 `v0.1.0`：

```bash
git tag -a v0.1.0 -m "M-CODE 2026 参赛提交版本"
git push origin v0.1.0

gh release create v0.1.0 \
  --title "v0.1.0 · M-CODE 2026 参赛版" \
  --notes-file CHANGELOG.md
```

---

## 三、README 里必须出现的 7 件事

大赛评审通常先看 README。按重要性排序：

1. **一句话定位**（前 3 行内讲清「你和别的点餐 AI 有什么不同」）
2. **Demo GIF**（3 秒，胜过千字）
3. **30 秒上手**（复制粘贴就能跑，**默认走 Mock**，别让人先去申请 Token）
4. **用到哪些 MCP Tool**（15 个真实 Tool 名，证明真接了官方能力）
5. **架构图 / 调用时序**（证明工程性，不是玩具）
6. **参赛声明入口**（链到 `NOTICE.md`）
7. **目录结构**（让人一眼找到代码在哪）

> 💡 反面教材：把安装步骤写成 `pip install xxx && 申请 Token && 配置 ...` 一大段。
> 评委没有耐心。第一屏就要能跑：`python main.py "累成狗"`。

---

## 四、MCP 接入说明的建议

大赛明确要求「MCP 接入说明」。建议**单独成文**（`docs/02-mcp-integration.md`），并在 README 用一小节链接过去。内容建议按下面 6 段组织：

### 段落 1：接入信息表（一眼能对）

| 项 | 值 |
|---|---|
| 地址 | `https://mcp.mcd.cn` |
| 协议 | Streamable HTTP |
| 认证 | `Authorization: Bearer <TOKEN>` |
| 限流 | 600 次/分钟 |
| 错误码 | 401 / 429 |

**为什么重要**：评审看到这张表就知道你真接过，而不是调了个假接口。

### 段落 2：Tool 选型表（证明你懂取舍）

不要只列「用了哪些」，还要列「**没用哪些，以及为什么**」：

```markdown
| Tool | 用/不用 | 原因 |
|------|--------|------|
| query-meals | ✅ P0 | 实时菜单 + 售罄标记 |
| auto-bind-coupons | ✅ P0 | 一次调用领满券（神级接口） |
| delivery-create-address | ❌ | 地址写操作，风险高，引导用户去 App 维护 |
| query-party-* | ❌ | 主题活动派对，MVP 不碰 |
```

**加分点**：主动说明不用的接口 = 有边界感 = 专业。

### 段落 3：调用时序（证明你懂编排）

重点讲清楚 **3 个反直觉决策**：

1. **先 `auto-bind-coupons` 再 `calculate-price`** —— 顺序反了算出来是「没券的价格」
2. **四条线并行**（菜单/门店/券/营养）—— 首屏从 8s 压到 3s
3. **绝不本地算钱** —— 本地只搜索券组合，金额一律以 `calculate-price` 为准

### 段落 4：降级容错（证明你想过生产环境）

| 故障 | 兜底 |
|---|---|
| 401 / 无 Token | 自动切 `--mock`，横幅提示「离线演示」 |
| 429 限流 | 指数退避（0.5→1→2→4s）+ 菜单缓存 TTL 10min |
| 券被抢完 | 排除该券重算，最多 3 轮 |
| 断网 | Mock Server 完整跑通 |

**必杀话术**：*"我们假设 MCP 会挂。"* —— 答辩时当场断网演示。

### 段落 5：安全与合规

- Token 只存环境变量 / `umask 077` 文件，日志脱敏为 `***abcd`
- `create-order` 前必须有显式确认（代码里就是 `confirm=True` 才放行）
- 情绪画像只存本地，支持导出/删除

### 段落 6：真实接入时的 TODO 清单

把代码里的 `TODO(real-mcp)` 集中列出来，让评审看到**你清楚哪些是占位、哪些是真实现**：

```markdown
- [ ] `_call()` 中实现 tools/call 的 HTTP 请求（含 initialize 握手 + SSE 解析）
- [ ] `list_nutrition_foods()` 解析官方 toon 紧凑格式文本
- [ ] 从券详情读取 stackable 字段，替掉 StackPolicy 的硬编码假设
- [ ] `calculate_price` 返回值与本地估算的一致性校验（差异 > 0.01 元则中止）
```

---

## 五、推荐的仓库文件顺序

评审打开仓库看到的第一屏（GitHub 会按字母序排），建议这样命名让关键文件靠前：

```
CHANGELOG.md     ← 变更记录
LICENSE          ← MIT
NOTICE.md        ← 参赛声明（醒目）
README.md        ← 主文档
main.py          ← 主入口（根目录，别藏进子目录）
requirements.txt
mcd_mood_buddy/  ← 核心包
data/            ← Mock 数据
docs/            ← 文档
tests/           ← 测试
```

**关键**：`main.py` 放根目录。评委 `git clone` 后第一反应是找入口，藏太深会被认为完成度低。

---

## 六、提交信息规范（可选但对团队协作有用）

```
feat:     新功能
fix:      修 bug
docs:     文档
refactor: 重构
perf:     性能优化
test:     测试
chore:    构建/工具
```

示例：
```
feat(deal_calculator): 凑单品加入情绪偏好排序
fix(models): 修正 saved 字段把应付额当成省钱额
docs: 补充 MCP 接入说明的降级容错章节
```

---

## 七、演示视频录制建议（大赛常要求）

| 时间 | 内容 |
|:--:|---|
| 0:00–0:15 | 抛痛点：所有点餐 AI 都在回答「吃什么」，没人问「你今天有多惨」 |
| 0:15–0:50 | 现场输入「甲方要五彩斑斓的黑，我笑了」→ 反讽识别 → 泄愤流 → 发疯文学 |
| 0:50–1:30 | 展示凑单悖论：多买一个反而省 ¥2（**这是最抓人的一屏**） |
| 1:30–2:00 | 技术点：RP 公式 + 并行拉取 + 断网演示 `--mock` |
| 2:00–2:30 | 「想摸鱼」→ 展示克制（不推大堡，因为吃完会犯困） |
| 2:30–3:00 | 发疯文学海报墙 + 收尾 |

> 录屏建议用 `python main.py "..." --top 3` 的终端输出，配 OBS。
> 终端字体调到 16px 以上，否则录出来看不清。
