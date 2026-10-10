"""
情绪匹配引擎 —— 把「人话」翻译成「套餐属性」。

两件事：
1. detect_mood()   : 用户输入 → 情绪画像（MoodMatch）
2. rank_items()    : 情绪画像 → 商品打分（RP 续命值）

为什么不用纯 LLM 做情绪识别？
-----------------------------
- 可控：规则命中是可复现的，评审问「为什么判定为被气死」能立刻给出命中词
- 便宜：不消耗 token，离线也能跑
- 可兜底：LLM 只作为规则置信度不足时的增强（见 build_mood_prompt）

推荐做法：**规则优先 + LLM 兜底**。
规则命中且置信度 >= 0.6 直接用；否则调用 build_mood_prompt() 生成的提示词让 LLM 判断。
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import (
    FlavorVector,
    KcalPref,
    MenuItem,
    MoodMatch,
    MoodProfile,
    ScoredItem,
    SpeedPref,
    ValuePref,
)

# =============================================================================
# 一、情绪库 —— 12 种打工人精神状态
# =============================================================================
# 每个情绪 = 一个四维口味向量 + 热量/性价比/出餐速度偏好 + 命中关键词
#
# 调参经验：
#   - bleed_score（失血值）决定「要多强的续命值才够」，别乱调，它跟文案语气绑定
#   - func_need 是 RP 公式里 func_fit 分量的开关，取值 caffeine / chew / sugar / none

MOOD_LIBRARY: dict[str, MoodProfile] = {
    p.mood_id: p
    for p in [
        MoodProfile(
            mood_id="overtime_crash",
            label="加班到崩溃",
            bleed_score=85,
            flavor=FlavorVector(salty=0.9, chewy=0.5, sweet=0.2, caffeine=0.9),
            kcal_pref=KcalPref.HIGH,
            value_pref=ValuePref.DEAL,
            speed_pref=SpeedPref.FAST,
            func_need="caffeine",
            keywords=(
                "加班", "bug", "上线", "通宵", "熬夜", "改需求", "想死", "不想活",
                "累成狗", "累死", "撑不住", "还有", "没改完", "deadline", "赶工",
            ),
            copy_tone="硬撑",
        ),
        MoodProfile(
            mood_id="pissed_off",
            label="被气死",
            bleed_score=90,
            flavor=FlavorVector(salty=0.8, chewy=1.0, sweet=0.3, caffeine=0.0),
            kcal_pref=KcalPref.MID,
            value_pref=ValuePref.DEAL,
            func_need="chew",  # 需要能咬的东西
            keywords=(
                "气死", "生气", "甲方", "背锅", "甩锅", "无语", "离谱", "骂",
                "受不了", "忍不了", "爆炸", "崩溃", "针对我", "凭什么",
            ),
            sarcasm_markers=("我笑了", "真好", "棒极了", "开心", "呵呵", "宠爱", "感谢"),
            copy_tone="发疯",
        ),
        MoodProfile(
            mood_id="slacking",
            label="摸鱼",
            bleed_score=25,
            flavor=FlavorVector(salty=0.4, chewy=0.2, sweet=0.3, caffeine=0.1),
            kcal_pref=KcalPref.LOW,
            value_pref=ValuePref.DEAL,
            func_need="none",
            keywords=(
                "摸鱼", "划水", "无聊", "无事发生", "没事干", "闲", "下午茶",
                "三点", "发呆", "偷懒", "摆烂", "躺平",
            ),
            copy_tone="松弛",
        ),
        MoodProfile(
            mood_id="payday",
            label="发工资",
            bleed_score=15,
            flavor=FlavorVector(salty=0.9, chewy=0.7, sweet=0.6, caffeine=0.2),
            kcal_pref=KcalPref.HIGH,
            value_pref=ValuePref.PREMIUM,  # 犒赏优先，不再抠门
            func_need="none",
            keywords=(
                "发工资", "发薪", "工资到账", "到账", "有钱了", "奖金", "年终",
                "发了", "加薪", "报销",
            ),
            copy_tone="凡尔赛",
        ),
        MoodProfile(
            mood_id="monday_blues",
            label="周一综合征",
            bleed_score=70,
            flavor=FlavorVector(salty=0.5, chewy=0.3, sweet=0.7, caffeine=1.0),
            kcal_pref=KcalPref.MID,
            func_need="caffeine",
            keywords=("周一", "星期一", "又要上班", "不想上班", "早起", "周一早"),
            copy_tone="唤醒",
        ),
        MoodProfile(
            mood_id="friday_joy",
            label="周五快乐",
            bleed_score=20,
            flavor=FlavorVector(salty=0.8, chewy=0.8, sweet=0.5, caffeine=0.0),
            kcal_pref=KcalPref.HIGH,
            func_need="none",
            keywords=("周五", "星期五", "周末", "放假", "下班了", "收工", "解放"),
            copy_tone="快乐",
        ),
        MoodProfile(
            mood_id="emo",
            label="emo / 失恋",
            bleed_score=75,
            flavor=FlavorVector(salty=0.3, chewy=0.3, sweet=1.0, caffeine=0.0),
            kcal_pref=KcalPref.MID,
            func_need="sugar",  # 糖分疗愈
            keywords=("emo", "失恋", "难过", "伤心", "分手", "想哭", "低落", "抑郁", "孤独"),
            copy_tone="疗愈",
        ),
        MoodProfile(
            mood_id="rushed",
            label="赶时间焦虑",
            bleed_score=60,
            flavor=FlavorVector(salty=0.7, chewy=0.4, sweet=0.2, caffeine=0.5),
            speed_pref=SpeedPref.FAST,
            func_need="caffeine",
            keywords=("赶时间", "来不及", "快点", "急", "马上", "开会", "迟到", "快"),
            copy_tone="高效",
        ),
        MoodProfile(
            mood_id="diet_broken",
            label="减肥第 3 天破防",
            bleed_score=65,
            flavor=FlavorVector(salty=0.9, chewy=0.9, sweet=0.8, caffeine=0.0),
            kcal_pref=KcalPref.HIGH,  # 破都破了，破得漂亮点
            func_need="chew",
            keywords=("减肥", "破防", "戒糖", "轻断食", "忍不住", "偷吃", "放纵", "管不住嘴"),
            copy_tone="自嘲",
        ),
        MoodProfile(
            mood_id="numb",
            label="被卷到麻木",
            bleed_score=80,
            flavor=FlavorVector(salty=0.6, chewy=0.6, sweet=0.6, caffeine=0.6),
            func_need="none",
            keywords=("卷", "麻木", "没感觉", "随便", "都行", "无所谓", "躺平了", "佛系"),
            copy_tone="摆烂",
        ),
        MoodProfile(
            mood_id="broke",
            label="月末吃土",
            bleed_score=55,
            flavor=FlavorVector(salty=0.8, chewy=0.6, sweet=0.2, caffeine=0.2),
            kcal_pref=KcalPref.HIGH,
            value_pref=ValuePref.DEAL,  # 极致性价比
            func_need="none",
            keywords=("吃土", "没钱", "穷", "月末", "月底", "余额", "省", "便宜", "性价比"),
            copy_tone="抠门",
        ),
        MoodProfile(
            mood_id="sunday_dread",
            label="周日晚上恐惧",
            bleed_score=72,
            flavor=FlavorVector(salty=0.7, chewy=0.5, sweet=0.6, caffeine=0.3),
            func_need="sugar",
            keywords=("周日", "星期天", "明天上班", "假期结束", "不想明天", "周末结束"),
            copy_tone="安抚",
        ),
    ]
}

# 默认兜底情绪：什么都不命中时的「平淡日常」
DEFAULT_MOOD = MoodProfile(
    mood_id="neutral",
    label="平淡日常",
    bleed_score=40,
    flavor=FlavorVector(salty=0.7, chewy=0.6, sweet=0.4, caffeine=0.3),
)

# =============================================================================
# 二、强度修饰词 —— 让「有点累」和「累成狗」区分开
# =============================================================================

INTENSITY_BOOST = {
    "重度": 1.3, "严重": 1.3, "爆表": 1.4, "极度": 1.35, "真的": 1.2,
    "快": 1.15, "要": 1.1, "死": 1.3, "爆炸": 1.35, "崩溃": 1.3,
}
INTENSITY_DAMP = {"有点": 0.7, "稍微": 0.7, "一点点": 0.65, "还行": 0.8, "不太": 0.7}

# 叠词/重复标点 → 情绪更强（「我累了我累了我累了」/「气死我了！！！」）
REPEAT_PUNCT = re.compile(r"[!！?？]{2,}")


# =============================================================================
# 三、情绪识别
# =============================================================================


def _detect_sarcasm(text: str, profile: MoodProfile) -> bool:
    """
    反讽识别。

    典型场景：用户说「今天又是被甲方爸爸宠爱的一天呢 ☺️」
    字面是正向词（宠爱/开心），但结合「甲方」这个负向词 → 判定为反讽。

    判定规则（保守）：
      命中该情绪的 sarcasm_markers 中的正向词
      AND 同时命中该情绪的负向关键词（说明语境其实是负面的）
    """
    hit_marker = any(m in text for m in profile.sarcasm_markers)
    hit_negative = any(k in text for k in profile.keywords)
    return hit_marker and hit_negative


def _time_context(now: datetime | None = None) -> str:
    """
    时间上下文。真实场景应调用 MCP `now-time-info`，这里用本地时间兜底。

    返回值供上层做额外加权 / 过滤：
      late_night      深夜（>= 22 点或 < 5 点）→ 过滤早餐品
      monday_morning  周一上午 → 加权「周一综合征」
      sunday_evening  周日晚上 → 加权「周日恐惧」
      payday          每月 8~12 号 → 加权「发工资」
      month_end       每月 26 号后 → 加权「月末吃土」
      normal
    """
    now = now or datetime.now()
    h, wd, day = now.hour, now.weekday(), now.day

    if h >= 22 or h < 5:
        return "late_night"
    if wd == 0 and h < 11:
        return "monday_morning"
    if wd == 6 and h >= 17:
        return "sunday_evening"
    if 8 <= day <= 12:
        return "payday"
    if day >= 26:
        return "month_end"
    return "normal"


# 时间上下文 → 情绪加权（同义情绪更容易被选中）
TIME_MOOD_BONUS: dict[str, dict[str, float]] = {
    "late_night": {"overtime_crash": 0.25},
    "monday_morning": {"monday_blues": 0.3},
    "sunday_evening": {"sunday_dread": 0.3},
    "payday": {"payday": 0.3},
    "month_end": {"broke": 0.25},
}


def detect_mood(
    text: str,
    now: datetime | None = None,
    time_ctx: str | None = None,
) -> MoodMatch:
    """
    情绪识别主函数。

    流程：
      1. 关键词打分 —— 每种情绪统计命中词数量
      2. 时间上下文加权
      3. 强度修饰词调整 intensity
      4. 反讽检测（命中则 intensity 拉到 1.4）
      5. 计算 confidence 与最终 bleed_score

    Args:
        text:     用户原始输入
        now:      当前时间（真实场景传 MCP now_time_info 的解析结果）
        time_ctx: 直接指定时间上下文，优先级高于 now

    Returns:
        MoodMatch —— 含 mood_id / bleed_score / flavor / 各偏好
    """
    text = (text or "").strip()
    lower = text.lower()
    ctx = time_ctx or _time_context(now)

    # --- 1. 关键词打分 ---
    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for mid, p in MOOD_LIBRARY.items():
        hit = [k for k in p.keywords if k in text or k.lower() in lower]
        if not hit:
            continue
        # 命中词越多分越高，但边际递减，避免长句误判
        scores[mid] = min(1.0, 0.45 + 0.2 * len(hit))
        hits[mid] = hit

    # --- 2. 时间上下文加权 ---
    for mid, bonus in TIME_MOOD_BONUS.get(ctx, {}).items():
        scores[mid] = scores.get(mid, 0.0) + bonus

    # --- 3. 选最优 ---
    if scores:
        mood_id = max(scores, key=lambda k: scores[k])
        profile = MOOD_LIBRARY[mood_id]
        confidence = min(0.98, scores[mood_id])
        hit_keywords = hits.get(mood_id, [])
    else:
        profile = DEFAULT_MOOD
        mood_id = profile.mood_id
        confidence = 0.35  # 兜底，建议交给 LLM
        hit_keywords = []

    # --- 4. 强度修饰 ---
    intensity = 1.0
    for w, v in INTENSITY_BOOST.items():
        if w in text:
            intensity = max(intensity, v)
    for w, v in INTENSITY_DAMP.items():
        if w in text:
            intensity = min(intensity, v)
    if REPEAT_PUNCT.search(text):
        intensity = min(1.5, intensity + 0.15)

    # --- 5. 反讽 ---
    sarcasm = _detect_sarcasm(text, profile)
    if sarcasm:
        intensity = max(intensity, 1.4)  # 「我笑了」通常比直说更严重

    # 上限 95 而非 100：给 RP 留出「够用」的空间，
    # 否则满血的失血值会让所有方案都判定为不够，产品体验很挫败
    bleed = int(round(min(95, profile.bleed_score * intensity)))

    return MoodMatch(
        profile=profile,
        intensity=round(intensity, 2),
        bleed_score=bleed,
        confidence=round(confidence, 2),
        sarcasm=sarcasm,
        hit_keywords=hit_keywords,
        time_context=ctx,
        raw_text=text,
    )


def build_mood_prompt(text: str, time_ctx: str = "") -> str:
    """
    LLM 兜底用的提示词模板。

    当 detect_mood 的 confidence < 0.6 时，把这段提示词丢给 LLM，
    让它返回结构化 JSON，再用 parse_mood_llm() 解析回 MoodMatch。

    设计要点：
      - 把 12 种情绪的 id/label 全部列给 LLM，避免它自由发挥
      - 强制 JSON 输出，方便解析
      - 明确要求识别反讽
    """
    catalog = "\n".join(
        f"- {p.mood_id}（{p.label}）：失血值 {p.bleed_score}，"
        f"口味偏好 咸香{p.flavor.salty}/咀嚼{p.flavor.chewy}/甜{p.flavor.sweet}/咖啡因{p.flavor.caffeine}"
        for p in MOOD_LIBRARY.values()
    )
    return f"""你是「麦麦精神续命搭子」的情绪识别模块，负责判断一位中国打工人当前的心理状态。

