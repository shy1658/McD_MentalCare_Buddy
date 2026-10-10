"""
发疯文学生成器 —— 把「情绪 + 订单」变成一段值得发朋友圈的话。

⚠️ 官方 MCP **没有**生成文案/海报的接口，这是纯本地能力。
好处是零接口依赖、可离线、天然适合社交传播 —— 是本项目的重要加分项。

设计：
    每种情绪对应一组模板（发疯 / 日报 / 凡尔赛 三种文体），
    用情绪 + 商品名 + 订单号 + 省钱金额做填空。
    模板由人工撰写，保证语气稳定；LLM 只做可选润色。
"""

from __future__ import annotations

import random

from .models import MoodMatch, Recommendation

# 三种文体
STYLE_CRAZY = "crazy"      # 发疯文学
STYLE_REPORT = "report"    # 打工人续命日报
STYLE_FLEXY = "flex"       # 反向凡尔赛


# 模板里可用的占位符：
#   {item} 主商品名   {order} 订单号   {pay} 实付   {save} 省钱
#   {time} 时间       {mood} 情绪名    {bs} 失血值  {rp} 续命值
TEMPLATES: dict[str, dict[str, list[str]]] = {
    "overtime_crash": {
        STYLE_CRAZY: [
            "{time}，我和我的第 40 个 bug，还有一个{item}。我活着唯一的证据是订单号 {order}。",
            "改完这版我就下班。（第 6 次说这句话）现在的证据：{item}，{pay}。",
        ],
        STYLE_REPORT: [
            "【今日续命报告】失血 {bs} / 续命 {rp} / 实付 {pay} / 省 {save}。今日评级：勉强活着。",
            "加班第 N 天。补给：{item}。实付 {pay}，省 {save}。这就是我坚持的理由。",
        ],
        STYLE_FLEXY: [
            "凌晨还在吃{item}，我不是加班，我是在为梦想充值。{pay}，很便宜的梦想。",
        ],
    },
    "pissed_off": {
        STYLE_CRAZY: [
            "甲方要五彩斑斓的黑。我给了。黑白的是我的简历，彩色的是我骂人的词汇量。"
            "吃完这个{item}，明天继续改第 11 版。订单号 {order}，这是我今天唯一自己做的决定。",
            "我不生气。我只是买了个{item}，然后把它咬得很碎。{pay}。",
        ],
        STYLE_REPORT: [
            "【情绪事故报告】失血 {bs}，已通过{item}止血，续命值 {rp}。建议：明日减少与甲方接触。",
            "今日被气死 {bs} 点。处方：{item}，{pay}。疗效：还行。",
        ],
        STYLE_FLEXY: [
            "被气到花 {pay} 买{item}。这不是消费，这是医疗费。",
        ],
    },
    "slacking": {
        STYLE_CRAZY: [
            "下午三点，无事发生。我吃了{item}，证明我还在工位上。",
        ],
        STYLE_REPORT: [
            "【摸鱼补给】失血 {bs} / 续命 {rp}。低负担，吃完继续摸，不会犯困。实付 {pay}。",
        ],
        STYLE_FLEXY: [
            "同样是坐着，我坐着吃{item}还花了 {pay}，这就是我和同事的区别。",
        ],
    },
    "payday": {
        STYLE_CRAZY: [
            "工资到账 0.3 秒后，我拥有了{item}。这是本月最快的一笔消费，也是最值的一笔。",
        ],
        STYLE_REPORT: [
            "【发薪日犒赏】失血 {bs} / 续命 {rp} / 实付 {pay}。今日评级：值得。",
        ],
        STYLE_FLEXY: [
            "月薪 3 千，吃了 {pay} 的{item}。我配吗？我配的。这是我这个月唯一一件自己说了算的事。",
        ],
    },
    "emo": {
        STYLE_CRAZY: [
            "医生说要多糖。我买了个{item}，{pay}。便宜，有效，不会问我为什么难过。",
        ],
        STYLE_REPORT: [
            "【今日疗愈】失血 {bs} / 续命 {rp}。糖分补给完成。实付 {pay}。",
        ],
        STYLE_FLEXY: [
            "用 {pay} 换一个{item}，换半小时不难过。划算。",
        ],
    },
    "broke": {
        STYLE_CRAZY: [
            "余额 47 块，我花 {pay} 买了{item}。穷，但穷得很有性价比。",
        ],
        STYLE_REPORT: [
            "【月末生存报告】失血 {bs} / 续命 {rp} / 实付 {pay} / 省 {save}。省钱即赚钱。",
        ],
        STYLE_FLEXY: [
            "省了 {save}。这 {save} 够我明天再活一天。",
        ],
    },
    "diet_broken": {
        STYLE_CRAZY: [
            "减肥第 3 天，第 3 天结束。证据：{item}，{pay}。明天重新第一天。",
        ],
        STYLE_REPORT: [
            "【破防记录】失血 {bs}，续命 {rp}。热量超标，心情达标。实付 {pay}。",
        ],
        STYLE_FLEXY: [
            "破都破了，破得漂亮点。{item}，{pay}。",
        ],
    },
}

DEFAULT_TEMPLATES: dict[str, list[str]] = {
    STYLE_CRAZY: ["我买了{item}。{pay}。这大概是我今天最果断的一次决策。"],
    STYLE_REPORT: ["【今日续命报告】失血 {bs} / 续命 {rp} / 实付 {pay} / 省 {save}。"],
    STYLE_FLEXY: ["{pay} 的{item}，我配得上。"],
}


def generate(
    rec: Recommendation,
    style: str = STYLE_CRAZY,
    order_id: str = "",
    now: str = "",
    seed: int | None = None,
) -> str:
    """
    生成文案。

    Args:
        rec:      推荐方案（含情绪、商品、价格）
        style:    crazy / report / flex
        order_id: 订单号，未下单时留空
        now:      时间字符串，如 "23:47"
        seed:     随机种子，传固定值可复现（测试用）

    Returns:
        一段可直接复制发朋友圈的文字
    """
    rng = random.Random(seed)
    mood: MoodMatch = rec.mood

    bank = TEMPLATES.get(mood.mood_id, {}).get(style) or DEFAULT_TEMPLATES.get(style, [""])
    tpl = rng.choice(bank) if bank else ""

    main_item = rec.lines[0].item.name if rec.lines else "麦当劳"
    ctx = {
        "item": main_item,
        "order": order_id or "——",
        "pay": f"¥{rec.deal.payable:.2f}",
        "save": f"¥{rec.deal.saved:.2f}",
        "time": now or "此刻",
        "mood": mood.label,
        "bs": str(mood.bleed_score),
        "rp": f"{rec.rp:.0f}",
    }
    try:
        return tpl.format(**ctx)
    except (KeyError, IndexError):
        return tpl


def generate_report_card(rec: Recommendation, order_id: str = "", now: str = "") -> str:
    """
    「打工人续命日报」卡片文案 —— 结构化、适合配图发朋友圈。
    """
    mood = rec.mood
    grade = (
        "勉强活着" if rec.rp >= mood.bleed_score
        else "需要二次续命" if rec.rp >= mood.bleed_score * 0.8
        else "建议直接下班"
    )
    return (
        f"【今日续命报告】\n"
        f"状态：{mood.label}\n"
        f"失血值：{mood.bleed_score}\n"
        f"续命值：{rec.rp:.0f}\n"
        f"补给：{' + '.join(ln.item.name for ln in rec.lines)}\n"
        f"实付：¥{rec.deal.payable:.2f}（省 ¥{rec.deal.saved:.2f}）\n"
        f"评级：{grade}"
    )
