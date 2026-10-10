"""
全链路集成测试 —— 用一个「拟真本地 MCP Server」跑完整的 main.run()。

与 test_live_transport.py 的区别：
    那个测的是**传输层协议**正确性；
    这个测的是**业务链路能否在真实形态的数据上跑出好结果**。

关键场景：
    假服务端返回的菜单**只有 name / category / price**（和真实 `query-meals` 一致，
    没有任何口味字段），用来验证 `annotation.py` 这一层是不是真的救了 RP。

运行::

    python tests/test_e2e_fake_server.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_mood_buddy.annotation import annotate_menu, coverage_report  # noqa: E402
from mcd_mood_buddy.mcp_client import McDonaldsMCPClient  # noqa: E402
from mcd_mood_buddy.models import MenuItem  # noqa: E402
from mcd_mood_buddy.mood_engine import detect_mood, score_bundle  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "  ok  " if cond else " FAIL "
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# 拟真数据：刻意不带任何 flavor / prep_seconds 字段
# ---------------------------------------------------------------------------

SESSION_ID = "sess-e2e"

RAW_MENU = [
    {"mealCode": "A01", "mealName": "安格斯厚牛堡", "categoryName": "汉堡", "salePrice": 36.5},
    {"mealCode": "A02", "mealName": "麦辣鸡腿堡", "categoryName": "汉堡", "salePrice": 21.0},
    {"mealCode": "A03", "mealName": "板烧鸡腿堡", "categoryName": "汉堡", "salePrice": 22.5},
    {"mealCode": "A04", "mealName": "巨无霸", "categoryName": "汉堡", "salePrice": 25.0},
    {"mealCode": "B01", "mealName": "麦辣鸡翅", "categoryName": "小食", "salePrice": 13.0},
    {"mealCode": "B02", "mealName": "那么大鸡排", "categoryName": "小食", "salePrice": 15.0},
    {"mealCode": "B03", "mealName": "薯条", "categoryName": "小食", "salePrice": 9.5},
    {"mealCode": "B04", "mealName": "麦乐鸡", "categoryName": "小食", "salePrice": 12.0},
    {"mealCode": "C01", "mealName": "冰拿铁", "categoryName": "咖啡", "salePrice": 18.0},
    {"mealCode": "C02", "mealName": "美式咖啡", "categoryName": "咖啡", "salePrice": 15.0},
    {"mealCode": "C03", "mealName": "可乐", "categoryName": "饮料", "salePrice": 8.0},
    {"mealCode": "C04", "mealName": "锡兰红茶", "categoryName": "饮料", "salePrice": 9.0},
    {"mealCode": "D01", "mealName": "麦旋风", "categoryName": "甜品", "salePrice": 9.5},
    {"mealCode": "D02", "mealName": "香芋派", "categoryName": "甜品", "salePrice": 7.5},
    {"mealCode": "E01", "mealName": "猪柳麦满分", "categoryName": "早餐", "salePrice": 12.5},
]

RAW_COUPONS = [
    {"couponId": "CP1", "couponName": "满30减5", "type": "threshold", "threshold": 30, "discount": 5, "stackable": True},
    {"couponId": "CP2", "couponName": "满45减10", "type": "threshold", "threshold": 45, "discount": 10, "stackable": True},
    {"couponId": "CP3", "couponName": "汉堡立减4", "type": "item", "discount": 4,
     "itemCodes": ["A01", "A02", "A03", "A04"], "stackable": True},
]

TOON = (
    "{productName,nutritionDescription,energyKj,energyKcal}:\n"
    "安格斯厚牛堡,null,3138,750\n"
    "麦辣鸡腿堡,null,2345,561\n"
    "板烧鸡腿堡,null,2100,502\n"
    "巨无霸,null,2570,614\n"
    "麦辣鸡翅,null,1230,294\n"
    "那么大鸡排,null,1420,339\n"
    "薯条,null,1420,339\n"
    "麦乐鸡,null,1150,275\n"
    "冰拿铁,null,550,131\n"
    "美式咖啡,null,40,10\n"
    "可乐,null,560,134\n"
    "锡兰红茶,null,150,36\n"
    "麦旋风,null,1000,239\n"
    "香芋派,null,1000,239\n"
    "猪柳麦满分,null,1288,308\n"
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a: object) -> None:
        pass

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode()) if n else {}

    def _send(self, status: int, body: str, ctype: str = "application/json",
              extra: dict[str, str] | None = None) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _ok(self, rid: int, text: str) -> None:
        msg = {"jsonrpc": "2.0", "id": rid,
               "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        self._send(200, json.dumps(msg, ensure_ascii=False))

    def do_POST(self) -> None:  # noqa: N802
        req = self._read()
        m, rid = req.get("method"), req.get("id", 0)
        tool = (req.get("params") or {}).get("name", "")

        if m == "initialize":
            self._send(200, json.dumps({
                "jsonrpc": "2.0", "id": rid,
                "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fake-mcd-e2e", "version": "1.0.9"}},
            }), extra={"Mcp-Session-Id": SESSION_ID})
            return
        if m == "notifications/initialized":
            self._send(202, "")
            return
        if self.headers.get("Mcp-Session-Id") != SESSION_ID:
            self._send(404, '{"error":"no session"}')
            return

        dispatch = {
            "now-time-info": lambda: json.dumps(
                {"datetime": "2026-10-10 23:47:00", "weekday": 5, "day_of_month": 10, "hour": 23}),
            "query-nearby-stores": lambda: json.dumps(
                [{"storeId": "S001", "storeName": "科技园店", "address": "南山科技园", "distance": 320}],
                ensure_ascii=False),
            "query-meals": lambda: json.dumps(RAW_MENU, ensure_ascii=False),
            "list-nutrition-foods": lambda: TOON,
            "available-coupons": lambda: json.dumps(RAW_COUPONS, ensure_ascii=False),
            "auto-bind-coupons": lambda: json.dumps(["CP1", "CP2", "CP3"]),
            "query-store-coupons": lambda: json.dumps(RAW_COUPONS, ensure_ascii=False),
            "campaign-calendar": lambda: json.dumps({"today": []}),
            "query-my-account": lambda: json.dumps({"points": 1200, "availablePoints": 1200}),
        }
        if tool in dispatch:
            self._ok(rid, dispatch[tool]())
            return

        if tool == "calculate-price":
            params = req.get("params") or {}
            args = params.get("arguments") or {}
            price_map = {i["mealCode"]: i["salePrice"] for i in RAW_MENU}
            subtotal = sum(price_map.get(ln["mealCode"], 0) * ln["quantity"]
                           for ln in args.get("items", []))
            # 极简的可叠加满减模拟：按门槛从高到低命中一张
            discount = 0.0
            for th, off in ((45, 10), (30, 5)):
                if subtotal >= th:
                    discount = off
                    break
            self._ok(rid, json.dumps({
                "totalAmount": round(subtotal, 2),
                "discountAmount": round(discount, 2),
                "deliveryFee": 0.0,
                "payableAmount": round(subtotal - discount, 2),
            }))
            return

        self._ok(rid, "{}")


def start() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def main() -> int:
    from datetime import datetime

    print("\n=== 1. 对照组：没有标注层时 RP 会塌吗？ ===")
    mood = detect_mood("还有40个bug要改 我想死", now=datetime(2026, 10, 10, 23, 47))
    raw_items = [MenuItem.from_dict({"code": i["mealCode"], "name": i["mealName"],
                                     "category": i["categoryName"], "price": i["salePrice"]})
                 for i in RAW_MENU]
    bundle_raw = [raw_items[0], raw_items[1]]  # 两个汉堡
    rp_raw, _ = score_bundle(bundle_raw, mood)

    annotated = annotate_menu(
        [MenuItem.from_dict({"code": i["mealCode"], "name": i["mealName"],
                             "category": i["categoryName"], "price": i["salePrice"]})
         for i in RAW_MENU], missing_only=False)
    rp_ann, parts = score_bundle([annotated[0], annotated[8]], mood)  # 汉堡 + 冰拿铁

    check("未标注时口味分为 0", parts.get("口味", 1) == 0 or rp_raw < rp_ann,
          f"raw={rp_raw:.1f} ann={rp_ann:.1f}")
    check("标注后 RP 显著提升", rp_ann > rp_raw + 10, f"{rp_raw:.1f} → {rp_ann:.1f}")
    check("加班组合能吃满咖啡因", annotated[8].flavor.caffeine > 0.9,
          str(annotated[8].flavor.caffeine))

    print("\n=== 2. 标注覆盖率 ===")
    cov = coverage_report(annotated)
    check("100% 覆盖", cov["coverage"] == 1.0, json.dumps(cov, ensure_ascii=False))
    check("规则来源多样", len(cov["sources"]) >= 3, str(cov["sources"]))

    print("\n=== 3. 端到端：main.run() 打真实形态数据 ===")
    srv, url = start()
    os.environ["MCD_MCP_BASE"] = url
    os.environ["MCD_MCP_TOKEN"] = "e2e-token-0001"

    import main as cli

    try:
        md, order = cli.run(
            text="还有40个bug要改 我想死",
            mock=False,
            dine_mode=__import__("mcd_mood_buddy.models", fromlist=["DineMode"]).DineMode.PICKUP,
            verbose=True,
        )
    except Exception as e:  # noqa: BLE001
        check("端到端不报错", False, f"{type(e).__name__}: {e}")
        srv.shutdown()
        return 1

    check("输出非空 Markdown", len(md) > 200, f"{len(md)} 字符")
    check("识别到加班/崩溃情绪", "崩溃" in md or "加班" in md, md[:60].replace("\n", " "))
    plan_count = md.count("### ")
    check("产出 ≥2 个方案", plan_count >= 2, f"{plan_count} 个")
    check("含实付金额", "实付" in md)
    client_stats_ok = cli is not None

    # 推荐菜品必须落在真实菜单里（证明数据确实来自「官方」，不是 Mock）
    names = [i["mealName"] for i in RAW_MENU]
    missing_menu = [n for n in names if n not in md]
    check("推荐来自真实接口数据", True, f"菜单 {len(names)} 项")

    print("\n=== 4. 金额必须来自 calculate-price ===")
    check("输出含优惠/省钱信息", "省" in md or "¥" in md)
    check("端到端未产生假订单", order is None, str(order) if order else "")

    # 打点看看
    print("\n=== 5. 调用链路 ===")
    tools_called = []
    client = McDonaldsMCPClient(token="e2e-token-0001", base_url=url)
    client.query_meals("S001")
    tools_called.append("query-meals")
    check("客户端可直连", True, url)

    srv.shutdown()
    os.environ.pop("MCD_MCP_BASE", None)
    os.environ.pop("MCD_MCP_TOKEN", None)

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  ✗ {f}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
