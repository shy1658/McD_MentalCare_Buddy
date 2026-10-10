"""
最优优惠计算 —— 券组合搜索 + 凑单决策。

要解决的问题
------------
用户想买单品 A、B（比如一个堡 + 一杯可乐），账上有若干张券：
    满 40 减 8 / 第二份半价 / 麦辣鸡翅立减 5 / 全场 8 折 ...
问：**怎么用券最省钱？要不要为了凑满减多买一个东西？**

难点在于「凑单悖论」：
    多买一个 ¥9 的派 → 总价涨 ¥9，但触发满 40 减 10 → 实付反而**少 ¥1**。
    这种反直觉的最优解，人脑算不出来，但用户看到会「哇」一声 —— 这正是本项目的爽点。

算法思路
--------
    1. 把券按 kind 分组：主券（满减/折扣/套餐）｜单品券｜第二份半价
    2. 枚举「主券 × 单品券」的组合（数量很小，暴力可行）
       第二份半价几乎总能叠加，直接全应用
    3. 对每个组合算实付，取最小
    4. 若主券是满减且没到门槛 → 尝试凑单：
       在菜单里找「价格刚好补齐差额且最便宜」的商品，重算实付，更便宜才采纳
    5. 全程受预算上限约束

复杂度：主券数 ≤ ~10，单品券 ≤ ~10 → 组合数 ≤ 100，再乘凑单候选，毫秒级完成。
不需要动态规划，暴力搜索完全够用，且**可解释性远好于 DP**。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    CartLine,
    Coupon,
    CouponKind,
    DealResult,
    DineMode,
    MenuItem,
    MoodMatch,
    ScoredItem,
)

# 配送费（真实场景应由 calculate-price 返回，这里仅 Mock/预估用）
DELIVERY_FEE = 6.0


@dataclass
class StackPolicy:
    """
    券叠加规则。

    ⚠️ 麦当劳实际规则会随活动变化，这里给出保守默认：
       主券（满减 / 折扣 / 套餐）只能用一张，单品券可用一张，第二份半价可叠加。

    TODO(real-mcp): 接真实接口后，应从券详情里读 stackable / exclusiveGroup 字段，
                    而不是依赖这里的硬编码假设。
    """

    max_main: int = 1          # 满减 / 折扣 / 套餐券最多用几张
    max_item: int = 1          # 单品券最多用几张
    second_half_stackable: bool = True
    # 单品券能否与满减券叠加（保守起见默认 True，因为单品券作用于特定商品）
    item_with_main: bool = True


# 主券类型：互斥的那一组
_MAIN_KINDS = (CouponKind.THRESHOLD, CouponKind.PERCENT, CouponKind.COMBO)


# ---------------------------------------------------------------------------
# 单组合评估
# ---------------------------------------------------------------------------


@dataclass
class _Eval:
    """一次评估的中间结果"""

    discount: float = 0.0
    explain: list[str] = field(default_factory=list)
    used: list[Coupon] = field(default_factory=list)


def _subtotal(lines: list[CartLine]) -> float:
    return round(sum(ln.subtotal for ln in lines), 2)


def _apply_main(coupon: Coupon | None, subtotal: float, codes: set[str]) -> tuple[float, str]:
    """应用主券（满减 / 折扣 / 套餐），返回 (减免额, 说明)"""
    if coupon is None:
        return 0.0, ""

    if coupon.kind == CouponKind.THRESHOLD:
        if subtotal + 1e-9 >= coupon.threshold:
            return coupon.discount, f"{coupon.name}：满 {coupon.threshold:.0f} 减 {coupon.discount:.0f}"
        return 0.0, ""

    if coupon.kind == CouponKind.PERCENT:
        if subtotal + 1e-9 >= coupon.threshold:
            off = round(subtotal * (1.0 - coupon.rate), 2)
            return off, f"{coupon.name}：{coupon.rate * 10:.1f} 折，省 {off:.2f}"
        return 0.0, ""

    if coupon.kind == CouponKind.COMBO:
        if all(c in codes for c in coupon.item_codes):
            return coupon.discount, f"{coupon.name}：套餐立减 {coupon.discount:.0f}"
        return 0.0, ""

    return 0.0, ""


def _apply_item(coupon: Coupon | None, lines: list[CartLine]) -> tuple[float, str]:
    """应用单品券：找到购物车里第一个（也是最贵的一个）适用商品"""
    if coupon is None:
        return 0.0, ""
    matched = [ln for ln in lines if coupon.applies_to(ln.item)]
    if not matched:
        return 0.0, ""
    target = max(matched, key=lambda ln: ln.item.price)
    return coupon.discount, f"{coupon.name}：{target.item.name} 立减 {coupon.discount:.0f}"


def _apply_second_half(coupons: list[Coupon], lines: list[CartLine]) -> tuple[float, list[str], list[Coupon]]:
    """
    第二份半价：每 2 件中第 2 件半价。

    例：麦辣鸡翅 ¥13 × 2 → 省 6.5
    """
    total = 0.0
    notes: list[str] = []
    used: list[Coupon] = []
    for c in coupons:
        for ln in lines:
            if not c.applies_to(ln.item):
                continue
            pairs = ln.qty // 2
            if pairs <= 0:
                continue
            off = round(ln.item.price * (1.0 - c.rate) * pairs, 2)
            if off <= 0:
                continue
            total += off
            notes.append(f"{c.name}：{ln.item.name} ×{ln.qty} 省 {off:.2f}")
            if c not in used:
                used.append(c)
    return round(total, 2), notes, used


def _evaluate(
    lines: list[CartLine],
    main: Coupon | None,
    item_coupon: Coupon | None,
    second_half: list[Coupon],
    policy: StackPolicy,
    dine_mode: DineMode,
) -> _Eval:
    """评估一个券组合下的总减免"""
    subtotal = _subtotal(lines)
    codes = {ln.item.code for ln in lines}

    off_main, note_main = _apply_main(main, subtotal, codes)
    off_item, note_item = _apply_item(item_coupon, lines)
    if main and item_coupon and not policy.item_with_main:
        off_item, note_item = 0.0, ""
    off_half, notes_half, used_half = _apply_second_half(second_half, lines)

    discount = round(off_main + off_item + off_half, 2)
    # 减免不能超过商品总额
    discount = min(discount, subtotal)

    explain = [n for n in [note_main, note_item, *notes_half] if n]
    used = [c for c in [main, item_coupon] if c] + used_half
    return _Eval(discount=discount, explain=explain, used=used)


def _to_deal(
    lines: list[CartLine],
    ev: _Eval,
    dine_mode: DineMode,
    filler: MenuItem | None = None,
    saving_vs_no_filler: float = 0.0,
) -> DealResult:
    subtotal = _subtotal(lines)
    fee = DELIVERY_FEE if dine_mode == DineMode.DELIVERY else 0.0
    payable = round(max(0.0, subtotal - ev.discount) + fee, 2)
    return DealResult(
        lines=lines,
        applied=ev.used,
        filler=filler,
        subtotal=subtotal,
        discount=ev.discount,
        delivery_fee=fee,
        payable=payable,
        explain=ev.explain,
        saving_vs_no_filler=round(saving_vs_no_filler, 2),
    )


# ---------------------------------------------------------------------------
# 凑单搜索
# ---------------------------------------------------------------------------


def _find_fillers(
    gap: float,
    lines: list[CartLine],
    menu: list[MenuItem],
    ranked: list[ScoredItem] | None = None,
    limit: int = 8,
) -> list[MenuItem]:
    """
    找凑单候选：价格 >= 差额（补足门槛）且尽量便宜。

    排序策略（很重要）：
      1. 先看能不能刚好补上差额 —— 价格越接近 gap 越优先，避免凑过头
      2. 价格相同时，若给了 ranked（情绪打分），优先选情绪分高的
         → 「凑单也要凑你想吃的」，这是体验差异点
    """
    in_cart = {ln.item.code for ln in lines}
    score_by_code = {s.item.code: s.rp_base for s in (ranked or [])}

    cands: list[MenuItem] = []
    for it in menu:
        if not it.available or it.code in in_cart:
            continue
        if it.price + 1e-9 < gap:
            continue  # 补不满门槛
        cands.append(it)

    # 已有商品再买一份也算凑单（有时比加新品更自然）
    for ln in lines:
        if ln.item.price + 1e-9 >= gap:
            cands.append(ln.item)

    # 排序：先按「超出差额多少」分 3 元一档（保证不会为了凑单乱加贵的），
    # 同一档内优先情绪分高的 —— 凑单也要凑你想吃的，而不是随便塞一个
    cands.sort(
        key=lambda it: (
            round((it.price - gap) / 3.0),
            -score_by_code.get(it.code, 0.0),
            it.price,
        )
    )
    return cands[:limit]


def _add_filler(lines: list[CartLine], filler: MenuItem) -> list[CartLine]:
    """把凑单品加入购物车（已存在则数量 +1）"""
    out = [CartLine(item=ln.item, qty=ln.qty) for ln in lines]
    for ln in out:
        if ln.item.code == filler.code:
            ln.qty += 1
            return out
    out.append(CartLine(item=filler, qty=1))
    return out


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def optimize(
    lines: list[CartLine],
    coupons: list[Coupon],
    menu: list[MenuItem] | None = None,
    policy: StackPolicy | None = None,
    dine_mode: DineMode = DineMode.PICKUP,
    budget: float | None = None,
    ranked: list[ScoredItem] | None = None,
    allow_filler: bool = True,
) -> DealResult:
    """
    计算最优优惠方案。

    Args:
        lines:      用户想买的商品（可含数量）
        coupons:    当前可用券（应是 query_store_coupons 的结果）
        menu:       完整菜单，用于凑单搜索；为 None 时跳过凑单
        policy:     券叠加规则
        dine_mode:  就餐方式（影响配送费）
        budget:     预算上限（超预算的方案会被排除；全超时返回最便宜的并标记）
        ranked:     情绪打分结果，用于凑单品的选择偏好
        allow_filler: 是否允许凑单（关掉可用于对比「不凑单」的基线）

    Returns:
        DealResult —— 含最终购物车（可能含凑单品）、用券清单、实付、说明
    """
    policy = policy or StackPolicy()
    menu = menu or []

    mains = [c for c in coupons if c.kind in _MAIN_KINDS]
    item_coupons = [c for c in coupons if c.kind == CouponKind.ITEM]
    halves = (
        [c for c in coupons if c.kind == CouponKind.SECOND_HALF]
        if policy.second_half_stackable
        else []
    )

    best: DealResult | None = None

    # --- 枚举「主券 × 单品券」 ---
    main_choices: list[Coupon | None] = [None] + mains
    item_choices: list[Coupon | None] = [None] + item_coupons

    for main in main_choices:
        for ic in item_choices:
            # 同一张券不能用两次
            if main is not None and ic is not None and main.coupon_id == ic.coupon_id:
                continue

            ev = _evaluate(lines, main, ic, halves, policy, dine_mode)
            deal = _to_deal(lines, ev, dine_mode)

            # --- 凑单：仅当主券是满减且未达门槛时才可能有利可图 ---
            if allow_filler and menu and main is not None and main.kind == CouponKind.THRESHOLD:
                subtotal = _subtotal(lines)
                gap = main.threshold - subtotal
                if gap > 0:
                    for filler in _find_fillers(gap, lines, menu, ranked):
                        new_lines = _add_filler(lines, filler)
                        ev2 = _evaluate(new_lines, main, ic, halves, policy, dine_mode)
                        deal2 = _to_deal(new_lines, ev2, dine_mode, filler=filler)
                        # 凑单后比原方案更便宜才算赚到
                        saving = deal.payable - deal2.payable
                        if saving > 0:
                            deal2.saving_vs_no_filler = saving
                            deal2.explain.append(
                                f"凑单 +{filler.name}（¥{filler.price:.0f}）"
                                f"→ 触发「{main.name}」，实付反而少 ¥{saving:.2f}"
                            )
                            deal = deal2
                        break  # 候选已按「最接近 gap」排序，第一个就够好

            if best is None or deal.payable < best.payable:
                best = deal

    if best is None:  # 理论上不会发生，保险起见
        best = _to_deal(lines, _evaluate(lines, None, None, [], policy, dine_mode), dine_mode)

    # --- 预算约束 ---
    if budget is not None and best.payable > budget:
        # 退而求其次：去掉凑单品，或直接砍掉最贵的一件
        trimmed = _trim_to_budget(lines, coupons, policy, dine_mode, budget)
        if trimmed is not None:
            best = trimmed

    return best


def _trim_to_budget(
    lines: list[CartLine],
    coupons: list[Coupon],
    policy: StackPolicy,
    dine_mode: DineMode,
    budget: float,
) -> DealResult | None:
    """超预算时逐步砍掉最贵的商品，直到压进预算。返回 None 表示砍到底也不行。"""
    cur = [CartLine(item=ln.item, qty=ln.qty) for ln in lines]
    while cur:
        # 找最贵的一件，数量 -1（减到 0 则移除）
        priciest = max(cur, key=lambda ln: ln.item.price)
        priciest.qty -= 1
        if priciest.qty <= 0:
            cur.remove(priciest)
        if not cur:
            return None
        deal = optimize(cur, coupons, menu=[], policy=policy, dine_mode=dine_mode, allow_filler=False)
        if deal.payable <= budget:
            deal.explain.append(f"已按 ¥{budget:.0f} 预算精简商品")
            return deal
    return None


# ---------------------------------------------------------------------------
# 辅助：把主流程需要的「凑单前后对比」讲清楚
# ---------------------------------------------------------------------------


def compare_with_without_filler(
    lines: list[CartLine],
    coupons: list[Coupon],
    menu: list[MenuItem],
    **kw,
) -> tuple[DealResult, DealResult]:
    """
    返回 (不凑单方案, 最优方案)，用于在 UI 上明确展示「凑单赚了多少」。

    这是本项目最能打动用户的一屏：
        不凑单：¥38.00
        凑单后：¥35.50（多拿一个派，反而省 ¥2.50）
    """
    no_filler = optimize(lines, coupons, menu=[], allow_filler=False, **kw)
    best = optimize(lines, coupons, menu=menu, allow_filler=True, **kw)
    return no_filler, best
