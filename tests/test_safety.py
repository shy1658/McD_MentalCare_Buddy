"""
心理安全层测试 —— 无需 pytest::

    python tests/test_safety.py

这一套测试守的是**安全底线**，不是功能。任何一条失败都必须当作 P0 处理。

覆盖：
  1. 三档判定（NONE / CONCERN / CRISIS）
  2. ⭐ 语境降档：「改 bug 改到想死」不得阻断（否则真正的危机信号会被稀释）
  3. ⭐ 告别式表达不降档：就算混在「bug」语境里也必须 CRISIS
  4. ⭐ CRISIS 档不得产生任何 MCP 调用、不得出现任何点餐内容
  5. CONCERN 档强制克制文体（不许发疯文学）
  6. 热线完整性
  7. JSON 输出结构
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_mood_buddy import safety  # noqa: E402
from mcd_mood_buddy.safety import (  # noqa: E402
    HOTLINES,
    CrisisLevel,
    detect_crisis,
    recommended_style,
)
from main import run  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "  ok  " if cond else " FAIL "
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail and not cond else ""))


# =============================================================================
# 1. 三档判定
# =============================================================================

def test_level_detection():
    print("\n=== A. 三档判定 ===")

    cases_none = [
        "累成狗",
        "甲方要五彩斑斓的黑，我笑了",
        "发工资了！！！",
        "想摸鱼",
        "今天天气不错",
    ]
    for t in cases_none:
        r = detect_crisis(t)
        check(f"正常输入不触发：{t[:12]}", r.level is CrisisLevel.NONE, r.level.value)

    # 吐槽语境 → CONCERN（不阻断）
    for t in [
        "还有40个bug要改 我想死",
        "加班到想死",
        "甲方又改需求，我不想活了",
        "论文写不完了想死",
    ]:
        r = detect_crisis(t)
        check(f"吐槽语境降为 CONCERN：{t[:14]}", r.level is CrisisLevel.CONCERN, r.level.value)

    # 无语境 → CRISIS
    for t in ["我不想活了", "活不下去了", "想死"]:
        r = detect_crisis(t)
        check(f"无语境判定 CRISIS：{t}", r.level is CrisisLevel.CRISIS, r.level.value)


# =============================================================================
# 2. ⭐ 告别式绝不降档
# =============================================================================

def test_farewell_never_downgrades():
    print("\n=== B. 告别式表达（任何语境都不降档）===")

    for t in [
        "以后不用管我了",
        "bug 改不完了，以后不用管我了",
        "帮我照顾好我妈",
        "我写好遗书了",
    ]:
        r = detect_crisis(t)
        check(f"告别式必为 CRISIS：{t[:16]}", r.level is CrisisLevel.CRISIS, r.level.value)
        check(f"  且阻断下单：{t[:16]}", r.blocks_ordering is True)


# =============================================================================
# 3. 绝望类
# =============================================================================

def test_hopeless():
    print("\n=== C. 持续性绝望 ===")

    for t in ["活着没意思", "不会好了", "撑不下去了"]:
        r = detect_crisis(t)
        check(f"绝望类判 CRISIS：{t}", r.level is CrisisLevel.CRISIS, r.level.value)


# =============================================================================
# 4. ⭐ CRISIS 档：不产生任何 MCP 调用、不含任何点餐内容
# =============================================================================

def test_crisis_blocks_everything():
    print("\n=== D. CRISIS 档短路（最关键的一条）===")

    md, order = run("我不想活了", mock=True)
    check("CRISIS 不返回订单", order is None)
    check("CRISIS 输出不含价格", "实付" not in md, md[:80])
    check("CRISIS 输出不含门店", "门店" not in md)
    check("CRISIS 输出不含方案", "【" not in md)
    check("CRISIS 输出含援助热线", "12356" in md)
    check("CRISIS 输出含降级说明", "未调用任何麦当劳 MCP 接口" in md)

    # 告别式同样短路
    md2, order2 = run("以后不用管我了", mock=True)
    check("告别式也不返回订单", order2 is None)
    check("告别式输出含热线", "12356" in md2)


# =============================================================================
# 5. CONCERN 档：不阻断 + 强制克制文体
# =============================================================================

def test_concern_not_blocking():
    print("\n=== E. CONCERN 档不阻断但附关怀 ===")

    md, order = run("还有40个bug要改 我想死", mock=True)
    check("CONCERN 仍出推荐", "实付" in md)
    check("CONCERN 输出含关怀横幅", "不太放心" in md)
    check("CONCERN 输出含热线", "12356" in md)
    check("CONCERN 横幅在情绪诊断之前", md.index("不太放心") < md.index("情绪诊断"))
    check("CONCERN 未下单", order is None)

    # 文体强制降级
    check(
        "CONCERN 强制 report 文体",
        recommended_style(CrisisLevel.CONCERN, "crazy") == "report",
    )
    check(
        "NONE 档保留用户选择",
        recommended_style(CrisisLevel.NONE, "crazy") == "crazy",
    )

    # 实际跑一遍：CONCERN 下的文案不应出现发疯文学特征词
    md2, _ = run("加班到想死", style="crazy", mock=True)
    check("CONCERN 下不发自毁幽默", "我活着唯一的证据" not in md2)


# =============================================================================
# 6. 热线完整性
# =============================================================================

def test_hotlines():
    print("\n=== F. 援助渠道 ===")

    check("至少 3 条热线", len(HOTLINES) >= 3, str(len(HOTLINES)))
    check("含全国 24h 热线 12356", any(t == "12356" for _, t in HOTLINES))
    for name, tel in HOTLINES:
        check(f"热线号码非空：{name}", bool(tel.strip()))


# =============================================================================
# 7. JSON 输出
# =============================================================================

def test_json_output():
    print("\n=== G. JSON 输出 ===")

    raw, _ = run("我不想活了", mock=True, as_json=True)
    payload = json.loads(raw)
    check("CRISIS JSON 含 crisis 字段", "crisis" in payload)
    check("CRISIS JSON level=crisis", payload["crisis"]["level"] == "crisis")
    check("CRISIS JSON 无推荐", payload.get("recommendations") == [])
    check("CRISIS JSON 带热线", len(payload["crisis"]["hotlines"]) >= 3)

    raw2, _ = run("加班到想死", mock=True, as_json=True)
    payload2 = json.loads(raw2)
    check("CONCERN JSON level=concern", payload2["crisis"]["level"] == "concern")
    check("CONCERN JSON 有推荐", len(payload2.get("plans", [])) > 0)


# =============================================================================
# 8. 边界
# =============================================================================

def test_edge_cases():
    print("\n=== H. 边界输入 ===")

    for t in ["", "   ", None]:
        r = detect_crisis(t)  # type: ignore[arg-type]
        check(f"空输入安全返回 NONE：{t!r}", r.level is CrisisLevel.NONE)

    r = detect_crisis("不想死，我只是很累")
    check("含否定词仍会命中（保守优先）", r.triggered is True, r.level.value)

    # 大写 / 半角不影响
    r2 = detect_crisis("我不想活了")
    check("中文不受大小写影响", r2.level is CrisisLevel.CRISIS)


def main() -> int:
    test_level_detection()
    test_farewell_never_downgrades()
    test_hopeless()
    test_crisis_blocks_everything()
    test_concern_not_blocking()
    test_hotlines()
    test_json_output()
    test_edge_cases()

    print()
    print("=" * 64)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("❌ 失败项：")
        for f in FAIL:
            print(f"   - {f}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
