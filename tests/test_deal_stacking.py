"""
券叠加规则测试（P1-4）—— 无需 pytest::

    python tests/test_deal_stacking.py

**这个套件守的是一条会直接伤到用户的线。**

早期版本只按 `StackPolicy` 里的硬编码假设组合券，**不读券自身的
`stackable` 字段**。后果：一张明确标注「不可与其他券叠加」的大额券，
仍会被拿去和别的券组合，算出来的实付比用户到店实际能付的更低。
用户按这个价格预期下单，结算时发现贵了 —— 这是项目里唯一
「不是体验问题、而是会误导消费」的缺陷。

覆盖：
  1. stacking_allowed 五条规则
  2. ⭐ 不可叠加券绝不出现在多券组合中
  3. ⭐ 不可叠加券单独使用仍然生效（不能因噎废食把它扔掉）
  4. exclusive_group 互斥
  5. 真实 Mock 数据（C_P85 全场85折不可叠加）的端到端行为
  6. 透明度：被排除的组合要有说明，不能让用户以为系统漏算
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_mood_buddy.deal_calculator import (  # noqa: E402
    StackPolicy,
    optimize,
    stacking_allowed,
)
from mcd_mood_buddy.models import (  # noqa: E402
    CartLine,
    Coupon,
    CouponKind,
    DineMode,
    MenuItem,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "  ok  " if cond else " FAIL "
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail and not cond else ""))


def _item(code: str, name: str, price: float, category: str = "汉堡") -> MenuItem:
    return MenuItem(code=code, name=name, price=price, category=category)


# =============================================================================
# 1. 规则本身
# =============================================================================

def test_stacking_rules():
    print("\n=== A. stacking_allowed 五条规则 ===")

    normal = Coupon("A", "满30减5", CouponKind.THRESHOLD, stackable=True)
    solo = Coupon("B", "大额券", CouponKind.THRESHOLD, discount=20, stackable=False)

    check("单张券永远合法（即使不可叠加）", stacking_allowed([solo], StackPolicy())[0])
    check("空组合合法", stacking_allowed([], StackPolicy())[0])
    check("可叠加券可两两组合", stacking_allowed(
        [normal, Coupon("A2", "单品券", CouponKind.ITEM, stackable=True)], StackPolicy()
    )[0])
    ok, why = stacking_allowed([normal, solo], StackPolicy())
    check("不可叠加券不得参与组合", not ok, why)
    check("  且给出券名", "大额券" in why, why)

    # 互斥组
    g1 = Coupon("G1", "互斥A", CouponKind.THRESHOLD, exclusive_group="G")
    g2 = Coupon("G2", "互斥B", CouponKind.THRESHOLD, exclusive_group="G")
    ok2, why2 = stacking_allowed([g1, g2], StackPolicy())
    check("同互斥组只能选一张", not ok2, why2)
    check("  且说明组名", "G" in why2, why2)

    # 数量上限
    ok3, why3 = stacking_allowed([normal, Coupon("A2", "满40减8", CouponKind.THRESHOLD)], StackPolicy())
    check("满减券最多 1 张", not ok3, why3)

    # 第二份半价的全局开关：单独用可以，同用不行
    half = Coupon("H", "第二份半价", CouponKind.SECOND_HALF, rate=0.5, stackable=True)
    check("第二份半价单独使用合法", stacking_allowed([half], StackPolicy())[0])
    ok4, why4 = stacking_allowed([normal, half], StackPolicy(second_half_stackable=False))
    check("开关关闭时不可与主券同用", not ok4, why4)


# =============================================================================
# 2. ⭐ 不可叠加券绝不出现在多券组合中
# =============================================================================

def test_solo_coupon_never_paired():
    print("\n=== B. 不可叠加券不得被组合（核心修复）===")

    items = [_item("BURGER", "厚牛堡", 45.0), _item("COLA", "可乐", 12.0)]
    lines = [CartLine(item=it, qty=1) for it in items]

    solo = Coupon(
        "P85", "全场85折", CouponKind.PERCENT,
        threshold=0, rate=0.85, stackable=False,
    )
    item_coupon = Coupon(
        "ITEM6", "厚牛堡立减6", CouponKind.ITEM,
        discount=6.0, item_codes=["BURGER"], stackable=True,
    )

    # 作弊算法：无视 stackable 硬叠加 → 得到的"价格"比实际低
    cheat_discount = round(57.0 * 0.15, 2) + 6.0     # 8.55 + 6 = 14.55
    honest_discount = max(round(57.0 * 0.15, 2), 6.0)  # 8.55 或 6 —— 只能选一个

    deal = optimize(lines, [solo, item_coupon], menu=[])
    applied_ids = [c.coupon_id for c in deal.applied]

    check(
        "不可叠加券未与单品券同用",
        not ({"P85", "ITEM6"} <= set(applied_ids)),
        str(applied_ids),
    )
    check("用了券", len(applied_ids) >= 1, str(applied_ids))
    check(
        "减免额不超过单券上限（没算出假低价）",
        deal.discount <= honest_discount + 1e-9,
        f"discount={deal.discount} honest={honest_discount} cheat={cheat_discount}",
    )


# =============================================================================
# 3. ⭐ 单独使用仍然生效
# =============================================================================

def test_solo_coupon_still_usable():
    print("\n=== C. 不可叠加券单独使用仍然生效 ===")

    it = _item("BURGER", "厚牛堡", 45.0)
    lines = [CartLine(item=it, qty=1)]
    solo = Coupon(
        "P85", "全场85折", CouponKind.PERCENT,
        threshold=0, rate=0.85, stackable=False,
    )

    deal = optimize(lines, [solo], menu=[])
    check("单独用不可叠加券能拿到折扣", deal.discount > 0, str(deal.discount))
    check("折扣为 45×15% = 6.75", abs(deal.discount - 6.75) < 0.01, str(deal.discount))
    check("券被记录", [c.coupon_id for c in deal.applied] == ["P85"])


# =============================================================================
# 4. 第二份半价：开关关闭时仍可单独使用
# =============================================================================

def test_second_half_alone():
    print("\n=== D. 第二份半价：不可叠加时仍能单独省 ===")

    # ⚠️ 数量必须是 3：13×2=26 达不到满30门槛，满减券不会生效，
    #    那样就测不出"能否同用"这件事了
    wings = _item("WINGS", "麦辣鸡翅", 13.0, "小食")
    lines = [CartLine(item=wings, qty=3)]
    half = Coupon(
        "HALF", "鸡翅第二份半价", CouponKind.SECOND_HALF,
        rate=0.5, item_codes=["WINGS"], stackable=True,
    )
    main = Coupon("TH30", "满30减5", CouponKind.THRESHOLD, threshold=30, discount=5, stackable=True)

    # 开关关闭 → 半价券不能和满减同用
    deal = optimize(lines, [half, main], policy=StackPolicy(second_half_stackable=False), menu=[])
    ids = {c.coupon_id for c in deal.applied}
    check("半价券未与满减同用", not ({"HALF", "TH30"} <= ids), str(ids))
    check("仍然省到钱", deal.discount > 0, str(deal.discount))

    # 开关打开 → 可以同用
    deal2 = optimize(lines, [half, main], policy=StackPolicy(second_half_stackable=True), menu=[])
    ids2 = {c.coupon_id for c in deal2.applied}
    check("开关打开时可同用", ids2 == {"HALF", "TH30"}, str(ids2))
    check("同用时省更多", deal2.discount > deal.discount, f"{deal2.discount} vs {deal.discount}")


# =============================================================================
# 5. 真实 Mock 数据
# =============================================================================

def test_real_mock_data():
    print("\n=== E. 真实 Mock 券数据（C_P85 不可叠加）===")

    root = Path(__file__).resolve().parent.parent
    raw = json.loads((root / "data/mock/coupons.json").read_text(encoding="utf-8"))
    coupons = [Coupon.from_dict(d) for d in raw if d.get("usable", True)]

    p85 = next(c for c in coupons if c.coupon_id == "C_P85")
    check("C_P85 被正确读为不可叠加", p85.stackable is False, str(p85.stackable))

    menu_raw = json.loads((root / "data/mock/menu.json").read_text(encoding="utf-8"))
    menu = [MenuItem.from_dict(d) for d in menu_raw]

    burger = next(i for i in menu if i.price > 40)
    lines = [CartLine(item=burger, qty=1)]

    deal = optimize(lines, coupons, menu=[], dine_mode=DineMode.PICKUP)
    ids = [c.coupon_id for c in deal.applied]

    # 正确的断言不是"只能有一张券"——「满减 + 单品券」本身就是合法组合。
    # 要守的是：**一旦 C_P85 出现，它就必须是唯一一张**。
    if "C_P85" in ids:
        check("C_P85 出现时必须是唯一一张券", len(ids) == 1, str(ids))
        check(
            "85 折后金额正确",
            abs(deal.discount - round(burger.price * 0.15, 2)) < 0.01,
            str(deal.discount),
        )
    else:
        check("C_P85 未被选中（被更优的合法组合取代）", True, str(ids))

    # 无论如何：组合里绝不能同时出现不可叠加券 + 其他券
    applied_coupons = deal.applied
    non_stackable = [c for c in applied_coupons if not c.stackable]
    check(
        "结果中不可叠加券绝不与他人同用",
        not (non_stackable and len(applied_coupons) > 1),
        str([c.coupon_id for c in applied_coupons]),
    )


# =============================================================================
# 6. 透明度
# =============================================================================

def test_transparency():
    print("\n=== F. 被排除的组合要有说明 ===")

    items = [_item("BURGER", "厚牛堡", 45.0)]
    lines = [CartLine(item=it, qty=1) for it in items]
    solo = Coupon("P85", "全场85折", CouponKind.PERCENT, rate=0.85, stackable=False)
    item_coupon = Coupon("I6", "立减6", CouponKind.ITEM, discount=6.0, item_codes=["BURGER"])

    deal = optimize(lines, [solo, item_coupon], menu=[])
    joined = " ".join(deal.explain)
    check("输出解释了为什么没叠加", "叠加规则" in joined, joined[:120])


def main() -> int:
    test_stacking_rules()
    test_solo_coupon_never_paired()
    test_solo_coupon_still_usable()
    test_second_half_alone()
    test_real_mock_data()
    test_transparency()

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
