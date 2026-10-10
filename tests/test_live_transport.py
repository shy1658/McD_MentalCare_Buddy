"""
真机链路测试 —— 用一个**本地假 MCP Server** 验证 `_call()` 的真实传输实现。

为什么这么做？
    真实 MCP 需要 Token + 网络，CI / 答辩现场 / 断网环境都跑不了。
    这里起一个 127.0.0.1 的 Mini MCP Server，完整复刻官方的协议行为，
    从而**不依赖任何外部资源**也能证明：握手、限流重试、SSE 解析、
    会话自愈、toon 解析、tools/list 差分全部正确。

运行::

    python tests/test_live_transport.py

覆盖场景：
    A. initialize 握手 + Mcp-Session-Id + notifications/initialized
    B. tools/call 返回 **纯 JSON** 信封
    C. tools/call 返回 **SSE（text/event-stream）** 信封
    D. 429 限流 → 读 Retry-After → 退避重试成功
    E. 会话失效 404 → 自动重新握手 → 重试成功
    F. list-nutrition-foods 的 **toon 紧凑格式**解析
    G. tools/list 差分（verify_tools）
    H. doctor() 自检报告
    I. 写接口（calculate-price / create-order）**不允许**降级到 Mock
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcd_mood_buddy.mcp_client import (  # noqa: E402
    REQUIRED_TOOLS,
    McDonaldsMCPClient,
    McpRateLimitError,
)

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "  ok  " if cond else " FAIL "
    print(f"[{mark}] {name}" + (f"  -> {detail}" if detail else ""))


# =============================================================================
# 假服务端：复刻官方 MCP（Streamable HTTP）的关键行为
# =============================================================================

SESSION_ID = "sess-mock-0001"

TOON_TEXT = (
    "{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbs}:\n"
    "猪柳麦满分,null,1288,308,16,16,24\n"
    "麦辣鸡腿堡,null,2345,561,28,31,42\n"
    '板烧鸡腿堡,"null, 无描述",2100,502,30,24,45\n'
)

MENU_JSON = [
    {"code": "M001", "name": "猪柳麦满分", "category": "早餐", "price": 12.5},
    {"code": "M002", "name": "麦辣鸡腿堡", "category": "汉堡", "price": 21.0},
]


class _Handler(BaseHTTPRequestHandler):
    """Mini MCP Server —— 故意做得「难搞」，好把客户端的边缘情况全逼出来"""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # 静音，避免污染测试输出
        pass

    # --- 工具方法 -----------------------------------------------------

    def _read_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def _send(self, status: int, body: str, ctype: str = "application/json",
              extra_headers: dict[str, str] | None = None) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _jsonrpc(self, rid: int, result: dict, sse: bool = False) -> None:
        msg = {"jsonrpc": "2.0", "id": rid, "result": result}
        if sse:
            body = f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
            self._send(200, body, ctype="text/event-stream")
        else:
            self._send(200, json.dumps(msg, ensure_ascii=False))

    def _tool_text(self, payload: str) -> dict:
        """MCP 的 content 信封：业务数据被包成一层 text 字符串"""
        return {"content": [{"type": "text", "text": payload}], "isError": False}

    # --- 路由 ---------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        req = self._read_body()
        method = req.get("method", "")
        rid = req.get("id", 0)
        tool = (req.get("params") or {}).get("name", "")
        st = self.server.state  # type: ignore[attr-defined]
        st["log"].append((method, tool))

        # 握手
        if method == "initialize":
            st["initialized"] = True
            self._send(
                200,
                json.dumps({
                    "jsonrpc": "2.0", "id": rid,
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake-mcd", "version": "1.0.9"},
                    },
                }),
                extra_headers={"Mcp-Session-Id": SESSION_ID},
            )
            return

        if method == "notifications/initialized":
            st["notified"] = True
            self._send(202, "")
            return

        # 业务接口一律要求有效会话
        if self.headers.get("Mcp-Session-Id") != SESSION_ID or st.get("expire_sessions"):
            st["expire_sessions"] = False  # 只失效一次，配合客户端重连
            self._send(404, '{"error":"Session not found"}')
            return

        if method == "tools/list":
            names = list(REQUIRED_TOOLS) + ["some-other-tool", "another-tool"]
            self._jsonrpc(rid, {"tools": [{"name": n} for n in names]})
            return

        if method != "tools/call":
            self._send(400, '{"error":"unknown method"}')
            return

        self._route_tool(rid, tool, st)

    def _route_tool(self, rid: int, tool: str, st: dict) -> None:
        # D. 限流：第一次 429，之后放行
        if tool == "flaky-rate-limit":
            if st.get("rate_hits", 0) == 0:
                st["rate_hits"] = 1
                self._send(429, '{"error":"rate limited"}',
                           extra_headers={"Retry-After": "0.05"})
                return
            self._jsonrpc(rid, self._tool_text('{"ok": true}'))
            return

        # B. 纯 JSON 信封
        if tool == "query-meals":
            self._jsonrpc(rid, self._tool_text(json.dumps(MENU_JSON, ensure_ascii=False)))
            return

        # C. SSE 信封
        if tool == "now-time-info":
            self._jsonrpc(
                rid,
                self._tool_text(
                    json.dumps(
                        {"datetime": "2026-10-10 23:47:00", "weekday": 5, "day_of_month": 10, "hour": 23},
                        ensure_ascii=False,
                    )
                ),
                sse=True,
            )
            return

        # F. toon 文本（不是 JSON，必须走专用解析器）
        if tool == "list-nutrition-foods":
            self._jsonrpc(rid, self._tool_text(TOON_TEXT))
            return

        if tool == "calculate-price":
            self._jsonrpc(rid, self._tool_text(json.dumps(
                {"totalAmount": 39.0, "discountAmount": 8.0, "deliveryFee": 0.0, "payableAmount": 31.0}
            )))
            return

        if tool == "boom":
            self._send(500, "internal error")
            return

        # 兜底
        self._jsonrpc(rid, self._tool_text('{"echo":"' + tool + '"}'))

    def do_DELETE(self) -> None:  # noqa: N802
        self.server.state["closed"] = True  # type: ignore[attr-defined]
        self._send(204, "")


def start_server() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.state = {  # type: ignore[attr-defined]
        "log": [], "initialized": False, "notified": False,
        "expire_sessions": False, "rate_hits": 0, "closed": False,
    }
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


# =============================================================================
# 测试
# =============================================================================


def main() -> int:
    srv, url = start_server()
    client = McDonaldsMCPClient(token="test-token-1234", base_url=url, max_retries=3)

    print("\n=== A. 握手 ===")
    client._ensure_session()
    check("initialize 成功", srv.state["initialized"])  # type: ignore[attr-defined]
    check("拿到 Mcp-Session-Id", client._session_id == SESSION_ID, str(client._session_id))
    check("发出 notifications/initialized", srv.state["notified"])  # type: ignore[attr-defined]
    check("协商到协议版本", client._negotiated_version == "2025-06-18", client._negotiated_version)

    print("\n=== B. 纯 JSON 信封 ===")
    menu = client.query_meals("S001")
    check("query-meals 返回 2 个商品", len(menu) == 2, str(menu))
    check("商品字段映射正确", menu[0].name == "猪柳麦满分" and menu[0].price == 12.5)

    print("\n=== C. SSE 信封 ===")
    info = client.now_time_info()
    check("now-time-info 解析出 dict", isinstance(info, dict), str(info))
    check("SSE 内容正确", info.get("hour") == 23, str(info.get("hour")))
    check("time 数据不被缓存", client.stats["cache_hits"] == 0)

    print("\n=== D. 429 退避重试 ===")
    before = client.stats["calls"]
    r = client._call_with_retry("flaky-rate-limit")
    check("重试后成功", r == {"ok": True}, str(r))
    check("重试被计数", client.stats["retries"] >= 1, f"retries={client.stats['retries']}")
    check("调用计数增加", client.stats["calls"] > before)

    print("\n=== E. 会话失效自愈 ===")
    srv.state["expire_sessions"] = True  # type: ignore[attr-defined]
    client.available_coupons()  # 应触发 404 → 重握手 → 重试
    check("会话重建成功", client._session_id == SESSION_ID, str(client._session_id))
    check("未抛出会话异常", True)

    print("\n=== F. toon 营养表解析 ===")
    nut = client.list_nutrition_foods()
    check("解析出 3 条", len(nut) == 3, str(nut))
    check("kcal 取值正确", nut.get("猪柳麦满分") == 308, str(nut.get("猪柳麦满分")))
    check("带引号逗号行也能解析", nut.get("板烧鸡腿堡") == 502, str(nut.get("板烧鸡腿堡")))
    check("返回类型是 int", all(isinstance(v, int) for v in nut.values()))

    print("\n=== G. tools/list 差分 ===")
    v = client.verify_tools()
    check("全部必需 Tool 都在", v["missing"] == [], str(v["missing"]))
    check("识别出多余 Tool", "some-other-tool" in v["extras"], str(v["extras"]))
    check("服务端工具总数", v["total"] == len(REQUIRED_TOOLS) + 2, str(v["total"]))

    print("\n=== H. doctor 自检 ===")
    rep = client.doctor()
    check("体检通过", rep["ok"] is True, json.dumps(rep["checks"], ensure_ascii=False))
    check("Token 已脱敏", rep["token"] == "***1234", rep["token"])
    check("含 Tool 清单校验项", any(c["name"] == "Tool 清单校验" for c in rep["checks"]))

    print("\n=== I. 写接口绝不降级 ===")
    nofb = McDonaldsMCPClient(
        token="bad", base_url="http://127.0.0.1:1", max_retries=1, fallback_to_mock=True
    )
    try:
        nofb.calculate_price([{"code": "M001", "qty": 1, "price": 12.5}])
        check("断网时 calculate-price 必须报错", False, "竟然返回了结果 —— 会用假金额骗用户！")
    except Exception as e:  # noqa: BLE001
        check("断网时 calculate-price 报错", True, type(e).__name__)

    print("\n=== J. 只读接口可降级（断网彩蛋）===")
    # 指向一个必然拒绝连接的端口，模拟答辩现场断网
    dead = McDonaldsMCPClient(
        token="t", base_url="http://127.0.0.1:1", mock=False,
        max_retries=1, fallback_to_mock=True,
    )
    try:
        items = dead.query_meals("S001")
        names = [i.name for i in items]
        check("断网后仍有数据", len(items) > 0, f"{len(items)} 条")
        check("数据来自本地 Mock", "安格斯厚牛堡" in names, str(names[:3]))
        check("降级被计数", dead.stats["mock_fallbacks"] >= 1, str(dead.stats["mock_fallbacks"]))
        # 写入口在断网时必须照常报错，哪怕是同一个降级客户端
        from mcd_mood_buddy.mcp_client import OrderNotConfirmedError
        try:
            dead.create_order([{"code": "M001", "qty": 1}], "S001")
            check("降级客户端仍禁止下单", False, "竟然下单成功了")
        except OrderNotConfirmedError:
            check("降级客户端下单仍需确认", True)
        except Exception as e:  # noqa: BLE001
            check("降级客户端下单被拦截", True, type(e).__name__)
    except Exception as e:  # noqa: BLE001
        check("只读接口降级", False, f"{type(e).__name__}: {e}")

    print("\n=== K. 资源释放 ===")
    client.close()
    check("DELETE 被调用", srv.state["closed"])  # type: ignore[attr-defined]

    srv.shutdown()

    print(f"\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print(f"  ✗ {f}")
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