# 可选情绪（只能从下面选一个 mood_id）
{catalog}
- neutral（平淡日常）：失血值 40，无明显情绪倾向

# 用户输入
"{text}"

# 时间上下文
{time_ctx or "未知"}

# 任务
1. 判断最匹配的 mood_id。注意识别**反讽**：中国人说「我笑了」「真好」「棒极了」往往是反话。
2. 给出 intensity（强度系数 0.5~1.5）。反讽或崩溃表达取高值。
3. 给出 confidence（置信度 0~1）。
4. 用一句不超过 20 字的话，共情用户的处境（不要安慰套话，要具体到场景）。

# 输出格式（仅输出 JSON，不要任何其他文字）
{{"mood_id": "...", "intensity": 1.0, "confidence": 0.9, "sarcasm": false, "empathy": "..."}}
"""


def parse_mood_llm(payload: dict, raw_text: str = "", time_ctx: str = "") -> MoodMatch:
    """
    把 LLM 返回的 JSON 解析成 MoodMatch（与规则识别的输出结构一致，
    上层无需区分是哪条路径产出的）。
    """
    mood_id = str(payload.get("mood_id") or "neutral")
    profile = MOOD_LIBRARY.get(mood_id, DEFAULT_MOOD)
    intensity = float(payload.get("intensity") or 1.0)
    return MoodMatch(
        profile=profile,
        intensity=intensity,
        bleed_score=int(round(min(100, profile.bleed_score * intensity))),
        confidence=float(payload.get("confidence") or 0.8),
        sarcasm=bool(payload.get("sarcasm")),
        time_context=time_ctx,
        raw_text=raw_text,
    )


# =============================================================================
# 四、商品打分 —— RP 续命值
# =============================================================================
#
# RP = 40×taste_fit + 20×energy_fit + 20×func_fit + 20×value_fit
#                                                  ↑ 注意：这里的 value 是「性价比取向」，
#                                                    真正的「优惠暴击」分量在 main.py 里
#                                                    拿到券后补上（deal_calculator 只有算完才知道）
# 所以 rank_items 返回的是 rp_base（满分 80），main.py 补满到 100。


def _energy_fit(item: MenuItem, pref: KcalPref) -> float:
    """热量契合度 0~1。加班要高热，摸鱼要低负担"""
    kcal = item.kcal or 500  # 缺数据时用中位数兜底
    if pref == KcalPref.HIGH:
        return min(1.0, kcal / 900.0)
    if pref == KcalPref.LOW:
        return min(1.0, max(0.0, (700.0 - kcal) / 500.0))
    # MID：500~800 之间最舒服，两头递减
    return max(0.0, 1.0 - abs(kcal - 650.0) / 650.0)


def _func_fit(item: MenuItem, func_need: str) -> float:
    """
    功能契合度 0~1。

    caffeine → 需要咖啡因提神（咖啡 / 拿铁 / 可乐）
    chew     → 需要咀嚼泄愤（鸡翅 / 鸡腿 / 酥脆类）
    sugar    → 需要糖分疗愈（麦旋风 / 派 / 甜品）
    none     → 不看功能，给 0.6 基准分
    """
    if func_need == "caffeine":
        return min(1.0, item.flavor.caffeine + 0.3)
    if func_need == "chew":
        return min(1.0, item.flavor.chewy + 0.2)
    if func_need == "sugar":
        return min(1.0, item.flavor.sweet + 0.2)
    return 0.6


def _value_fit(item: MenuItem, pref: ValuePref) -> float:
    """
    消费取向契合度 0~1。

    DEAL（抠门）    → 每元热量越高越好
    PREMIUM（犒赏） → 反而偏好贵的、限定的，便宜货扣印象分
    """
    if pref == ValuePref.PREMIUM:
        # 贵 = 值得感；同时看标签里有没有「限定 / 新品」
        premium_tag = 0.2 if any(t in item.tags for t in ("限定", "新品", "安格斯", "厚牛")) else 0.0
        return min(1.0, 0.3 + item.price / 60.0 + premium_tag)
    # DEAL：以 25 kcal/元 为满分线
    return min(1.0, item.kcal_per_yuan / 25.0)


def _speed_fit(item: MenuItem, pref: SpeedPref) -> float:
    """出餐速度契合度（叠加到 func_fit 上做微调）"""
    if pref == SpeedPref.FAST:
        return max(0.0, 1.0 - item.prep_seconds / 300.0)
    return 0.8


def rank_items(items: list[MenuItem], mood: MoodMatch, top_k: int = 12) -> list[ScoredItem]:
    """
    按情绪给商品打分排序。

    Returns:
        ScoredItem 列表（按 rp_base 降序），每个含 parts 分项，用于 UI 画进度条。
    """
    p = mood.profile
    scored: list[ScoredItem] = []

    for it in items:
        if not it.available:
            continue
        taste = p.flavor.cosine(it.flavor)                     # 0~1
        energy = _energy_fit(it, p.kcal_pref)                  # 0~1
        func = _func_fit(it, p.func_need) * _speed_fit(it, p.speed_pref)  # 0~1
        value = _value_fit(it, p.value_pref)                   # 0~1

        rp_base = 40 * taste + 20 * energy + 20 * func + 20 * value
        scored.append(
            ScoredItem(
                item=it,
                rp_base=round(rp_base, 1),
                parts={
                    "口味": round(40 * taste, 1),
                    "热量": round(20 * energy, 1),
                    "功能": round(20 * func, 1),
                    "取向": round(20 * value, 1),
                },
            )
        )

    scored.sort(key=lambda s: s.rp_base, reverse=True)
    return scored[:top_k]


def score_bundle(items: list[MenuItem], mood: MoodMatch) -> tuple[float, dict[str, float]]:
    """
    **套餐维度**的 RP 打分（0 ~ 80，不含优惠暴击）。

    为什么不能简单平均单品分数？
    ----------------------------
    反例：加班情绪需要咖啡因，而「安格斯厚牛堡」的 caffeine = 0。
    若按单品打分再平均，汉堡会被咖啡因这一项拖死 —— 但事实上
    「汉堡 + 冰美式」这个组合完全满足诉求，因为**提神由那杯咖啡负责**。

    所以套餐打分必须在组合层面算：
      - 口味  : 用组合的**平均**口味向量（代表这顿饭整体的味道）
      - 热量  : 用组合的**总**热量（吃一顿的量）
      - 功能  : 用组合的**最大**能力值（有一杯咖啡就够了，不需要每样都提神）
      - 出餐  : 用平均出餐时长
      - 取向  : 用总热量 / 总价

    Returns:
        (rp_base, parts) —— parts 与单品打分结构一致，可直接画进度条
    """
    if not items:
        return 0.0, {}

    p = mood.profile
    n = len(items)
    dims = ("salty", "chewy", "sweet", "caffeine")

    # 平均口味向量 + 最大能力向量
    avg_vals = [sum(getattr(it.flavor, d) for it in items) / n for d in dims]
    max_vals = [max(getattr(it.flavor, d) for it in items) for d in dims]
    avg_flavor = FlavorVector(*avg_vals)
    max_flavor = FlavorVector(*max_vals)

    total_kcal = sum(it.kcal for it in items)
    total_price = sum(it.price for it in items)
    avg_prep = sum(it.prep_seconds for it in items) / n

    # --- 口味 ---
    taste = p.flavor.cosine(avg_flavor)

    # --- 热量（按整餐总量判断）---
    if p.kcal_pref == KcalPref.HIGH:
        energy = min(1.0, total_kcal / 1000.0)
    elif p.kcal_pref == KcalPref.LOW:
        energy = min(1.0, max(0.0, (600.0 - total_kcal) / 450.0))
    else:
        energy = max(0.0, 1.0 - abs(total_kcal - 700.0) / 700.0)

    # --- 功能（取组合最大能力）---
    if p.func_need == "caffeine":
        func = min(1.0, max_flavor.caffeine + 0.15)
    elif p.func_need == "chew":
        func = min(1.0, max_flavor.chewy + 0.10)
    elif p.func_need == "sugar":
        func = min(1.0, max_flavor.sweet + 0.10)
    else:
        func = 0.6
    speed = max(0.0, 1.0 - avg_prep / 300.0)
    func = func * (0.5 + 0.5 * speed)  # 出餐慢只扣分一半，别一刀切

    # --- 取向 ---
    if p.value_pref == ValuePref.PREMIUM:
        tag = 0.2 if any(
            any(t in it.tags for t in ("限定", "新品", "安格斯", "厚牛")) for it in items
        ) else 0.0
        value = min(1.0, 0.25 + total_price / 70.0 + tag)
    else:
        value = min(1.0, (total_kcal / total_price) / 25.0) if total_price > 0 else 0.0

    rp_base = 40 * taste + 20 * energy + 20 * func + 20 * value
    parts = {
        "口味": round(40 * taste, 1),
        "热量": round(20 * energy, 1),
        "功能": round(20 * func, 1),
        "取向": round(20 * value, 1),
    }
    return round(rp_base, 1), parts


def compose_reason(mood: MoodMatch, scored: ScoredItem) -> str:
    """
    生成一句「人话理由」——不是模板套话，而是基于打最高分的那个分量来说。
    """
    p = mood.profile
    top_part = max(scored.parts, key=lambda k: scored.parts[k])
    reasons = {
        "口味": f"你现在的口味偏好是「{_flavor_desc(p.flavor)}」，这道菜对得上。",
        "热量": f"{'需要高热量硬撑' if p.kcal_pref == KcalPref.HIGH else '要轻负担别犯困'}，它刚好卡在这个区间。",
        "功能": {
            "caffeine": "咖啡因拉满，够你撑到下班。",
            "chew": "酥脆耐嚼 —— 你需要一个能咬的东西。",
            "sugar": "糖分疗愈，科学有效。",
        }.get(p.func_need, "没什么特别的，就是适合。"),
        "取向": "性价比最高，每一分钱都花在刀刃上。"
        if p.value_pref == ValuePref.DEAL
        else "够有仪式感，值得拿来犒赏自己。",
    }
    return reasons.get(top_part, "它挺适合你现在的状态。")


def _flavor_desc(v: FlavorVector) -> str:
    """把口味向量翻译成人话（取最高的那一维）"""
    mapping = {"咸香扎实": v.salty, "酥脆耐嚼": v.chewy, "甜口疗愈": v.sweet, "提神醒脑": v.caffeine}
    return max(mapping, key=lambda k: mapping[k])
