"""
麦麦「打工人精神续命」搭子 —— 主入口（CLI）

用法
----
    python main.py "累成狗"                    # 干跑，只推荐不下单（默认）
    python main.py "发工资了！！！" --budget 60
    python main.py "甲方要五彩斑斓的黑，我笑了" --style crazy
    python main.py "想摸鱼" --confirm          # 真正下单（会要求二次确认）
    python main.py "加班" --json               # 输出原始 JSON，便于调试

流程
----
    用户输入
      ↓
    now_time_info()              拿时间上下文（深夜 / 周一 / 发薪日）
      ↓
    detect_mood()                情绪识别 → 失血值 + 口味向量
      ↓
    query_nearby_stores()        选门店
      ↓
    query_meals() + enrich_nutrition()   实时菜单 + 热量
      ↓
    available_coupons() → auto_bind_coupons() → query_store_coupons()
                                ⚠️ 先领满券，再算价（顺序不能反）
      ↓
    rank_items()                 按情绪给商品打 RP 分
      ↓
    build_bundles()              组装 3 个候选方案
      ↓
    optimize()                   券组合搜索 + 凑单决策
      ↓
    render()                     输出 Markdown
      ↓ (仅 --confirm)
    create_order()               下单
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from mcd_mood_buddy import copywriting, renderer
from mcd_mood_buddy.annotation import annotate_menu, coverage_report
from mcd_mood_buddy.deal_calculator import optimize
from mcd_mood_buddy.mcp_client import build_client
from mcd_mood_buddy.models import CartLine, DineMode, Recommendation
from mcd_mood_buddy.mood_engine import (
    MOOD_LIBRARY,
    compose_reason,
    detect_mood,
    rank_items,
    score_bundle,
)

# 每种情绪的方案命名（比「方案一/二/三」有记忆点得多）
PLAN_NAMES: dict[str, list[str]] = {
    "pissed_off": ["泄愤流", "止血流", "破防流"],
    "overtime_crash": ["硬撑流", "省命流", "破防流"],
    "slacking": ["续摸流", "低负担流", "放纵流"],
    "payday": ["犒赏流", "限定流", "值得流"],
    "emo": ["疗愈流", "糖分流", "抱抱流"],
    "broke": ["抠门流", "极致性价比流", "活着就行流"],
    "diet_broken": ["破防流", "彻底摆烂流", "明天再说流"],
    "monday_blues": ["唤醒流", "续命流", "硬拉流"],
    "rushed": ["快取流", "极速流", "边走边吃流"],
}
DEFAULT_PLAN_NAMES = ["轻盈版", "标准版", "豪华版"]

# 深夜不推的品类（真实场景应依赖 query-meals 的可售标记，这里是额外保险）
LATE_NIGHT_EXCLUDE_TAGS = ("早餐", "早餐时段")
LATE_NIGHT_EXCLUDE_CATEGORIES = ("早餐",)


# ---------------------------------------------------------------------------
# 方案组装
# ---------------------------------------------------------------------------


def _pick_by_category(ranked, keywords: tuple[str, ...], exclude_codes: set[str]):
    """从打分结果里挑指定品类的第一名（如「饮品」「小食」）"""
    for s in ranked:
        if s.item.code in exclude_codes:
            continue
        blob = s.item.category + "".join(s.item.tags) + s.item.name
        if any(k in blob for k in keywords):
            return s
    return None


def build_bundles(ranked, mood) -> list[tuple[str, list[CartLine]]]:
    """
    把打分后的商品组装成 3 个候选方案。

    策略：
      轻盈版 = 情绪分第 1 名（单点，最省钱）
      标准版 = 第 1 名 + 最佳饮品（最均衡）
      豪华版 = 第 1 名 + 第 2 名 + 最佳饮品（续命值拉满，也最贵）
    """
    if not ranked:
        return []

    first = ranked[0]
    used = {first.item.code}

    drink = _pick_by_category(ranked, ("饮", "咖啡", "可乐", "拿铁", "茶"), used)
    if drink:
        used.add(drink.item.code)

    second = next((s for s in ranked[1:] if s.item.code not in used), None)

    bundles: list[tuple[str, list[CartLine]]] = [
        ("轻盈版", [CartLine(item=first.item, qty=1)]),
    ]
    if drink:
        bundles.append(
            ("标准版", [CartLine(item=first.item, qty=1), CartLine(item=drink.item, qty=1)])
        )
    if second and drink:
        bundles.append(
            (
                "豪华版",
                [
                    CartLine(item=first.item, qty=1),
                    CartLine(item=second.item, qty=1),
                    CartLine(item=drink.item, qty=1),
                ],
            )
        )
    return bundles


def apply_time_filter(items, time_ctx: str):
    """按时间上下文过滤商品（深夜不推早餐品）"""
    if time_ctx != "late_night":
        return items
    out = []
    for it in items:
        if it.category in LATE_NIGHT_EXCLUDE_CATEGORIES:
            continue
        if any(t in it.tags for t in LATE_NIGHT_EXCLUDE_TAGS):
            continue
        out.append(it)
    return out or items  # 全被过滤掉就不过滤，避免空菜单


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(
    text: str,
    budget: float | None = None,
    dine_mode: DineMode = DineMode.PICKUP,
    address: str = "",
    mock: bool = True,
    style: str = copywriting.STYLE_CRAZY,
    confirm: bool = False,
    top: int = 3,
    as_json: bool = False,
    fallback_to_mock: bool = False,
    verbose: bool = False,
):
    """
    完整跑一遍流程，返回 (markdown 字符串, 订单对象或 None)

    Args:
        fallback_to_mock: --live 模式下真实接口挂了是否自动降级到 Mock
            （**只对只读接口生效**，算价 / 下单永远不降级）
        verbose: 打印调用埋点到 stderr
    """

    client = build_client(
        mock=mock, fallback_to_mock=fallback_to_mock, verbose=verbose
    )
    mode = "mock" if client.mock else "live"
    t_start = time.perf_counter()

    # --- 1. 首批并行：时间 / 门店 / 营养表（三者互不依赖）---
    # 真实网络下单次 RTT 约 60~120ms，串行拉这三项要 ~300ms；并行只要一次往返。
    # 营养表在这里只是**预热缓存**，稍后 enrich_nutrition 会直接命中。
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_time = ex.submit(client.now_time_info)
        f_stores = ex.submit(client.query_nearby_stores, address)
        f_nutrition = ex.submit(client.list_nutrition_foods)
        now_info = f_time.result()
        stores = f_stores.result()
        f_nutrition.result()  # 结果丢弃，目的是把 24h 缓存灌进去

    t_gather1 = time.perf_counter() - t_start

    try:
        now_dt = datetime.strptime(now_info["datetime"], "%Y-%m-%d %H:%M:%S")
    except (KeyError, ValueError):
        now_dt = datetime.now()
    now_str = now_dt.strftime("%H:%M")

    # --- 2. 情绪识别 ---
    mood = detect_mood(text, now=now_dt)
    # 置信度不足 → 输出 LLM 兜底提示词（TODO: 接 LLM 后用 parse_mood_llm 回填）
    if mood.confidence < 0.6:
        from mcd_mood_buddy.mood_engine import build_mood_prompt

        # TODO(llm-fallback): 把 build_mood_prompt(text, mood.time_context) 的结果
        # 交给 LLM，再用 parse_mood_llm(payload, text, mood.time_context) 覆盖 mood
        _ = build_mood_prompt(text, mood.time_context)

    # --- 3. 门店已在首批并行取回 ---
    store = stores[0] if stores else None
    store_id = store.store_id if store else ""

    # --- 4+5. 第二批并行：菜单 / 可领券 / 一键领券 ---
    # 依赖关系要守住：
    #   · query_meals        需要 storeId（已有）
    #   · query_store_coupons 必须在 auto_bind_coupons **之后** —— 先领满再查可用
    # 所以 menu / available_coupons / auto_bind 并行跑，最后再串行查门店可用券。
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_menu = ex.submit(
            lambda: annotate_menu(
                client.enrich_nutrition(client.query_meals(store_id)), missing_only=True
            )
        )
        f_avail = ex.submit(client.available_coupons)   # 展示「还能领几张」
        f_bind = ex.submit(client.auto_bind_coupons)    # 一键领满所有券
        menu_raw = f_menu.result()
        f_avail.result()
        f_bind.result()

    menu = menu_raw
    if verbose:
        cov = coverage_report(menu)
        print(
            f"[anno] 有效口味向量 {cov['covered']}/{cov['total']} "
            f"({cov['coverage']:.0%})，本次新标注 {cov['newly_annotated']} 项 "
            f"{cov['sources']}",
            file=sys.stderr,
        )

    menu = apply_time_filter(menu, mood.time_context)

    coupons = client.query_store_coupons(store_id)  # 拿这家店真正能用的
    t_gather2 = time.perf_counter() - t_start

    # --- 6. 情绪打分 ---
    # 单品打分用于「选谁进候选 / 凑单时优先加谁」；
    # 但**最终 RP 必须用 score_bundle 在套餐维度算**（见该函数注释里的咖啡因反例）
    ranked = rank_items(menu, mood, top_k=len(menu))

    # --- 7. 组装方案 + 券优化 ---
    recs: list[Recommendation] = []
    names = PLAN_NAMES.get(mood.mood_id, DEFAULT_PLAN_NAMES)

    for idx, (default_name, lines) in enumerate(build_bundles(ranked, mood)):
        # ① 本地搜索「用哪套券 + 要不要凑单」
        deal = optimize(
            lines=lines,
            coupons=coupons,
            menu=menu,
            dine_mode=dine_mode,
            budget=budget,
            ranked=ranked,
        )

        # ② 以 MCP calculate-price 的返回为准，回写权威金额
        #    （原则：本地只做搜索，绝不自己算钱）
        price = client.calculate_price(
            lines=[{"code": ln.item.code, "qty": ln.qty, "price": ln.item.price} for ln in deal.lines],
            coupon_ids=[c.coupon_id for c in deal.applied],
            store_id=store_id,
            dine_mode=dine_mode,
            discount_hint=deal.discount,
        )
        deal.subtotal = price.subtotal
        deal.discount = price.discount
        deal.delivery_fee = price.delivery_fee
        deal.payable = price.payable

        # TODO(real-mcp): 真实模式下应在此处校验 price.payable 与本地估算的一致性，
        #   差异 > 0.01 元说明券状态已变（被抢完 / 已过期），应重跑 optimize 而非直接展示。

        # ③ 套餐维度打分（注意：凑单品也要算进去，否则「多送的那个」不影响体验就怪了）
        rp_base, parts = score_bundle([ln.item for ln in deal.lines], mood)

        # ④ 优惠暴击：折扣率 40% 即满分，把 RP 从 80 补满到 100
        crit = 20.0 * min(1.0, deal.discount_rate / 0.4)
        parts["暴击"] = round(crit, 1)
        rp = round(min(100.0, rp_base + crit), 1)  # RP 上限 100

        rec = Recommendation(
            mood=mood,
            plan_name=names[idx] if idx < len(names) else default_name,
            lines=deal.lines,
            deal=deal,
            rp=rp,
            rp_parts=parts,
            reason=compose_reason(mood, ranked[0]),
            store=store,
            enough=rp >= mood.bleed_score,
            over_budget=bool(budget and deal.payable > budget),
        )
        rec.copywriting = copywriting.generate(rec, style=style, now=now_str)
        recs.append(rec)

    # --- 8. 排序：优先「够用」，其次 RP 高，最后便宜 ---
    recs.sort(key=lambda r: (r.enough, r.rp, -r.deal.payable), reverse=True)
    recs = recs[:top]

    # 方案名按**展示顺序**重命名 —— 第 1 名永远叫「硬撑流」而不是「破防流」，
    # 否则用户看到的编号和名字对不上，很出戏
    for i, r in enumerate(recs):
        if i < len(names):
            r.plan_name = names[i]

    # --- 9. 下单（安全闸门）---
    order = None
    if confirm and recs:
        best = recs[0]
        payload = [
            {"code": ln.item.code, "qty": ln.qty, "price": ln.item.price} for ln in best.deal.lines
        ]
        coupon_ids = [c.coupon_id for c in best.deal.applied]
        # TODO(real-mcp): 真实场景这里应先调 calculate_price 再 create_order，
        # 并把 MCP 返回的实付金额与本地估算做一次一致性校验（差异 > 0.01 则中止）
        order = client.create_order(
            lines=payload,
            store_id=store_id,
            dine_mode=dine_mode,
            coupon_ids=coupon_ids,
            confirm=True,
        )
        best.copywriting = copywriting.generate(best, style=style, order_id=order.order_id, now=now_str)

    # --- 10. 输出 ---
    if as_json:
        payload = {
            "mood": {
                "id": mood.mood_id,
                "label": mood.label,
                "bleed_score": mood.bleed_score,
                "confidence": mood.confidence,
                "sarcasm": mood.sarcasm,
                "time_context": mood.time_context,
                "hits": mood.hit_keywords,
            },
            "store": store.name if store else "",
            "plans": [
                {
                    "name": r.plan_name,
                    "items": [{"name": ln.item.name, "qty": ln.qty, "price": ln.item.price} for ln in r.lines],
                    "subtotal": r.deal.subtotal,
                    "discount": r.deal.discount,
                    "payable": r.deal.payable,
                    "coupons": [c.name for c in r.deal.applied],
                    "filler": r.deal.filler.name if r.deal.filler else None,
                    "rp": r.rp,
                    "enough": r.enough,
                    "copy": r.copywriting,
                }
                for r in recs
            ],
            "order": {"id": order.order_id, "pickup": order.pickup_code} if order else None,
        }
        return json.dumps(payload, ensure_ascii=False, indent=2), order

    md = renderer.render(recs, client_mode=mode, order=order, store_name=store.name if store else "")

    if verbose:
        # 埋点打到 stderr：不污染 stdout，方便 `python main.py ... --json | jq`
        s = client.stats
        total_ms = int((time.perf_counter() - t_start) * 1000)
        print(
            f"[stats] mode={mode} calls={s['calls']} retries={s['retries']} "
            f"cache_hits={s['cache_hits']} mock_fallbacks={s['mock_fallbacks']} "
            f"errors={s['errors']} rate_remain={client._limiter.remaining}",
            file=sys.stderr,
        )
        print(
            f"[timing] 数据抓取 {int(t_gather1*1000)}ms → {int(t_gather2*1000)}ms ｜ 全流程 {total_ms}ms",
            file=sys.stderr,
        )
    return md, order


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcd-mood-buddy",
        description="麦麦「打工人精神续命」搭子 —— 听得懂你今天有多惨，并给你算好券",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  python main.py "累成狗"\n'
            '  python main.py "发工资了！！！" --budget 60 --style flex\n'
            '  python main.py "甲方要五彩斑斓的黑，我笑了" --style crazy\n'
            "  python main.py \"想摸鱼\" --confirm\n"
        ),
    )
    # nargs="?" + default=None：让 `--list-moods` 等无需输入的命令也能单独使用
    p.add_argument("text", nargs="?", default=None, help="你现在的精神状态，一句话就行")
    p.add_argument("--budget", type=float, default=None, help="预算上限（元）")
    p.add_argument(
        "--dine",
        choices=[m.value for m in DineMode],
        default=DineMode.PICKUP.value,
        help="就餐方式（默认 pickup 到店取餐）",
    )
    p.add_argument("--address", default="", help="地址关键词，用于选门店")
    p.add_argument(
        "--style",
        choices=[copywriting.STYLE_CRAZY, copywriting.STYLE_REPORT, copywriting.STYLE_FLEXY],
        default=copywriting.STYLE_CRAZY,
        help="文案文体（默认 crazy 发疯文学）",
    )
    p.add_argument("--top", type=int, default=3, help="展示几个方案（默认 3）")
    p.add_argument("--json", action="store_true", help="输出 JSON 而非 Markdown")
    p.add_argument(
        "--confirm",
        action="store_true",
        help="真正下单。默认 --dry-run，只推荐不下单",
    )
    p.add_argument("--live", action="store_true", help="使用真实 MCP（需要 MCD_MCP_TOKEN）")
    p.add_argument("--list-moods", action="store_true", help="列出所有支持的情绪并退出")
    p.add_argument(
        "--doctor",
        action="store_true",
        help="体检真实 MCP 链路（Token / 握手 / 协议 / Tool 清单 / 限流），不点餐",
    )
    p.add_argument(
        "--auto-mock",
        action="store_true",
        help="--live 时若真实接口不可用，只读接口自动降级到 Mock（答辩断网保命）",
    )
    p.add_argument("--verbose", action="store_true", help="打印 MCP 调用埋点到 stderr")
    return p


def _print_doctor(rep: dict) -> int:
    """把体检报告渲染成终端友好的表格"""
    print(f"\n🩺 MCP 链路体检  ——  {rep['base_url']}")
    print(f"   模式 {rep['mode']} ｜ Token {rep['token']}")
    print("-" * 62)
    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
    for c in rep["checks"]:
        print(f"{icon.get(c['status'], '  ')} {c['name']}")
        print(f"     {c['detail']}")
    print("-" * 62)
    if rep["ok"]:
        print("结论：链路健康，可以跑 `--live` 了 ✅\n")
        return 0
    print("结论：存在问题，按上面的建议修复后再试 ❌\n")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_moods:
        print(f"{'mood_id':<18}{'标签':<16}{'失血值':<8}功能诉求")
        print("-" * 58)
        for p in MOOD_LIBRARY.values():
            print(f"{p.mood_id:<18}{p.label:<16}{p.bleed_score:<8}{p.func_need or '-'}")
        return 0

    # 体检模式：只验链路，不点餐
    if args.doctor:
        from mcd_mood_buddy.mcp_client import build_client as _build

        client = _build(mock=not args.live)
        try:
            return _print_doctor(client.doctor())
        finally:
            client.close()

    if not args.text:
        build_parser().print_help()
        print('\n错误：请告诉我你现在的状态，例如：python main.py "累成狗"')
        return 2

    md, order = run(
        text=args.text,
        budget=args.budget,
        dine_mode=DineMode(args.dine),
        address=args.address,
        mock=not args.live,
        style=args.style,
        confirm=args.confirm,
        top=args.top,
        as_json=args.json,
        fallback_to_mock=args.auto_mock,
        verbose=args.verbose,
    )
    print(md)

    if not args.confirm and not args.json:
        print()
        print("提示：加 --confirm 才会真正下单（默认 --dry-run 安全档）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
