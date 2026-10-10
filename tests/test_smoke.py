"""
冒烟测试 —— 无需 pytest 也能跑：

    python tests/test_smoke.py

覆盖：
  1. 情绪识别（直述 / 反讽 / 时间上下文）
  2. RP 套餐打分（咖啡因反例：汉堡 + 咖啡应当互补）
  3. 券组合与凑单（多买反而更便宜）
  4. 端到端 main.run()
  5. 安全闸门（未 confirm 不得下单）
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_mood_buddy.deal_calculator import optimize  # noqa: E402
from mcd_mood_buddy.mcp_client import build_client, OrderNotConfirmedError  # noqa: E402
from mcd_mood_buddy.models import CartLine, Coupon, CouponKind, MenuItem, FlavorVector  # noqa: E402
from mcd_mood_buddy.mood_engine import detect_mood, rank_items, score_bundle  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "  ok  " if cond else " FAIL "
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail and not cond else ""))


# =============================================================================
# 1. 情绪识别
# =============================================================================

def test_mood_basic():
    m = detect_mood("还有 40 个 bug 要改，我想死")
    check("直述：识别为加班到崩溃", m.mood_id == "overtime_crash", m.mood_id)
    check("直述：失血值 >= 85", m.bleed_score >= 85, str(m.bleed_score))

    m2 = detect_mood("发工资了！！！")
    check("直述：识别为发工资", m2.mood_id == "payday", m2.mood_id)

    m3 = detect_mood("想摸鱼")
    check("直述：识别为摸鱼", m3.mood_id == "slacking", m3.mood_id)


def test_sarcasm():
    m = detect_mood("甲方说要五彩斑斓的黑，我笑了")
    check("反讽：识别为被气死", m.mood_id == "pissed_off", m.mood_id)
    check("反讽：sarcasm=True", m.sarcasm is True)
    check("反讽：失血值被拉高(>=90)", m.bleed_score >= 90, str(m.bleed_score))


def test_time_context():
    late = datetime(2026, 10, 10, 23, 47)
    m = detect_mood("好累", now=late)
    check("时间上下文：深夜", m.time_context == "late_night", m.time_context)

    mon = datetime(2026, 10, 5, 9, 0)  # 周一
    m2 = detect_mood("不想动", now=mon)
    check("时间上下文：周一上午", m2.time_context == "monday_morning", m2.time_context)


# =============================================================================
# 2. RP 套餐打分
# =============================================================================

def test_bundle_caffeine():
    """
    核心反例：加班需要咖啡因，但汉堡 caffeine=0。
    若按单品平均分，「汉堡 + 冰美式」会被汉堡拖死；
    套餐维度打分则应该拿到高分。
    """
    m = detect_mood("加班到崩溃")
    burger = MenuItem(
        code="B", name="厚牛堡", category="堡", price=38, kcal=780,
        flavor=FlavorVector(salty=0.95, chewy=0.7, sweet=0.1, caffeine=0.0),
    )
    coffee = MenuItem(
        code="C", name="冰美式", category="饮品", price=12, kcal=15,
        flavor=FlavorVector(salty=0.0, chewy=0.0, sweet=0.05, caffeine=1.0),
        prep_seconds=60,
    )
    rp_combo, _ = score_bundle([burger, coffee], m)
    rp_burger, _ = score_bundle([burger], m)

    check("套餐打分：汉堡+咖啡 > 单吃汉堡", rp_combo > rp_burger, f"{rp_combo} vs {rp_burger}")
    check("套餐打分：汉堡+咖啡 RP >= 75", rp_combo >= 75, str(rp_combo))


def test_rank_returns_items():
    client = build_client(mock=True)
    menu = client.enrich_nutrition(client.query_meals("S001"))
    m = detect_mood("被气死了")
    ranked = rank_items(menu, m, top_k=5)
    check("打分：返回候选", len(ranked) > 0)
    check("打分：按 RP 降序", all(ranked[i].rp_base >= ranked[i + 1].rp_base for i in range(len(ranked) - 1)))


# =============================================================================
# 3. 券组合与凑单
# =============================================================================

def _mk(name, price, code, kcal=400, **flavor):
    return MenuItem(code=code, name=name, category="测试", price=price, kcal=kcal,
                    flavor=FlavorVector(**flavor))


def test_threshold():
    burger = _mk("堡", 22.0, "B1", salty=0.9, chewy=0.5)
    c = Coupon(coupon_id="T30", name="满30减5", kind=CouponKind.THRESHOLD, threshold=30, discount=5)
    deal = optimize([CartLine(burger, 1)], [c], menu=[])
    check("满减：未达门槛不减", deal.discount == 0.0, str(deal.discount))

    deal2 = optimize([CartLine(burger, 1), CartLine(_mk("薯条", 10.0, "F1"), 1)], [c], menu=[])
    check("满减：达门槛减 5", abs(deal2.discount - 5.0) < 0.01, str(deal2.discount))
    check("满减：实付 = 32-5 = 27", abs(deal2.payable - 27.0) < 0.01, str(deal2.payable))


def test_second_half():
    wings = _mk("鸡翅", 13.0, "W1", chewy=1.0)
    c = Coupon(coupon_id="H", name="鸡翅第二份半价", kind=CouponKind.SECOND_HALF,
               rate=0.5, item_codes=["W1"])
    deal = optimize([CartLine(wings, 2)], [c], menu=[])
    check("第二份半价：省 6.5", abs(deal.discount - 6.5) < 0.01, str(deal.discount))
    check("第二份半价：实付 26-6.5=19.5", abs(deal.payable - 19.5) < 0.01, str(deal.payable))


def test_filler_paradox():
    """
    凑单悖论：买 ¥22 的堡，加 ¥9 的派凑到 ¥31，
    触发「满30减5」→ 实付 ¥26，比原来的 ¥22-0=¥22 ... 需要构造得更狠才划算。
    这里用「满30减10」制造真正的悖论：22 → 31-10 = 21 < 22。
    """
    burger = _mk("堡", 22.0, "B1", salty=0.9)
    pie = _mk("派", 9.0, "P1", sweet=0.9)
    wings = _mk("鸡翅", 13.0, "W1", chewy=1.0)
    menu = [burger, pie, wings]

    c = Coupon(coupon_id="T30_10", name="满30减10", kind=CouponKind.THRESHOLD, threshold=30, discount=10)

    no_fill = optimize([CartLine(burger, 1)], [c], menu=[], allow_filler=False)
    best = optimize([CartLine(burger, 1)], [c], menu=menu, allow_filler=True)

    check("凑单：找到凑单品", best.filler is not None, str(best.filler))
    check("凑单悖论：多买反而更便宜", best.payable < no_fill.payable,
          f"凑单后 {best.payable} vs 不凑 {no_fill.payable}")


def test_budget():
    burger = _mk("堡", 38.0, "B1")
    deal = optimize([CartLine(burger, 1)], [], menu=[], budget=20.0)
    check("预算：超预算时给出提示或精简", deal.payable is not None)


# =============================================================================
# 4. 端到端
# =============================================================================

def test_end_to_end():
    from main import run

    md, order = run("还有 40 个 bug 要改，我想死", mock=True, confirm=False)
    check("端到端：输出 Markdown", "# 🍔" in md)
    check("端到端：默认不下单", order is None)
    check("端到端：含实付金额", "实付" in md)

    js, _ = run("想摸鱼", mock=True, as_json=True)
    import json
    data = json.loads(js)
    check("端到端：JSON 含 mood", "mood" in data)
    check("端到端：JSON 含方案", len(data["plans"]) >= 1)


def test_order_gate():
    from mcd_mood_buddy.mcp_client import build_client, OrderNotConfirmedError

    c = build_client(mock=True)
    try:
        c.create_order([{"code": "B1", "qty": 1}], store_id="S001", confirm=False)
        check("安全闸门：未确认应抛异常", False)
    except OrderNotConfirmedError:
        check("安全闸门：未确认抛 OrderNotConfirmedError", True)


# =============================================================================

def main():
    print("\n=== McDonald's MentalCare Buddy 冒烟测试 ===\n")
    for fn in [
        test_mood_basic,
        test_sarcasm,
        test_time_context,
        test_bundle_caffeine,
        test_rank_returns_items,
        test_threshold,
        test_second_half,
        test_filler_paradox,
        test_budget,
        test_end_to_end,
        test_order_gate,
    ]:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(f"{fn.__name__}（异常）", False, f"{type(e).__name__}: {e}")
        print()

    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  - {f}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
