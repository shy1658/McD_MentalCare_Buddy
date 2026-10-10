"""
Markdown 渲染器 —— 把 Recommendation 变成人爱看的一屏。

输出原则：
1. **先给情绪，再给方案** —— 用户要的是「被理解」，不是「被推荐」
2. **每个数字都有出处** —— RP 分项、券减免、原价/实付，全部可追溯
3. **凑单要单独高亮** —— 「多买反而更便宜」是本项目最爽的一屏
4. **价格以 MCP 为准** —— 底部固定免责声明
"""

from __future__ import annotations

from .models import CartLine, Coupon, Recommendation


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _bar(value: float, total: float = 100.0, width: int = 20) -> str:
    """进度条：▓▓▓▓▓░░░░░"""
    if total <= 0:
        return "░" * width
    filled = int(round(min(1.0, value / total) * width))
    return "▓" * filled + "░" * (width - filled)


def _money(v: float) -> str:
    return f"¥{v:.2f}"


def _items_str(lines: list[CartLine]) -> str:
    return " + ".join(f"{ln.item.name}×{ln.qty}" if ln.qty > 1 else ln.item.name for ln in lines)


def _coupon_names(coupons: list[Coupon]) -> str:
    if not coupons:
        return "未使用优惠券"
    return "、".join(c.name for c in coupons)


# ---------------------------------------------------------------------------
# 分块渲染
# ---------------------------------------------------------------------------


def render_mood_card(rec: Recommendation) -> list[str]:
    """情绪诊断卡 —— 输出的第一屏"""
    m = rec.mood
    lines = [
        "## 🩺 情绪诊断",
        "",
        f"> **{m.label}**　失血值 **{m.bleed_score}**　`{_bar(m.bleed_score)}`",
        "",
        f"- 识别置信度：`{m.confidence:.2f}`　强度系数：`{m.intensity}`",
        f"- 时间上下文：`{m.time_context}`",
    ]
    if m.sarcasm:
        lines.append("- ⚠️ 检测到**反讽**表达 —— 你说的不是字面意思，我按真实情绪处理了")
    if m.hit_keywords:
        lines.append(f"- 命中关键词：{'、'.join(m.hit_keywords[:6])}")
    if m.confidence < 0.6:
        lines.append(
            "- 💡 置信度偏低，建议用 `mood_engine.build_mood_prompt()` 生成的提示词交给 LLM 复核"
        )
    lines.append("")
    return lines


def render_plan(rec: Recommendation, idx: int, client_mode: str = "mock") -> list[str]:
    """单个方案卡片"""
    d = rec.deal
    mark = "✅" if rec.enough else "⚠️"
    if rec.over_budget:
        mark = "💸"

    # live 模式下若官方实时计价返回 0（如沙箱未开通计价 / 组合餐需逐项计价），
    # 不应展示误导性的 ¥0.00，改为提示以官方结算页为准
    live_no_price = client_mode == "live" and d.subtotal == 0 and d.payable == 0

    out = [
        f"### {idx}. 【{rec.plan_name}】{mark}",
        "",
        f"**{_items_str(rec.lines)}**",
        "",
        f"| 项 | 值 |",
        f"|---|---|",
    ]
    if live_no_price:
        out.append("| 价格 | 以麦当劳官方结算页为准（实时计价接口本次返回 0）|")
    else:
        out.append(f"| 原价 | ~~{_money(d.subtotal)}~~ |")
        out.append(f"| 优惠 | −{_money(d.discount)}（{_coupon_names(d.applied)}）|")
        if d.delivery_fee:
            out.append(f"| 配送费 | {_money(d.delivery_fee)} |")
        out.append(f"| **实付** | **{_money(d.payable)}**（省 {_money(d.saved)}，折扣率 {d.discount_rate:.0%}）|")
    out.append("")

    # 续命值
    verdict = "够用，能撑住" if rec.enough else f"低于失血值 {rec.mood.bleed_score}，可能不太够"
    out.append(f"**续命值 RP {rec.rp:.0f}**　`{_bar(rec.rp)}`　—— {verdict}")
    if rec.rp_parts:
        # 各分量满分：口味 40，其余 20（含凑单也不影响的「暴击」）
        caps = {"口味": 40, "热量": 20, "功能": 20, "取向": 20, "暴击": 20}
        parts = " ｜ ".join(f"{k} {v:.0f}/{caps.get(k, 20)}" for k, v in rec.rp_parts.items())
        out.append("")
        out.append(f"<sub>{parts}</sub>")
    out.append("")

    # 凑单高亮
    if d.filler is not None:
        out.append(
            f"> 🎯 **凑单提示**：加购「{d.filler.name}」（{_money(d.filler.price)}）后触发满减，"
            f"实付反而**少 {_money(d.saving_vs_no_filler)}** —— 多拿一个还更便宜。"
        )
        out.append("")

    # 用券明细
    if d.explain:
        out.append("<details><summary>优惠明细</summary>")
        out.append("")
        for e in d.explain:
            out.append(f"- {e}")
        out.append("")
        out.append("</details>")
        out.append("")

    if rec.reason:
        out.append(f"💬 {rec.reason}")
        out.append("")
    return out


def render_copy(rec: Recommendation) -> list[str]:
    """文案区"""
    if not rec.copywriting:
        return []
    return [
        "## 🎴 给你写了段文案",
        "",
        "```text",
        rec.copywriting,
        "```",
        "",
    ]


def render_order(rec: Recommendation, order=None) -> list[str]:
    """下单结果"""
    if order is None:
        return [
            "## 🚦 下一步",
            "",
            "- 当前为 `--dry-run` 模式，**未真正下单**",
            "- 去掉 `--dry-run` 后会展示确认卡片，你点头才会调用 `create-order`",
            "",
        ]
    return [
        "## ✅ 下单成功",
        "",
        f"- 订单号：`{order.order_id}`",
        f"- 取餐号：`{order.pickup_code}`",
        f"- 支付链接：{order.pay_url}",
        "",
    ]


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------


def render(
    recs: list[Recommendation],
    client_mode: str = "mock",
    order=None,
    store_name: str = "",
) -> str:
    """
    渲染完整 Markdown 报告。

    Args:
        recs:        推荐方案列表（应按综合分排序，取前 3 展示）
        client_mode: "mock" | "live"，决定是否打「离线演示」标记
        order:       下单结果（未下单传 None）
        store_name:  门店名
    """
    if not recs:
        return "## 😶 没找到合适的方案\n\n换个说法再试试？比如「加班快死了」「发工资了」。"

    head = recs[0]
    blocks: list[str] = []

    blocks.append("# 🍔 麦麦精神续命搭子")
    blocks.append("")
    if client_mode == "mock":
        blocks.append(
            "> ⚠️ **当前为离线演示模式（Mock 数据）**，菜单与价格均为样例，非真实麦当劳数据。"
        )
        blocks.append("")
    if store_name:
        blocks.append(f"📍 门店：{store_name}")
        blocks.append("")

    blocks += render_mood_card(head)

    blocks.append("## 🍟 为你匹配到这些方案")
    blocks.append("")
    for i, r in enumerate(recs[:3], start=1):
        blocks += render_plan(r, i, client_mode=client_mode)

    blocks += render_copy(head)
    blocks += render_order(head, order)

    blocks.append("---")
    blocks.append("")
    blocks.append(
        "<sub>价格、库存与优惠可用性以麦当劳官方 MCP `calculate-price` 实时返回为准。"
        "本项目不构成医疗或心理建议。</sub>"
    )
    return "\n".join(blocks)
