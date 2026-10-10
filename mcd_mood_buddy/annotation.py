"""
本地菜单标注层 —— 真实 MCP 数据与 RP 公式之间的「补钙制度」。

为什么必须有这一层？
--------------------------------
「麦麦精神续命搭子」的核心是把情绪映射成口味向量再算相似度，但**官方 `query-meals`
返回的字段只有分类、编码、名称、价格、标签**，没有任何口味数据。
直接喂进 `rank_items()` 的结果是：所有商品的 FlavorVector 都是零向量 →
余弦相似度恒为 0 → RP 的口味分量恒为 0 → 推荐彻底退化成「按热量乱排」。

解法：本地维护一张「商品/关键词/分类 → 口味向量」的映射表
（`data/mappings/flavor_map.json`），在真实菜单进算法之前补齐缺失维度。

设计原则：
1. **只补缺失**：Mock 数据自带 flavor 时不覆盖（missing_only=True），
   保证离线演示效果与线上标注能力互相独立、便于分别调优。
2. **外部可配**：规则放在 JSON 而不是代码里，调参不用改 Python，
   评委面前现场调「麦旋风有多甜」这种事是可以演示的。
3. **可审计**：补出来的每个值都记在 item.tags 上标注来源，
   出了问题一秒钟定位是哪条规则生效。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import FlavorVector, MenuItem

DEFAULT_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "mappings" / "flavor_map.json"

_CACHE: dict[str, Any] | None = None


def load_map(path: str | Path | None = None) -> dict[str, Any]:
    """加载映射表（进程内缓存，热重载无需求）"""
    global _CACHE
    if _CACHE is not None and path is None:
        return _CACHE
    p = Path(path) if path else DEFAULT_MAP_PATH
    try:
        table = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        table = {}
    if path is None:
        _CACHE = table
    return table


def resolve_rule(name: str, category: str, table: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """
    按 精确名 → 关键词 → 分类 → 默认 的优先级找到一条规则。

    Returns:
        (规则 dict, 命中的来源标记)，来源形如 `exact` / `kw:堡` / `cat:汉堡` / `default`
    """
    t = table or load_map()

    exact = (t.get("exact") or {}).get(name)
    if exact:
        return exact, "exact"

    for rule in t.get("keyword") or []:
        for kw in rule.get("contains") or []:
            if kw and kw in name:
                return rule, f"kw:{kw}"

    cat = (t.get("category") or {}).get(category)
    if cat:
        return cat, f"cat:{category}"

    return t.get("default") or {}, "default"


def flavor_from_rule(rule: dict[str, Any]) -> FlavorVector:
    f = rule.get("flavor") or {}
    return FlavorVector(
        salty=float(f.get("salty", 0.0)),
        chewy=float(f.get("chewy", 0.0)),
        sweet=float(f.get("sweet", 0.0)),
        caffeine=float(f.get("caffeine", 0.0)),
    )


def annotate_item(item: MenuItem, table: dict[str, Any] | None = None,
                  missing_only: bool = True) -> MenuItem:
    """
    给单个商品补齐口味向量 / 出餐时长。

    Args:
        missing_only: True 时仅在商品「口味为零向量」或「出餐时长为默认值」才补，
            避免覆盖 Mock 数据里人工调过的精标注。
    """
    already_annotated = item.flavor.norm() > 0.0
    if missing_only and already_annotated and item.prep_seconds != 120:
        return item

    rule, source = resolve_rule(item.name, item.category, table)
    if not already_annotated or not missing_only:
        item.flavor = flavor_from_rule(rule)
    if item.prep_seconds == 120 or not missing_only:
        item.prep_seconds = int(rule.get("prep_seconds", 120))

    # 来源写进 tags：调试时一眼看出是谁标错的
    tag = f"anno:{source}"
    if tag not in item.tags:
        item.tags = list(item.tags) + [tag]
    return item


def annotate_menu(items: list[MenuItem], missing_only: bool = True) -> list[MenuItem]:
    """批量标注。真实菜单进 `rank_items()` 之前必须过这一层。"""
    table = load_map()
    return [annotate_item(it, table, missing_only=missing_only) for it in items]


def coverage_report(items: list[MenuItem]) -> dict[str, Any]:
    """
    标注覆盖率——衡量这一层做得好不好。

    看的是「有多少商品具备有效口味向量」，而不是「本次补了几条」——
    Mock 数据自带标注时不应被算成没覆盖。

    返回::

        {"total": 26, "covered": 26, "newly_annotated": 4,
         "coverage": 1.0, "sources": {"kw:堡": 8, "cat:汉堡": 3, ...}}
    """
    total = len(items)
    sources: dict[str, int] = {}
    covered = 0
    newly = 0
    for it in items:
        tags = [t for t in it.tags if t.startswith("anno:")]
        if tags:
            newly += 1
            key = tags[0][5:]
            sources[key] = sources.get(key, 0) + 1
        if it.flavor.norm() > 0.0:  # 只要有非零口味向量就算被覆盖
            covered += 1
    return {
        "total": total,
        "covered": covered,
        "coverage": round(covered / total, 3) if total else 0.0,
        "newly_annotated": newly,
        "sources": dict(sorted(sources.items(), key=lambda kv: -kv[1])),
    }
