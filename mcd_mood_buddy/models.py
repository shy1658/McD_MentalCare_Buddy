"""
数据模型层 —— 全项目共用的结构体。

设计原则：
1. 所有模型都是「MCP 返回结构」与「本地算法结构」的中间层。
   真实 MCP 返回的字段名可能与这里不同，只需在 mcp_client.py 的 _parse_* 里做一次映射，
   上层算法完全不用改。
2. 金额统一用 float（元），但对外展示时统一 round(2)。
3. 所有模型支持从 dict 构造（from_dict），方便直接吃 Mock JSON。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class CouponKind(str, Enum):
    """优惠券类型（对应麦当劳麦麦省常见券种）"""

    THRESHOLD = "threshold"    # 满 X 减 Y，如「满 40 减 8」
    SECOND_HALF = "second_half"  # 第二份半价（通常是活动而非券，但这里统一抽象）
    PERCENT = "percent"        # 折扣券，如「全场 8 折」
    ITEM = "item"              # 指定单品立减，如「麦辣鸡腿堡立减 5」
    COMBO = "combo"            # 套餐券，如「1+1 随心配 13.9」


class DineMode(str, Enum):
    """就餐方式。对应 create-order 的入参"""

    PICKUP = "pickup"      # 到店取餐
    DELIVERY = "delivery"  # 麦乐送
    DINE_IN = "dine_in"    # 堂食
    DRIVE_THRU = "drive_thru"  # 得来速（v1.0.5+ 支持）


class KcalPref(str, Enum):
    """热量偏好：由情绪推导"""

    HIGH = "high"    # 需要高热量补给（加班 / 崩溃）
    MID = "mid"      # 无所谓
    LOW = "low"      # 要低负担（摸鱼 / 减肥）


class ValuePref(str, Enum):
    """消费取向：由情绪推导"""

    DEAL = "deal"        # 性价比优先（吃土 / 日常）
    PREMIUM = "premium"  # 犒赏优先（发工资 / 庆祝）


class SpeedPref(str, Enum):
    FAST = "fast"  # 出餐快（赶时间 / 焦虑）
    ANY = "any"


# ---------------------------------------------------------------------------
# 口味向量：情绪与商品的公共坐标系
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlavorVector:
    """
    四维口味向量，取值 0.0 ~ 1.0。

    这是整个「情绪 → 商品」映射的数学基础：
    情绪有一个 FlavorVector（我现在想要什么味道），
    商品也有一个 FlavorVector（这道菜是什么味道），
    两者做余弦相似度就是 RP 公式里的 taste_fit。
    """

    salty: float = 0.0      # 咸香 / 肉感 / 扎实
    chewy: float = 0.0      # 酥脆 / 需要咀嚼（泄愤用）
    sweet: float = 0.0      # 甜 / 疗愈
    caffeine: float = 0.0   # 咖啡因 / 提神

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.salty, self.chewy, self.sweet, self.caffeine)

    def norm(self) -> float:
        return math.sqrt(sum(v * v for v in self.as_tuple()))

    def cosine(self, other: "FlavorVector") -> float:
        """余弦相似度，返回 0.0 ~ 1.0。任一向量为零向量时返回 0.0。"""
        n1, n2 = self.norm(), other.norm()
        if n1 == 0.0 or n2 == 0.0:
            return 0.0
        dot = sum(a * b for a, b in zip(self.as_tuple(), other.as_tuple()))
        return max(0.0, min(1.0, dot / (n1 * n2)))

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "FlavorVector":
        if not d:
            return cls()
        return cls(
            salty=float(d.get("salty", 0.0)),
            chewy=float(d.get("chewy", 0.0)),
            sweet=float(d.get("sweet", 0.0)),
            caffeine=float(d.get("caffeine", 0.0)),
        )


# ---------------------------------------------------------------------------
# 商品
# ---------------------------------------------------------------------------


@dataclass
class MenuItem:
    """
    菜单单品。

    TODO(real-mcp): 真实字段以 `query-meals` 返回为准。
    官方返回包含「分类、餐品编码、标签」，热量需另外查 `list-nutrition-foods` 补齐。
    """

    code: str
    name: str
    category: str
    price: float
    tags: list[str] = field(default_factory=list)
    kcal: int = 0
    available: bool = True
    flavor: FlavorVector = field(default_factory=FlavorVector)
    prep_seconds: int = 120  # 估算出餐时长，用于 speed_pref=FAST 场景

    @property
    def kcal_per_yuan(self) -> float:
        """每元热量——性价比场景（吃土）的排序依据"""
        return self.kcal / self.price if self.price > 0 else 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MenuItem":
        return cls(
            code=str(d.get("code") or d.get("mealCode") or ""),
            name=str(d.get("name") or d.get("mealName") or ""),
            category=str(d.get("category") or d.get("categoryName") or "其他"),
            price=float(d.get("price") or d.get("salePrice") or 0.0),
            tags=list(d.get("tags") or []),
            kcal=int(d.get("kcal") or 0),
            available=bool(d.get("available", True)),
            flavor=FlavorVector.from_dict(d.get("flavor")),
            prep_seconds=int(d.get("prep_seconds") or 120),
        )


# ---------------------------------------------------------------------------
# 优惠券
# ---------------------------------------------------------------------------


@dataclass
class Coupon:
    """
    优惠券。不同 kind 用到的字段不同：
      - THRESHOLD  : threshold(门槛) + discount(减免金额)
      - SECOND_HALF: rate(0.5) + item_codes(参与商品)
      - PERCENT    : rate(0.8 = 八折) + threshold(可选门槛)
      - ITEM       : item_codes + discount(立减金额)
      - COMBO      : item_codes(套餐编码) + discount

    真实 MCP 返回的券字段与 Mock 略有差异，from_dict 已兼容两种：
      · 门店券（query-store-coupons）：title / couponId / couponCode / products
      · 可领券（available-coupons）  ：couponName / couponStatus / label
    """

    coupon_id: str
    name: str
    kind: CouponKind
    threshold: float = 0.0
    discount: float = 0.0
    rate: float = 1.0
    item_codes: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    stackable: bool = True
    """能否与其他券同时使用。False = 只能单独使用（大额券常见）。"""

    exclusive_group: str = ""
    """
    互斥组：同一组内最多选一张。官方字段名为 `exclusiveGroup`，
    为空表示不属于任何互斥组。
    """

    expires_at: str = ""
    coupon_code: str = ""
    """
    券的「核销码」（query-store-coupons 返回的 couponCode）。
    与 coupon_id 不同：coupon_id 是系统内主键，couponCode 是给用户展示的券码。
    """
    products: list[str] = field(default_factory=list)
    """适用商品名列表（真实券常带 products[]，便于展示「适用于 XX」"""

    def applies_to(self, item: MenuItem) -> bool:
        """该券是否适用于某个商品（用于 ITEM / SECOND_HALF 券）"""
        if self.item_codes:
            return item.code in self.item_codes
        if self.categories:
            return item.category in self.categories
        return True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Coupon":
        # 真实券的「名称」字段可能是 couponName（可领券）或 title（门店券）
        name = (
            d.get("couponName")
            or d.get("title")
            or d.get("name")
            or "优惠券"
        )
        # 适用商品列表：真实券的 products[] 里是 {productCode, productName}
        products = [
            p.get("productName") or p.get("productCode") or str(p)
            for p in (d.get("products") or [])
            if isinstance(p, (dict, str))
        ]
        return cls(
            coupon_id=str(d.get("couponId") or d.get("coupon_id") or ""),
            name=str(name),
            kind=CouponKind(str(d.get("kind") or d.get("type") or "threshold")),
            threshold=float(d.get("threshold") or 0.0),
            discount=float(d.get("discount") or 0.0),
            rate=float(d.get("rate") or 1.0),
            item_codes=list(d.get("item_codes") or d.get("itemCodes") or []),
            categories=list(d.get("categories") or []),
            stackable=bool(d.get("stackable", True)),
            exclusive_group=str(
                d.get("exclusive_group") or d.get("exclusiveGroup") or ""
            ),
            expires_at=str(d.get("expires_at") or d.get("expireTime") or ""),
            coupon_code=str(d.get("couponCode") or d.get("coupon_code") or ""),
            products=products,
        )


# ---------------------------------------------------------------------------
# 门店
# ---------------------------------------------------------------------------


@dataclass
class Store:
    store_id: str
    name: str
    address: str = ""
    distance_m: int = 0
    open_now: bool = True
    supports_delivery: bool = False
    be_code: str = ""
    """
    业务编码（beCode）。真实 MCP 中，外送（orderType=2）场景的算价 / 下单
    必须把 beCode 与 storeCode 配对使用；到店（orderType=1）场景不传 beCode。
    query-nearby-stores 的响应里通常不含 beCode（需从 delivery-query-stores 取），
    因此这里默认空串，到店流程不受影响。
    """
    reservation: bool = False
    """是否支持预约（真实字段 reservation）"""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Store":
        return cls(
            store_id=str(
                d.get("storeCode") or d.get("storeId") or d.get("store_id") or ""
            ),
            name=str(d.get("storeName") or d.get("name") or ""),
            address=str(d.get("address") or ""),
            distance_m=int(d.get("distance_m") or d.get("distance") or 0),
            # 真实字段 businessStatus 表示是否在营业
            open_now=bool(d.get("open_now", d.get("businessStatus", True))),
            supports_delivery=bool(d.get("supports_delivery", False)),
            be_code=str(d.get("beCode") or d.get("be_code") or ""),
            reservation=bool(d.get("reservation", False)),
        )


# ---------------------------------------------------------------------------
# 情绪
# ---------------------------------------------------------------------------


@dataclass
class MoodProfile:
    """
    一种「打工人精神状态」的完整定义。

    bleed_score（失血值）越高越惨；推荐方案的 RP（续命值）必须 >= 它才算「够用」。
    """

    mood_id: str
    label: str
    bleed_score: int
    flavor: FlavorVector
    kcal_pref: KcalPref = KcalPref.MID
    value_pref: ValuePref = ValuePref.DEAL
    speed_pref: SpeedPref = SpeedPref.ANY
    func_need: str = ""            # 功能诉求：caffeine / chew / sugar / none
    keywords: tuple[str, ...] = ()       # 命中关键词
    sarcasm_markers: tuple[str, ...] = ()  # 反讽标志词
    copy_tone: str = "温暖"          # 文案语气，供 SocialEngine 使用


@dataclass
class MoodMatch:
    """一次情绪识别的结果"""

    profile: MoodProfile
    intensity: float = 1.0        # 强度系数 0.5 ~ 1.5
    bleed_score: int = 0          # = profile.bleed_score * intensity
    confidence: float = 0.0
    sarcasm: bool = False
    hit_keywords: list[str] = field(default_factory=list)
    time_context: str = ""        # late_night / monday_morning / payday / ...
    raw_text: str = ""

    @property
    def label(self) -> str:
        return self.profile.label

    @property
    def mood_id(self) -> str:
        return self.profile.mood_id


@dataclass
class ScoredItem:
    """商品在某个情绪下的打分结果"""

    item: MenuItem
    rp_base: float = 0.0                       # 0 ~ 80（不含优惠暴击）
    parts: dict[str, float] = field(default_factory=dict)  # 各分量得分，用于可解释展示

    @property
    def name(self) -> str:
        return self.item.name


# ---------------------------------------------------------------------------
# 购物车 / 优惠计算
# ---------------------------------------------------------------------------


@dataclass
class CartLine:
    item: MenuItem
    qty: int = 1

    @property
    def subtotal(self) -> float:
        return round(self.item.price * self.qty, 2)


@dataclass
class DealResult:
    """
    最优凑单方案。

    filler 是「凑单品」——为触发满减门槛而额外加购的商品。
    关键卖点：可能出现「多买一个反而更便宜」的情况。
    """

    lines: list[CartLine]
    applied: list[Coupon] = field(default_factory=list)
    filler: MenuItem | None = None
    subtotal: float = 0.0
    discount: float = 0.0
    delivery_fee: float = 0.0
    payable: float = 0.0
    explain: list[str] = field(default_factory=list)
    saving_vs_no_filler: float = 0.0  # 凑单带来的额外收益（负数表示不划算）

    @property
    def discount_rate(self) -> float:
        return self.discount / self.subtotal if self.subtotal > 0 else 0.0

    @property
    def saved(self) -> float:
        """省下的钱 = 优惠减免额（注意不是 subtotal - discount，那是应付）"""
        return round(self.discount, 2)


@dataclass
class PriceResult:
    """calculate-price 的返回。真实场景一律以 MCP 返回为准，本地只做校验"""

    subtotal: float = 0.0
    discount: float = 0.0
    delivery_fee: float = 0.0
    payable: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    """create-order 的返回"""

    order_id: str = ""
    pay_url: str = ""
    pickup_code: str = ""
    status: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 最终推荐方案（main.py 组装，renderer.py 渲染）
# ---------------------------------------------------------------------------


@dataclass
class Recommendation:
    mood: MoodMatch
    plan_name: str                 # 方案名，如「泄愤流」
    lines: list[CartLine]
    deal: DealResult
    rp: float = 0.0                # 最终续命值 0~100
    rp_parts: dict[str, float] = field(default_factory=dict)
    reason: str = ""               # 一句人话理由
    copywriting: str = ""          # 发疯文学 / 文案
    store: Store | None = None
    enough: bool = True            # RP >= 失血值 ?
    over_budget: bool = False
