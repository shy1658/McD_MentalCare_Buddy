"""
参考文档生成器
==============

从**代码的真实数据**生成 `skill/references/` 下的文档，避免手写文档与实现脱节。

    python skill/scripts/gen_references.py

生成物::

    references/mood-matrix.md         ← mood_engine.MOOD_LIBRARY（12 种情绪）
    references/copywriting-bank.md    ← copywriting.TEMPLATES（3 种文体）

为什么不让文档手写？
--------------------
情绪库和语料库是本项目迭代最频繁的两块数据。手写 markdown 必然会出现
「文档写着 12 种情绪，代码里其实已经加到 15 种」这种不一致，
而评审是**会对照代码看的**。生成即唯一真相源（single source of truth）。

注意：`mcp-tools.md` 与 `safety-rules.md` 含大量无法从代码推断的信息
（入参语义、限流规则、危机干预话术），保持手写，不纳入自动生成。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcd_mood_buddy.copywriting import STYLE_CRAZY, STYLE_FLEXY, STYLE_REPORT, TEMPLATES  # noqa: E402
from mcd_mood_buddy.mood_engine import MOOD_LIBRARY  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "references"

STYLE_LABEL = {
    STYLE_CRAZY: "发疯文学",
    STYLE_REPORT: "打工人续命日报",
    STYLE_FLEXY: "反向凡尔赛",
}


def _bar(score: float, width: int = 20, full: str = "▓", empty: str = "░") -> str:
    """失血值进度条，与 CLI 输出保持一致"""
    n = int(round(score / 100 * width))
    return full * n + empty * (width - n)


def gen_mood_matrix() -> str:
    lines: list[str] = []
    w = lines.append

    w("# 情绪—口味映射矩阵")
    w("")
    w("> 🤖 **本文件由 `skill/scripts/gen_references.py` 自动生成**，数据源 `mood_engine.MOOD_LIBRARY`。")
    w("> 修改情绪库后请重新运行生成命令，**不要直接编辑本文件**。")
    w("")
    w("---")
    w("")
    w("## 1. 总览")
    w("")
    w("| mood_id | 标签 | 失血值 BS | 强度条 | 热量取向 | 性价比取向 | 出餐速度 | 功能诉求 | 文案基调 |")
    w("|---|---|---|---|---|---|---|---|---|")
    for p in MOOD_LIBRARY.values():
        w(
            f"| `{p.mood_id}` | {p.label} | **{p.bleed_score}** | `{_bar(p.bleed_score, 12)}` "
            f"| {p.kcal_pref.value} | {p.value_pref.value} | {p.speed_pref.value} "
            f"| {p.func_need or '—'} | {p.copy_tone} |"
        )
    w("")
    w("**字段含义**")
    w("")
    w("| 字段 | 说明 |")
    w("|---|---|")
    w("| 失血值 BS | Bleed Score，你有多惨（0–95，上限刻意不设 100，见 §4） |")
    w("| 热量取向 | HIGH 高热量 / MID 中等 / LOW 低热量 |")
    w("| 性价比取向 | DEAL 追求优惠 / PREMIUM 愿意为品质付费 |")
    w("| 出餐速度 | FAST 要求快 / ANY 无所谓 |")
    w("| 功能诉求 | `caffeine` 提神 / `chew` 耐嚼泄愤 / `sugar` 糖分 / `none` 无 |")
    w("| 文案基调 | 决定发疯文学语料走哪一组 |")
    w("")
    w("---")
    w("")
    w("## 2. 口味向量明细")
    w("")
    w("四个维度：`salty` 咸香 ｜ `chewy` 耐嚼 ｜ `sweet` 甜 ｜ `caffeine` 咖啡因（取值 0–1）")
    w("")
    w("| mood_id | salty | chewy | sweet | caffeine | 解读 |")
    w("|---|---|---|---|---|---|")
    for p in MOOD_LIBRARY.values():
        f = p.flavor
        w(
            f"| `{p.mood_id}` | {f.salty} | {f.chewy} | {f.sweet} | {f.caffeine} | {_flavor_reading(p)} |"
        )
    w("")
    w("> 向量用于与商品口味向量算**余弦相似度**，命中 RP 公式中占比最重的「口味 40 分」。")
    w("")
    w("---")
    w("")
    w("## 3. 命中关键词")
    w("")
    w("命中即判定为该情绪；多个情绪同时命中时取**失血值最高**的那个（人总是先觉得自己惨）。")
    w("")
    for p in MOOD_LIBRARY.values():
        w(f"### `{p.mood_id}` · {p.label}")
        w("")
        w("```")
        w("、".join(p.keywords))
        w("```")
        w("")
    w("---")
    w("")
    w("## 4. 反讽识别")
    w("")
    w("**为什么需要**：「甲方要五彩斑斓的黑，**我笑了**」—— 字面是开心，实际是暴怒。")
    w("纯关键词匹配会把这句判成正面情绪，推荐出一堆甜品，荒谬且伤人。")
    w("")
    w("规则：若文本命中某情绪的**普通关键词**，且同时命中该情绪的**反讽标记**，")
    w("则强度系数 ×1.4（上限 95），并在输出中标注「⚠️ 检测到反讽表达」。")
    w("")
    has_sarcasm = [(p.mood_id, p.label, p.sarcasm_markers) for p in MOOD_LIBRARY.values() if p.sarcasm_markers]
    if has_sarcasm:
        w("| mood_id | 反讽标记 |")
        w("|---|---|")
        for mid, label, marks in has_sarcasm:
            w(f"| `{mid}`（{label}） | {'、'.join(marks)} |")
        w("")
    else:  # pragma: no cover
        w("（当前无情绪配置反讽标记）")
        w("")
    w("**扩展建议**：把「辛苦了」「加油哦」「改个小需求」这类职场黑话加进 `pissed_off` 的反讽标记，")
    w("识别准确率会明显提升。")
    w("")
    w("---")
    w("")
    w("## 5. 时间上下文增强")
    w("")
    w("取 `now-time-info` 后叠加判定（同一句话在不同时间可能是不同情绪）：")
    w("")
    w("| 条件 | 触发 | 说明 |")
    w("|---|---|---|")
    w("| 每周一 | `monday_blues` | 周一综合征，咖啡因拉满 |")
    w("| 每周五 | `friday_joy` | 周五快乐，放开吃 |")
    w("| 周日 18:00 后 | `sunday_dread` | 周日晚上恐惧，糖分安抚 |")
    w("| 每月 10 日前后 | `payday` | 发薪日，允许贵价组合 |")
    w("| 月末 3 天 | `broke` | 月末吃土，性价比优先 |")
    w("| 深夜 22:00 后 | 过滤早餐类 | 早餐只在早餐时段出现 |")
    w("")
    w("---")
    w("")
    w("## 6. 强度系数与失血值上限")
    w("")
    w("```")
    w("最终失血值 = min(95, 基础失血值 × 强度系数)")
    w("强度系数   = 1.0 基线")
    w("            + 0.2 × 感叹号数量（最多 +0.4）")
    w("            + 0.2 × 极端词命中（想死 / 爆炸 / 崩溃 / 受不了）")
    w("            + 0.4 × 反讽命中")
    w("```")
    w("")
    w("> ⚠️ **上限刻意设为 95 而不是 100。**")
    w(">")
    w("> 若失血值可到 100，则没有任何方案的 RP 能满足「RP ≥ BS」，")
    w("> 所有结果都会显示「不够用」，用户会觉得这系统只会泼冷水。")
    w("> 留 5 分余量 = 永远给人留一点希望 —— 这是产品判断，不是数学问题。")
    w("")
    return "\n".join(lines)


def _flavor_reading(p) -> str:
    f = p.flavor
    bits: list[str] = []
    if f.caffeine >= 0.8:
        bits.append("必须提神")
    elif f.caffeine >= 0.4:
        bits.append("稍需提神")
    if f.chewy >= 0.8:
        bits.append("要咬东西泄愤")
    if f.sweet >= 0.8:
        bits.append("需要糖分安抚")
    if f.salty >= 0.8:
        bits.append("咸香扎实")
    if f.salty <= 0.4 and f.sweet <= 0.4:
        bits.append("清淡为主")
    return "、".join(bits) or "均衡，随便都行"


def gen_copywriting_bank() -> str:
    lines: list[str] = []
    w = lines.append

    w("# 发疯文学语料库")
    w("")
    w("> 🤖 **本文件由 `skill/scripts/gen_references.py` 自动生成**，数据源 `copywriting.TEMPLATES`。")
    w("> 改语料请改代码后重新生成，**不要直接编辑本文件**。")
    w("")
    w("---")
    w("")
    w("## 1. 三种文体")
    w("")
    w("| `--style` | 文体 | 适用情绪 | 语气 |")
    w("|---|---|---|---|")
    w("| `crazy` | 发疯文学 | 被气死、加班崩溃、emo | 失控、反讽、自毁式幽默 |")
    w("| `report` | 打工人续命日报 | 周五、发工资、摸鱼 | 冷静克制、公文腔 |")
    w("| `flex` | 反向凡尔赛 | 发工资、月末吃土 | 表面炫耀实则苦涩 |")
    w("")
    w("## 2. 可用占位符")
    w("")
    w("| 占位符 | 含义 | 占位符 | 含义 |")
    w("|---|---|---|---|")
    w("| `{item}` | 主商品名 | `{order}` | 订单号 |")
    w("| `{pay}` | 实付金额 | `{save}` | 省下的钱 |")
    w("| `{time}` | 时间 | `{mood}` | 情绪名 |")
    w("| `{bs}` | 失血值 | `{rp}` | 续命值 |")
    w("")
    w("> 占位符未提供的字段会被替换成空串，**不会把 `{xxx}` 暴露给用户**。")
    w("")
    w("---")
    w("")
    w("## 3. 语料明细")
    w("")
    total = 0
    for mood_id, by_style in TEMPLATES.items():
        label = MOOD_LIBRARY[mood_id].label if mood_id in MOOD_LIBRARY else mood_id
        w(f"### `{mood_id}` · {label}")
        w("")
        for style in (STYLE_CRAZY, STYLE_REPORT, STYLE_FLEXY):
            items = by_style.get(style, [])
            if not items:
                continue
            total += len(items)
            w(f"**{STYLE_LABEL[style]}**（`--style {style}`）")
            w("")
            for t in items:
                w(f"> {t}")
                w(">")
            w("")
    w("---")
    w("")
    w(f"## 4. 规模统计")
    w("")
    w(f"- 覆盖情绪：**{len(TEMPLATES)} / {len(MOOD_LIBRARY)}** 种")
    w(f"- 语料总数：**{total}** 条")
    w("")
    missing = [m for m in MOOD_LIBRARY if m not in TEMPLATES]
    if missing:
        w("⚠️ **以下情绪尚无专属语料**，会回落到默认模板：")
        w("")
        for m in missing:
            w(f"- `{m}`（{MOOD_LIBRARY[m].label}）")
        w("")
    w("### 扩写建议")
    w("")
    w("语料是这个 Skill 最容易出圈的部分，扩写时守住三条：")
    w("")
    w("1. **不卖惨到失真** —— 自嘲可以，但不美化过劳。")
    w("2. **不攻击具体他人** —— 吐槽「甲方」这类泛化对象，不指名道姓、不带脏字。")
    w("3. **不把消费当解药** —— 避免「吃了就好了」式表达，快餐只是一场小小的喘息。")
    w("")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    targets = {
        "mood-matrix.md": gen_mood_matrix,
        "copywriting-bank.md": gen_copywriting_bank,
    }
    for name, fn in targets.items():
        path = OUT_DIR / name
        path.write_text(fn(), encoding="utf-8")
        print(f"✅ 已生成 {path.relative_to(ROOT)}")

    print()
    print("以下两份含大量无法从代码推断的信息，保持手写：")
    print("  - references/mcp-tools.md")
    print("  - references/safety-rules.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
