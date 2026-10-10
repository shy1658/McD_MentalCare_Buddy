"""
心理安全层 —— 危机信号检测与干预
=================================

**这个模块的存在理由只有一个：不伤害。**

一个"情绪化点餐"产品最危险的地方，恰恰在于它会主动拥抱用户的负面情绪。
如果用户说「我不想活了」，而系统回一句「来个麦辣鸡腿堡，券后只要 23 块」——
这不是贴心，这是把求救信号当成了消费场景。

所以本模块做两件事：

1. **在情绪识别之前短路** —— 检测到危机信号时，不调任何 MCP 接口、不做任何推荐，
   直接返回援助信息。
2. **分档而非一刀切** —— 见 `CrisisLevel`，避免"狼来了效应"。

设计中最难的一点：区分吐槽与求救
--------------------------------
`overtime_crash` 的关键词里**本来就包含「想死」「不想活」**——
打工人说「改 bug 改到想死」是夸张修辞，不是求救。
如果每次都弹热线，真正的危机信号会被稀释（用户学会了"别这么说话，会被打断"）。

解法是**语境降档**：

    命中危机词 + 有具体吐槽对象（bug / 甲方 / 加班 / 论文…）  →  CONCERN（不阻断）
    命中危机词 + 无具体对象                                  →  CRISIS（阻断）
    命中告别类表达（「以后不用管我了」）                      →  无论语境一律 CRISIS

**分不清时按更严重的一档处理** —— 这条规则不会变。

引用
----
行为规则与话术的权威版本见 `skill/references/safety-rules.md`，
本模块是那份文档在代码层的落地。两者必须保持一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# 援助渠道
# ---------------------------------------------------------------------------

HOTLINES: tuple[tuple[str, str], ...] = (
    ("全国 24 小时心理援助热线", "12356"),
    ("北京心理危机研究与干预中心", "010-82951332"),
    ("希望 24 热线", "400-161-9995"),
)

# ---------------------------------------------------------------------------
# 信号词典
# ---------------------------------------------------------------------------

# ① 自伤 / 自杀倾向 —— 注意「想死」「不想活」同时也在加班吐槽里高频出现，
#    因此这类词命中后必须再过一遍语境判定（见 _has_vent_context）。
SELF_HARM: tuple[str, ...] = (
    "不想活",
    "不想活了",
    "活不下去",
    "活不下去了",
    "想死",
    "死了算了",
    "去死",
    "自杀",
    "结束生命",
    "了结自己",
    "想消失",
    "不如死了",
    "活着干什么",
    "不想存在",
)

# ② 持续性绝望 —— 指向人生/自身，而非某件具体的事
HOPELESS: tuple[str, ...] = (
    "活着没意思",
    "活着没意义",
    "活着的没意义",
    "人生没意义",
    "没有意义活下去",
    "不会好了",
    "永远都这样",
    "永远不会好",
    "没救了我",
    "我这个人没救",
    "看不到任何希望",
    "一点希望都没有",
    "撑不下去了",
    "熬不下去了",
)

# ③ 告别式表达 —— ⚠️ 最高危，无论处于什么语境都不降档
FAREWELL: tuple[str, ...] = (
    "最后一次",
    "以后不用管我",
    "以后就别管我",
    "帮我照顾",
    "替我照顾",
    "再见了",
    "告别了",
    "遗书",
    "来世",
    "下辈子",
    "我的东西都",
    "账号留给",
    "密码留给",
)

# 吐槽语境标记 —— 命中说明用户很可能在夸张修辞，而不是求救
VENT_CONTEXT: tuple[str, ...] = (
    "bug",
    "加班",
    "甲方",
    "需求",
    "上线",
    "代码",
    "改",
    "工作",
    "deadline",
    "ddl",
    "老板",
    "客户",
    "会议",
    "开会",
    "周报",
    "汇报",
    "考试",
    "论文",
    "答辩",
    "导师",
    "作业",
    "通勤",
    "地铁",
    "熬夜",
    "通宵",
    "需求文档",
    "评审",
    "排期",
    "联调",
    "返工",
)

# 缓解/否定标记 —— 「我不想死，我只是累」这类
DENIAL_HINTS: tuple[str, ...] = (
    "不想死",
    "不是想死",
    "只是",
    "开玩笑",
    "夸张",
    "比喻",
)


class CrisisLevel(str, Enum):
    """危机等级 —— 决定系统怎么回应"""

    NONE = "none"
    """正常，走点餐流程"""

    CONCERN = "concern"
    """
    存疑：有危机词，但处于明确的吐槽语境（「改 bug 改到想死」）。
    **不阻断点餐**，但会在顶部附一句温和关怀 + 热线，
    并强制改用克制文体（不发疯文学，避免强化自毁情绪）。
    """

    CRISIS = "crisis"
    """
    确定：**短路，不做任何推荐**。只返回干预信息。
    这是唯一会跳过全部 MCP 调用的分支。
    """


@dataclass
class CrisisResult:
    """危机检测结果"""

    level: CrisisLevel
    hits: list[str] = field(default_factory=list)
    """命中的危机词"""

    categories: list[str] = field(default_factory=list)
    """命中的信号类别：self_harm / hopeless / farewell"""

    vent_context: bool = False
    """是否处于吐槽语境（决定要不要降档）"""

    reason: str = ""
    """判定理由，便于调试与审计"""

    @property
    def triggered(self) -> bool:
        return self.level is not CrisisLevel.NONE

    @property
    def blocks_ordering(self) -> bool:
        """是否阻断点餐流程"""
        return self.level is CrisisLevel.CRISIS


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------


def _has_vent_context(text: str) -> bool:
    """文本里是否存在具体吐槽对象（bug / 甲方 / 论文 …）"""
    return any(v in text for v in VENT_CONTEXT)


def detect_crisis(text: str) -> CrisisResult:
    """
    危机信号检测。**必须在 `detect_mood()` 之前调用。**

    判定顺序（ seriously，顺序不能改）：

      1. 告别类  → 一律 CRISIS，不看语境
      2. 绝望类  → 一律 CRISIS，不看语境
      3. 自伤类  → 有吐槽语境则 CONCERN，否则 CRISIS
      4. 都没中  → NONE

    Args:
        text: 用户原始输入

    Returns:
        CrisisResult —— level 决定调用方怎么处理

    Examples:
        >>> detect_crisis("改 bug 改到想死").level
        <CrisisLevel.CONCERN: 'concern'>
        >>> detect_crisis("我不想活了").level
        <CrisisLevel.CRISIS: 'crisis'>
        >>> detect_crisis("累成狗").level
        <CrisisLevel.NONE: 'none'>
    """
    raw = (text or "").strip()
    if not raw:
        return CrisisResult(level=CrisisLevel.NONE, reason="空输入")

    lower = raw.lower()
    hits: list[str] = []
    cats: list[str] = []

    def _scan(words: tuple[str, ...], category: str) -> bool:
        found = [w for w in words if w in lower]
        if found:
            hits.extend(found)
            cats.append(category)
            return True
        return False

    has_farewell = _scan(FAREWELL, "farewell")
    has_hopeless = _scan(HOPELESS, "hopeless")
    has_self_harm = _scan(SELF_HARM, "self_harm")

    if not (has_farewell or has_hopeless or has_self_harm):
        return CrisisResult(level=CrisisLevel.NONE, hits=[], categories=[])

    vent = _has_vent_context(lower)

    # ① 告别类 —— 最高危，任何语境都不降档
    if has_farewell:
        return CrisisResult(
            level=CrisisLevel.CRISIS,
            hits=hits,
            categories=cats,
            vent_context=vent,
            reason="命中告别式表达，属最高危信号，不做语境降档",
        )

    # ② 绝望类 —— 不降档
    if has_hopeless:
        return CrisisResult(
            level=CrisisLevel.CRISIS,
            hits=hits,
            categories=cats,
            vent_context=vent,
            reason="命中持续性绝望表达",
        )

    # ③ 自伤类 —— 看语境
    if has_self_harm:
        if vent:
            return CrisisResult(
                level=CrisisLevel.CONCERN,
                hits=hits,
                categories=cats,
                vent_context=True,
                reason="命中自伤词但处于明确吐槽语境，判定为夸张修辞；"
                "仍附关怀与热线，并改用克制文体",
            )
        return CrisisResult(
            level=CrisisLevel.CRISIS,
            hits=hits,
            categories=cats,
            vent_context=False,
            reason="命中自伤词且无具体吐槽对象",
        )

    return CrisisResult(level=CrisisLevel.NONE, hits=hits, categories=cats)


# ---------------------------------------------------------------------------
# 文体适配
# ---------------------------------------------------------------------------


def recommended_style(level: CrisisLevel, requested: str) -> str:
    """
    按危机等级调整文案文体。

    CONCERN 档强制降级为克制文体（`report`）——
    此时再给用户看「我活着唯一的证据是订单号」这种自毁式幽默，
    等于拿他的痛苦开玩笑。
    """
    if level is CrisisLevel.CONCERN:
        return "report"
    return requested


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def render_hotlines() -> list[str]:
    """热线列表的 Markdown 行"""
    return [f"🆘 **{name}：`{tel}`**" for name, tel in HOTLINES]


def render_crisis_card(result: CrisisResult, original_text: str = "") -> str:
    """
    CRISIS 档专用输出 —— **不包含任何点餐内容**。

    这一段的结构是固定的，顺序不能改：
        接住 → 表明认真对待 → 给热线 → 留一个回来的口子
    """
    out: list[str] = []
    w = out.append

    w("# 🍔 麦麦精神续命搭子")
    w("")
    w("## 🫂 先停一下")
    w("")
    w("我听到你说的话了，而且我认真对待。")
    w("")
    w("我只是一个点餐搭子，帮不了你这件事——但有人能，而且他们 24 小时都在。")
    w("")
    w("### 请现在联系他们")
    w("")
    for line in render_hotlines():
        w(line)
        w("")
    w("如果身边有信任的人，也请跟他们说一句。你不需要一个人扛。")
    w("")
    w("---")
    w("")
    w("**我不是专业的心理工作者**，刚才的话不是诊断、也不是建议，")
    w("只是一个希望你安全的提醒。")
    w("")
    w("如果你愿意，我们回头再挑个吃的——但那件事不重要，所以我把这段话放在了前面。")
    w("")

    if original_text:
        w("<details><summary>技术信息（调试用）</summary>")
        w("")
        w(f"- 判定等级：`{result.level.value}`")
        w(f"- 命中信号：{result.hits or '—'}")
        w(f"- 信号类别：{result.categories or '—'}")
        w(f"- 判定理由：{result.reason}")
        w("")
        w("> 本次未调用任何麦当劳 MCP 接口，未生成任何点餐推荐。")
        w("")
        w("</details>")

    return "\n".join(out)


def render_concern_banner(result: CrisisResult) -> str:
    """
    CONCERN 档的温和提示块 —— 放在正常推荐**之前**，不阻断。

    刻意写得轻：不说教、不盘问、不把气氛搞沉重。
    """
    out: list[str] = []
    out.append("> 💛 你刚才说的话里有些词我不太放心。")
    out.append("> 如果是认真的，请一定和能帮到你的人聊聊：")
    out.append(">")
    for name, tel in HOTLINES[:1]:
        out.append(f"> 🆘 **{name}：`{tel}`**（24 小时）")
    out.append(">")
    out.append("> 如果只是累到随口说说——那下面这顿，我给你算仔细点。")
    out.append("")
    return "\n".join(out)


def to_dict(result: CrisisResult) -> dict:
    """供 `--json` 输出"""
    return {
        "level": result.level.value,
        "triggered": result.triggered,
        "blocks_ordering": result.blocks_ordering,
        "hits": result.hits,
        "categories": result.categories,
        "vent_context": result.vent_context,
        "reason": result.reason,
        "hotlines": [{"name": n, "tel": t} for n, t in HOTLINES],
    }


__all__ = [
    "HOTLINES",
    "CrisisLevel",
    "CrisisResult",
    "detect_crisis",
    "recommended_style",
    "render_crisis_card",
    "render_concern_banner",
    "render_hotlines",
    "to_dict",
]
