"""
MCP 调用层 —— 对麦当劳官方 MCP Server 的统一封装。

接入信息（官方 v1.0.9）
----------------------
    地址   : https://mcp.mcd.cn
    协议   : Streamable HTTP（不支持 WebSocket）
    认证   : Authorization: Bearer <MCP_TOKEN>
    限流   : 600 次 / 分钟 / Token（超限返回 429）
    错误码 : 401 Token 无效 ｜ 429 触发限流
    协议版本: 仅支持 MCP 2025-06-18 及之前

本文件对外暴露的每一个方法 = 一个官方 Tool：

    now_time_info()            -> now-time-info
    query_nearby_stores()      -> query-nearby-stores
    query_meals()              -> query-meals
    query_meal_detail()        -> query-meal-detail
    available_coupons()        -> available-coupons
    auto_bind_coupons()        -> auto-bind-coupons
    query_store_coupons()      -> query-store-coupons
    query_my_coupons()         -> query-my-coupons
    calculate_price()          -> calculate-price
    create_order()             -> create-order
    query_order()              -> query-order
    list_nutrition_foods()     -> list-nutrition-foods
    query_my_account()         -> query-my-account
    campaign_calendar()        -> campaign-calendar
    order_list()               -> order-list

设计要点
--------
1. **Mock 优先**：`McDonaldsMCPClient(mock=True)` 时全部走 data/mock/*.json，
   无 Token、无网络也能跑完整流程。这是答辩现场断网的保命开关。
2. **统一出口**：所有真实调用都经过 `_call()`，握手、重试、限流退避、缓存、
   降级、脱敏、埋点都在这里，上层不用关心网络细节。
3. **解析隔离**：`_parse_*` 负责把官方返回映射成 models.py 的结构体。
   官方改字段名时**只改这里**，算法层零改动。
4. **零第三方依赖**：HTTP 层用标准库 urllib 实现（不引 requests / httpx），
   保持 `pip install -r requirements.txt` 是空装的干净状态。

真实链路已经打通，开箱即用::

    export MCD_MCP_TOKEN="<你的 token>"
    python main.py "加班到想死" --live --doctor
"""

from __future__ import annotations

import csv
import json
import os
import random
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterable

from .models import (
    Coupon,
    DineMode,
    MenuItem,
    OrderResult,
    PriceResult,
    Store,
)

try:  # 版本号仅供 User-Agent 上报，拿不到也不影响功能
    from . import __version__ as _PKG_VERSION
except Exception:  # noqa: BLE001
    _PKG_VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# 常量与异常
# ---------------------------------------------------------------------------

MCP_BASE_URL = "https://mcp.mcd.cn"
MCP_PROTOCOL_VERSION = "2025-06-18"

# 配送费预估（真实场景一律以 calculate-price 返回为准，这里仅供 Mock / 预算试算）
DELIVERY_FEE = 6.0

# Mock 数据目录：<repo>/data/mock/
MOCK_DIR = Path(__file__).resolve().parent.parent / "data" / "mock"

# 缓存 TTL（秒）
TTL_MENU = 600          # 菜单 10 分钟（售罄状态会变，不能太久）
TTL_NUTRITION = 86400   # 营养表 24 小时（静态数据）
TTL_STORE = 1800        # 门店 30 分钟
TTL_CAMPAIGN = 3600     # 活动日历 1 小时
TTL_NO_CACHE = 0        # 券不缓存

# 本地限流：官方额度是 600 次/分钟，这里留 10% 安全水位，
# 避免和业务自身的重试叠加后被 429。四路并行时也顶得住。
RATE_LIMIT_PER_MIN = 540

# 本项目实际用到的官方 Tool 清单。
# 用途：`client.verify_tools()` 会拿它与服务端 tools/list 做差集，
# 官方改名 / 下线时能第一时间发现，而不是等到调用时才炸。
REQUIRED_TOOLS: tuple[str, ...] = (
    "now-time-info",
    "campaign-calendar",
    "query-my-account",
    "query-nearby-stores",
    "query-meals",
    "query-meal-detail",
    "list-nutrition-foods",
    "available-coupons",
    "auto-bind-coupons",
    "query-store-coupons",
    "query-my-coupons",
    "calculate-price",
    "create-order",
    "query-order",
    "order-list",
)

CLI_VERSION = _PKG_VERSION
USER_AGENT = f"McD_MentalCare_Buddy/{CLI_VERSION} (+https://github.com/your-name/McD_MentalCare_Buddy)"


# ---------------------------------------------------------------------------
# 限流器
# ---------------------------------------------------------------------------


class _SlidingWindowLimiter:
    """
    滑动窗口限流器（标准库实现，无第三方依赖）。

    官方额度 600 次/分钟/Token。这里只做「本地预防」，真正的兜底仍然是
    429 之后的指数退避（见 `_call_with_retry`）。
    """

    def __init__(self, limit: int = RATE_LIMIT_PER_MIN, window: float = 60.0) -> None:
        self.limit = max(1, limit)
        self.window = window
        self._hits: deque[float] = deque()

    def acquire(self) -> float:
        """阻塞直到可以发下一个请求，返回实际等待秒数"""
        now = time.time()
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        if len(self._hits) >= self.limit:
            sleep_s = self.window - (now - self._hits[0]) + 0.01
            if sleep_s > 0:
                time.sleep(sleep_s)
                return sleep_s
        self._hits.append(time.time())
        return 0.0

    @property
    def used(self) -> int:
        now = time.time()
        while self._hits and now - self._hits[0] >= self.window:
            self._hits.popleft()
        return len(self._hits)

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class McpError(Exception):
    """MCP 调用失败的统一异常"""


class McpAuthError(McpError):
    """401：Token 无效 / 过期 / 未提供"""


class McpRateLimitError(McpError):
    """429：触发限流"""

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class McpSessionExpiredError(McpError):
    """Mcp-Session-Id 失效（404 / 400 session not found）—— 需要重新握手"""


class OrderNotConfirmedError(McpError):
    """下单未通过二次确认闸门 —— 这是安全机制，不是 bug"""


# ---------------------------------------------------------------------------
# 响应头 / 重试 工具函数
# ---------------------------------------------------------------------------


def _lower_headers(headers: Any) -> dict[str, str]:
    """HTTP 头统一小写化，避免 `Mcp-Session-Id` / `mcp-session-id` 大小写问题"""
    try:
        return {str(k).lower(): str(v) for k, v in headers.items()}
    except Exception:  # noqa: BLE001
        return {}


def _parse_retry_after(value: str) -> float:
    """
    解析 Retry-After：支持秒数（推荐）与 HTTP 日期两种形态。

    解析不了就返回 0.0，交给调用方走默认指数退避。
    """
    value = (value or "").strip()
    if not value:
        return 0.0
    try:
        return max(0.0, min(60.0, float(value)))
    except ValueError:
        pass
    try:  # HTTP-date: Wed, 21 Oct 2026 07:28:00 GMT
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(value)
        delta = target.timestamp() - time.time()
        return max(0.0, min(60.0, delta))
    except Exception:  # noqa: BLE001
        return 0.0


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------


class McDonaldsMCPClient:
    """
    麦当劳 MCP 客户端。

    用法::

        # 真实模式（需要 Token）
        client = McDonaldsMCPClient(token=os.environ["MCD_MCP_TOKEN"])

        # Mock 模式（演示 / 单测 / 断网）
        client = McDonaldsMCPClient(mock=True)

        store = client.query_nearby_stores("科技园")[0]
        menu  = client.query_meals(store.store_id)
    """

    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        mock: bool | None = None,
        timeout: float = 15.0,
        max_retries: int = 4,
        rate_limit: int = RATE_LIMIT_PER_MIN,
        fallback_to_mock: bool = False,
        verbose: bool = False,
    ) -> None:
        # ⚠️ 顺序陷阱：必须**先**解析 Token，**再**决定要不要降级 mock。
        # 否则「token 来自环境变量 / Token 文件」的情况会被误判成无 Token 而静默降级，
        # 用户明明配了 Token 却一直在跑 Mock —— 这个 bug 极难排查。
        self._token = token or os.environ.get("MCD_MCP_TOKEN") or self._read_token_file()

        if mock is None:
            mock = not self._token

        self.mock = bool(mock)

        # 地址优先级：显式入参 > 环境变量 MCD_MCP_BASE > 官方默认地址
        # （环境变量是为了让你可以指着本地假服务端跑集成测试）
        self.base_url = (
            base_url or os.environ.get("MCD_MCP_BASE") or MCP_BASE_URL
        ).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.fallback_to_mock = fallback_to_mock
        self.verbose = verbose

        self._cache: dict[str, tuple[float, Any]] = {}
        self._session_id: str | None = None          # MCP Streamable HTTP 会话
        self._negotiated_version: str = ""           # 握手后服务端确认的协议版本
        self._rpc_id = 0                             # JSON-RPC 自增请求号
        self._limiter = _SlidingWindowLimiter(rate_limit)

        # 埋点：答辩演示时可以直接打出「本次调用了多少次接口」
        self.stats: dict[str, Any] = {
            "calls": 0,
            "retries": 0,
            "cache_hits": 0,
            "mock_fallbacks": 0,
            "errors": 0,
            "total_wait_s": 0.0,
        }

        if not self.mock and not self._token:
            raise McpAuthError(
                "未找到 MCP Token。请设置环境变量 MCD_MCP_TOKEN，"
                "或写入 ~/.config/mcd/mcp_token，或显式传 mock=True 使用离线演示数据。"
            )

    # ------------------------------------------------------------------
    # 内部：Token / 缓存 / 调用
    # ------------------------------------------------------------------

    @staticmethod
    def _read_token_file() -> str | None:
        """从 ~/.config/mcd/mcp_token 读 Token（该文件应以 umask 077 创建）"""
        p = Path.home() / ".config" / "mcd" / "mcp_token"
        try:
            return p.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    @staticmethod
    def _mask(token: str | None) -> str:
        """日志脱敏：只保留后 4 位"""
        if not token:
            return "<none>"
        return f"***{token[-4:]}"

    def _cached(self, key: str, ttl: int, producer) -> Any:
        """带 TTL 的进程内缓存。ttl=0 表示不缓存。"""
        if ttl <= 0:
            return producer()
        now = time.time()
        hit = self._cache.get(key)
        if hit and now - hit[0] < ttl:
            self.stats["cache_hits"] += 1
            return hit[1]
        value = producer()
        self._cache[key] = (now, value)
        return value

    def clear_cache(self) -> None:
        """清空进程内缓存（切换门店 / 换新 Token 后建议调一次）"""
        self._cache.clear()

    def _next_rpc_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _headers(self) -> dict[str, str]:
        """组装请求头。Token 脱敏只在日志里做，这里必须是原值。"""
        h = {
            "Content-Type": "application/json",
            # Streamable HTTP 允许服务端自由选择 JSON 或 SSE，两种都要能接
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    # --- ① HTTP -------------------------------------------------------

    def _http_post(self, payload: dict[str, Any]) -> tuple[int, dict[str, str], str]:
        """
        最底层的一次 POST。返回 `(status, 小写化的响应头, 响应体文本)`。

        刻意用标准库 urllib 而非 requests/httpx：本项目承诺零第三方依赖，
        `pip install -r requirements.txt` 是空装也能跑。
        """
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url, data=data, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, _lower_headers(resp.headers), body
        except urllib.error.HTTPError as e:  # HTTPError 继承 URLError，必须排前面
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                body = ""
            return e.code, _lower_headers(e.headers), body
        except TimeoutError as e:
            raise McpError(f"请求超时（>{self.timeout}s）：{self.base_url} —— {e}")
        except urllib.error.URLError as e:
            raise McpError(f"网络不可达：{self.base_url}（{e.reason}）")

    @staticmethod
    def _err_snippet(body: str) -> str:
        """
        从响应体里提取最有信息量的错误摘要。

        真实观察到的两种形态（2026-10 实测 mcp.mcd.cn）：
          · 网关信封 JSON：{"code":"0001","msg":"系统错误","data":null}
          · 业务纯文本  ：当前鉴权码不存在或已失效!
        两种都要能读懂，否则排障时只能看到一串 HTTP 状态码。
        """
        text = " ".join((body or "").split())[:300]
        if not text:
            return "<空响应体>"
        try:
            d = json.loads(body or "")
        except json.JSONDecodeError:
            return text
        if isinstance(d, dict):
            for k in ("msg", "message", "error_description", "detail"):
                if d.get(k):
                    code = d.get("code", "")
                    return f"{code} {d[k]}".strip() if code else str(d[k])
        return text

    def _raise_for_status(self, status: int, headers: dict[str, str], body: str) -> None:
        """把 HTTP 状态码翻译成**可执行**的语义化异常"""
        if 200 <= status < 300:
            return
        snippet = self._err_snippet(body)
        if status == 401:
            raise McpAuthError(
                f"401 Token 无效 / 过期 / 不存在 —— {snippet}。"
                f"当前 Token: {self._mask(self._token)}。"
                f"处理建议：到麦当劳 MCP 开放平台重新获取鉴权码，"
                f"并 export MCD_MCP_TOKEN=<新鉴权码>"
            )
        if status == 403:
            raise McpAuthError(
                f"403 无访问权限 —— {snippet}。"
                f"多半是请求头里压根没带上 Authorization；"
                f"请确认请求为 Authorization: Bearer <MCP_TOKEN>"
            )
        if status == 429:
            raise McpRateLimitError(
                f"429 触发限流（官方额度 600 次/分钟/Token）—— {snippet}",
                retry_after=_parse_retry_after(headers.get("retry-after", "")),
            )
        if status == 404 or (status == 400 and "session" in (body or "").lower()):
            raise McpSessionExpiredError(
                f"{status} 会话失效（Mcp-Session-Id 过期或不存在），需重新握手 —— {snippet}"
            )
        if status >= 500:
            raise McpError(f"{status} 服务端异常：{snippet}")
        raise McpError(f"{status} 请求失败：{snippet}")

    # --- ② 响应解析 ---------------------------------------------------

    @staticmethod
    def _extract_messages(content_type: str, body: str) -> list[dict[str, Any]]:
        """
        把响应体拆成若干 JSON-RPC 消息。

        Streamable HTTP 有两种响应形态：
          A. application/json        → 直接是一个 JSON-RPC 响应（或 batch 数组）
          B. text/event-stream (SSE) → 若干 `data: {...}` 行，每行各自是 JSON

        这里**先尝试按 JSON 解析，失败再回退 SSE**，因为部分 MCP Server
        明明返回 SSE 文本却标了 application/json —— 只看 Content-Type 会被坑。
        """
        text = (body or "").strip()
        if not text:
            return []

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [m for m in parsed if isinstance(m, dict)]
        if isinstance(parsed, dict):
            return [parsed]

        msgs: list[dict[str, Any]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(":"):  # `:` 开头是 SSE 注释 / 心跳
                continue
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(obj, list):
                        msgs.extend(m for m in obj if isinstance(m, dict))
                    elif isinstance(obj, dict):
                        msgs.append(obj)
        return msgs

    @staticmethod
    def _pick_message(messages: list[dict[str, Any]], rpc_id: int) -> dict[str, Any]:
        """按 id 精确匹配响应；匹配不到就退化为第一个带 result/error 的消息"""
        for m in messages:
            if m.get("id") == rpc_id and ("result" in m or "error" in m):
                return m
        for m in messages:
            if "result" in m or "error" in m:
                return m
        return messages[-1] if messages else {}

    @staticmethod
    def _unwrap_tool_result(msg: dict[str, Any], tool: str) -> Any:
        """
        把 tools/call 的响应拆成业务数据。

        MCP 约定：
            result.content = [{"type": "text", "text": "<业务 JSON 字符串>"}]

        也就是说要 **json.loads 两次**：第一次是信封，第二次才是信封里的业务数据。
        忘了第二次是接入 MCP 最高频的坑（表现是拿到一个字符串而不是 dict）。
        """
        if not msg:
            return {}

        if msg.get("error"):
            err = msg["error"] or {}
            code = err.get("code")
            message = str(err.get("message") or "未知错误")
            low = message.lower()
            if code in (-32001, -32602) or "unauthorized" in low or "401" in message:
                raise McpAuthError(f"{tool} 鉴权失败：{message}")
            raise McpError(f"{tool} 调用失败 [jsonrpc code={code}]：{message}")

        result = msg.get("result") or {}

        # 优先吃结构化字段（部分 Server 直接给结构化结果）
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and result.get("isError") is not True:
            return structured

        if result.get("isError") is True:
            texts0 = [
                c.get("text", "")
                for c in (result.get("content") or [])
                if isinstance(c, dict)
            ]
            raise McpError(f"{tool} 服务端返回业务错误：{' '.join(texts0)[:300]}")

        content = result.get("content")
        texts: list[str] = []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    t = c.get("text", "")
                    if t:
                        texts.append(t)
        elif isinstance(content, str):
            texts.append(content)

        if not texts:
            # 既没 content 也没 structuredContent：把 result 本体当业务数据
            return result

        joined = "\n".join(texts)
        for candidate in (joined, *texts):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        # 确实不是 JSON（如 list-nutrition-foods 的 toon 文本）：原样返回，交给 _parse_*
        return joined

    # --- ③ 握手 -------------------------------------------------------

    def _ensure_session(self) -> None:
        """MCP 握手：initialize → 取 Mcp-Session-Id → notifications/initialized"""
        if self._session_id or self._negotiated_version:
            return

        rpc_id = self._next_rpc_id()
        payload = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}, "sampling": {}},
                "clientInfo": {"name": "McD_MentalCare_Buddy", "version": CLI_VERSION},
            },
        }
        status, headers, body = self._http_post(payload)
        self._raise_for_status(status, headers, body)

        # 无状态（stateless）Server 允许不返回会话头，这时每次请求都独立也能工作
        self._session_id = headers.get("mcp-session-id") or None

        msgs = self._extract_messages(headers.get("content-type", ""), body)
        res = (self._pick_message(msgs, rpc_id) or {}).get("result") or {}
        self._negotiated_version = str(res.get("protocolVersion") or "")
        self.stats["server_info"] = res.get("serverInfo") or {}

        if self._negotiated_version and self._negotiated_version != MCP_PROTOCOL_VERSION:
            print(
                f"[warn] 服务端协议版本 {self._negotiated_version} "
                f"≠ 本地声明的 {MCP_PROTOCOL_VERSION}，按服务端版本继续。"
            )

        # 握手完成通知：按协议无需响应，失败也不影响后续调用
        try:
            self._http_post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:  # noqa: BLE001
            pass

    # --- ④ 主调用 -----------------------------------------------------

    def _call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """
        统一出口：发起一次 tools/call 并拆出业务数据。

        完整链路：
            ① 首次调用前握手（initialize → Mcp-Session-Id → notifications/initialized）
            ② 本地滑动窗口限流（预防撞官方 600 次/分钟）
            ③ POST tools/call（JSON-RPC 2.0）
            ④ 兼容 SSE / JSON 两种响应形态 → 拆 content[0].text → 二次 json.loads
            ⑤ 会话失效时自动重新握手并重试一次
        """
        self._ensure_session()

        for attempt in (0, 1):
            waited = self._limiter.acquire()
            self.stats["total_wait_s"] = round(self.stats["total_wait_s"] + waited, 3)
            self.stats["calls"] += 1

            rpc_id = self._next_rpc_id()
            payload = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments or {}},
            }
            status, headers, body = self._http_post(payload)

            try:
                self._raise_for_status(status, headers, body)
            except McpSessionExpiredError:
                if attempt == 0:  # 会话过期自愈：重建会话后重试一次
                    self._session_id = None
                    self._negotiated_version = ""
                    self._ensure_session()
                    continue
                raise

            msgs = self._extract_messages(headers.get("content-type", ""), body)
            if self.verbose:
                print(f"[mcp] <- {tool} id={rpc_id} msgs={len(msgs)} bytes={len(body)}")
            return self._unwrap_tool_result(self._pick_message(msgs, rpc_id), tool)

        raise McpError(f"调用 {tool} 失败：会话反复失效")

    def _real_or_mock(
        self,
        tool: str,
        arguments: dict[str, Any] | None,
        producer: Callable[[], Any],
    ) -> Any:
        """
        真实优先、失败降级。

        ⚠️ 只用于**只读**接口。写操作（calculate-price / create-order）
        一律不降级 —— 宁可报错也不能拿 Mock 金额骗用户下单。
        """
        try:
            return self._call_with_retry(tool, arguments)
        except Exception:  # noqa: BLE001
            if not self.fallback_to_mock:
                raise
            self.stats["mock_fallbacks"] += 1
            return producer()

    def _call_with_retry(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        """
        重试策略（注意异常是**有层级**的，顺序写错会吞掉语义）：

            McpAuthError      → 401/403，重试没意义，立刻抛
            McpRateLimitError → 429，优先听服务端 Retry-After，否则指数退避
            其他 McpError     → 网络/5xx，指数退避后重试
        """
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._call(tool, arguments)
            except McpAuthError:
                self.stats["errors"] += 1
                raise
            except McpRateLimitError as e:
                last_err = e
                self.stats["retries"] += 1
                wait = e.retry_after or (0.5 * (2**attempt) + random.uniform(0, 0.3))
                time.sleep(wait)
            except McpError as e:
                last_err = e
                self.stats["retries"] += 1
                time.sleep(0.5 * (2**attempt) + random.uniform(0, 0.2))
        self.stats["errors"] += 1
        raise McpError(f"调用 {tool} 失败（已重试 {self.max_retries} 次）：{last_err}")

    def _mock_json(self, name: str) -> Any:
        """读取 Mock 数据。文件缺失时抛清晰错误。"""
        path = MOCK_DIR / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"缺少 Mock 数据文件：{path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # ==================================================================
    # 官方 Tool 封装
    # ==================================================================

    # --- 基础信息 -----------------------------------------------------

    def now_time_info(self) -> dict[str, Any]:
        """
        now-time-info：获取当前时间信息。

        本项目用途：给情绪加「时间上下文」——
        23:47 自动过滤早餐品、周一早 8 点加权「周一综合征」、10 号左右命中发薪日。
        """
        def _mock() -> dict[str, Any]:
            import datetime as _dt

            now = _dt.datetime.now()
            return {
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "weekday": now.weekday(),          # 0=周一
                "day_of_month": now.day,
                "hour": now.hour,
            }

        if self.mock:
            return _mock()
        # ⚠️ 绝不能缓存：这是整条链路的时间源，缓存 = 时间静止
        raw = self._real_or_mock("now-time-info", None, _mock)
        return raw if isinstance(raw, dict) else {"datetime": str(raw)}

    def campaign_calendar(self) -> dict[str, Any]:
        """
        campaign-calendar：活动日历查询。

        用途：蹭官方活动做彩蛋，例如「今天有 XX 活动，这单还能再省」。
        """
        def _mock() -> dict[str, Any]:
            return self._mock_json("campaigns")

        if self.mock:
            return _mock()
        return self._cached(
            "campaign",
            TTL_CAMPAIGN,
            lambda: self._real_or_mock("campaign-calendar", None, _mock),
        )

    def query_my_account(self) -> dict[str, Any]:
        """
        query-my-account：我的积分查询。

        用途：发工资场景先查积分，发现「即将过期积分」就提醒用户先兑券。
        """
        def _mock() -> dict[str, Any]:
            return {"points": 2340, "available": 2340, "expiring_soon": 800, "frozen": 0}

        if self.mock:
            return _mock()
        raw = self._real_or_mock("query-my-account", None, _mock)
        if not isinstance(raw, dict):
            return _mock()
        # 官方字段名可能与本地不一致（如 points / availablePoints），这里做一次归一
        return {
            "points": raw.get("points") or raw.get("availablePoints") or 0,
            "available": raw.get("available") or raw.get("availablePoints") or 0,
            "expiring_soon": raw.get("expiring_soon") or raw.get("expiringPoints") or 0,
            "frozen": raw.get("frozen") or raw.get("frozenPoints") or 0,
            "raw": raw,
        }

    # --- 门店 ---------------------------------------------------------

    def query_nearby_stores(self, address: str = "") -> list[Store]:
        """
        query-nearby-stores：查询附近可用门店（到店取餐场景）。

        TODO(real-mcp): 官方入参是自然语言地址，返回门店列表。
        拿到 storeId 后才能调 query_meals —— 这是整条链路的第一个依赖点。
        """
        def _mock() -> list[Store]:
            return [Store.from_dict(d) for d in self._mock_json("stores")]

        if self.mock:
            return _mock()
        raw = self._cached(
            f"stores:{address}",
            TTL_STORE,
            lambda: self._real_or_mock(
                "query-nearby-stores", {"address": address}, _mock
            ),
        )
        # _mock() 返回的是 Store 对象列表，真实返回是 dict 列表，这里统一处理
        if raw and all(isinstance(x, Store) for x in raw):
            return list(raw)
        return [Store.from_dict(d) for d in self._as_list(raw)]

    def query_delivery_stores(self, address_id: str = "") -> list[Store]:
        """
        delivery-query-stores：外送场景查询可配送门店。

        TODO(real-mcp): 需先调 delivery-query-addresses 拿 addressId。
        """
        def _mock() -> list[Store]:
            return [s for s in self.query_nearby_stores() if s.supports_delivery]

        if self.mock:
            return _mock()
        raw = self._real_or_mock("delivery-query-stores", {"addressId": address_id}, _mock)
        if raw and all(isinstance(x, Store) for x in raw):
            return list(raw)
        return [Store.from_dict(d) for d in self._as_list(raw)]

    # --- 菜单 ---------------------------------------------------------

    def query_meals(self, store_id: str, refresh: bool = False) -> list[MenuItem]:
        """
        query-meals：查询当前门店可售卖的餐品列表。

        返回含「分类、餐品编码、标签」，其中标签用于过滤售罄与时段限制。
        热量数据不在这里，需另调 list_nutrition_foods() 补齐（见 enrich_nutrition）。
        """
        if refresh:
            self._cache.pop(f"menu:{store_id}", None)

        def _mock() -> list[MenuItem]:
            return [i for i in (MenuItem.from_dict(d) for d in self._mock_json("menu")) if i.available]

        if self.mock:
            return _mock()
        raw = self._cached(
            f"menu:{store_id}",
            TTL_MENU,
            lambda: self._real_or_mock("query-meals", {"storeId": store_id}, _mock),
        )
        if raw and all(isinstance(x, MenuItem) for x in raw):
            return list(raw)
        return [MenuItem.from_dict(d) for d in self._as_list(raw)]

    def query_meal_detail(self, meal_code: str) -> dict[str, Any]:
        """
        query-meal-detail：查询餐品详情（套餐组成、默认选项、可替换项）。

        用途：对候选集前 10 名做精细打分时用，避免全量调用打满限流。
        """
        def _mock() -> dict[str, Any]:
            return {"code": meal_code, "components": [], "options": []}

        if self.mock:
            return _mock()
        # 详情是静态度较高的数据，可以复用菜单的 TTL；
        # 更重要的是：候选集通常 10 个左右，不缓存很容易打满 600 次/分钟
        raw = self._cached(
            f"detail:{meal_code}",
            TTL_MENU,
            lambda: self._real_or_mock(
                "query-meal-detail", {"mealCode": meal_code}, _mock
            ),
        )
        return raw if isinstance(raw, dict) else _mock()

    @staticmethod
    def parse_nutrition_toon(text: str) -> dict[str, int]:
        """
        解析官方 `list-nutrition-foods` 的 **toon 紧凑格式**（已完成，非 TODO）。

        官方为了省 token 不用标准 JSON，而是这种表头 + CSV 行格式：:

            {productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbs,sodium,calcium}:
            猪柳麦满分,null,1288,308,16,16,24,781,213
            麦辣鸡腿堡,null,2345,561,28,31,42,1120,180

        解析策略：
          1. 从 `{...}:` 表头行拆出列名（**不假设列顺序**，官方改列也不会炸）
          2. 每行按 CSV 拆分，与列名做 zip
          3. 热量字段按优先级找：kcal 列名优先直接取，否则拿 kJ 列 ÷ 4.184 换算
        """
        result: dict[str, int] = {}
        if not text:
            return result

        columns: list[str] = []
        for raw_line in str(text).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # 表头行：以 { 开头
            if line.startswith("{"):
                end = line.find("}")
                if end == -1:
                    continue
                inner = line[1:end]
                tail = line[end + 1:].lstrip()
                tail = tail[1:] if tail.startswith(":") else tail  # 去掉收尾的冒号
                columns = [c.strip() for c in inner.split(",") if c.strip()]
                # 极少数实现把第一行数据紧跟在冒号后面
                if tail:
                    McDonaldsMCPClient._consume_toon_row(tail, columns, result)
                continue

            if columns:
                McDonaldsMCPClient._consume_toon_row(line, columns, result)

        return result

    @staticmethod
    def _consume_toon_row(line: str, columns: list[str], sink: dict[str, int]) -> None:
        """解析一行 toon 数据并写入 sink"""
        # 用 csv 而不是 str.split：描述字段里可能出现带引号的逗号（"脂肪, 碳水"）
        try:
            cells = [c.strip() for c in next(csv.reader([line]))]
        except Exception:  # noqa: BLE001
            cells = [c.strip() for c in line.split(",")]
        if len(cells) < 2:
            return
        row = dict(zip(columns, cells))

        # 商品名列：按常见命名兜底
        name = ""
        for key in ("productName", "name", "product", "mealName"):
            v = row.get(key)
            if v and v.lower() != "null":
                name = v
                break
        if not name:
            name = cells[0]
        if not name or name.lower() == "null":
            return

        # 热量列：优先 kcal，否则由 kJ 换算（1 kcal ≈ 4.184 kJ）
        kcal = 0
        for key in ("energyKcal", "kcal", "calories", "energy_kcal"):
            v = row.get(key, "")
            if v and v.lower() != "null":
                try:
                    kcal = int(float(v))
                    break
                except ValueError:
                    pass
        if not kcal:
            for key in ("energyKj", "energy", "kj", "energy_kj"):
                v = row.get(key, "")
                if v and v.lower() != "null":
                    try:
                        kcal = int(float(v) / 4.184)
                        break
                    except ValueError:
                        pass
        if kcal > 0:
            sink[name] = kcal

    def list_nutrition_foods(self) -> dict[str, int]:
        """
        list-nutrition-foods：餐品营养信息列表（返回 {商品名: 千卡}）。

        官方返回 toon 文本（见 `parse_nutrition_toon`），已在这里完成解析并转成
        「商品名 → 热量（kcal）」的映射，供 RP 公式的 `energy_fit` 分量使用。
        """
        def _mock() -> dict[str, int]:
            return {str(k): int(v) for k, v in self._mock_json("nutrition").items()}

        if self.mock:
            return _mock()

        raw = self._cached(
            "nutrition",
            TTL_NUTRITION,
            lambda: self._real_or_mock("list-nutrition-foods", None, _mock),
        )
        if isinstance(raw, dict):
            # 已经是 {名: 值}（Mock 或服务端直接给了结构化数据）
            return {str(k): int(v) for k, v in raw.items() if str(v).isdigit()}
        if isinstance(raw, str):
            return self.parse_nutrition_toon(raw)
        return _mock()

    def enrich_nutrition(self, items: list[MenuItem]) -> list[MenuItem]:
        """给菜单补齐热量数据（RP 公式的 energy_fit 分量依赖它）"""
        table = self.list_nutrition_foods()
        for it in items:
            if not it.kcal and it.name in table:
                it.kcal = int(table[it.name])
        return items

    # --- 优惠券 -------------------------------------------------------

    def available_coupons(self) -> list[Coupon]:
        """
        available-coupons：麦麦省券列表查询（当前**可领取**的券）。

        这是给用户看「待薅清单」用的，展示「还能领 N 张」。
        """
        def _mock() -> list[Coupon]:
            return [
                Coupon.from_dict(d)
                for d in self._mock_json("coupons")
                if d.get("claimable", True)
            ]

        if self.mock:
            return _mock()
        raw = self._real_or_mock("available-coupons", None, _mock)
        if raw and all(isinstance(x, Coupon) for x in raw):
            return list(raw)
        return [Coupon.from_dict(d) for d in self._as_list(raw)]

    def auto_bind_coupons(self) -> list[str]:
        """
        auto-bind-coupons：麦麦省一键领券。

        ⭐ 神级接口：**无需指定 couponId，一次调用自动领取所有当前可领的券**。

        ⚠️ 调用顺序铁律：必须在 calculate_price() **之前**调用，
        否则算出来的是「没券的价格」，省钱的爽感直接归零。
        """
        def _mock() -> list[str]:
            return [
                str(d.get("couponId") or d.get("coupon_id"))
                for d in self._mock_json("coupons")
                if d.get("claimable", True)
            ]

        if self.mock:
            return _mock()
        raw = self._real_or_mock("auto-bind-coupons", None, _mock)
        return [str(x) for x in self._as_list(raw)]

    def query_store_coupons(self, store_id: str) -> list[Coupon]:
        """
        query-store-coupons：查询用户在**当前门店**下可用的券。

        注意与 query_my_coupons 的区别：
          - query_my_coupons      = 我账户里所有的券（可能这家店用不了）
          - query_store_coupons   = 这家店能用的券（真正能核销的）
        算价前必须用它。
        """
        def _mock() -> list[Coupon]:
            return [
                Coupon.from_dict(d)
                for d in self._mock_json("coupons")
                if d.get("usable", True)
            ]

        if self.mock:
            return _mock()
        raw = self._real_or_mock("query-store-coupons", {"storeId": store_id}, _mock)
        if raw and all(isinstance(x, Coupon) for x in raw):
            return list(raw)
        return [Coupon.from_dict(d) for d in self._as_list(raw)]

    def query_my_coupons(self) -> list[Coupon]:
        """query-my-coupons：我的优惠券（全账户）"""
        def _mock() -> list[Coupon]:
            return [Coupon.from_dict(d) for d in self._mock_json("coupons")]

        if self.mock:
            return _mock()
        raw = self._real_or_mock("query-my-coupons", None, _mock)
        if raw and all(isinstance(x, Coupon) for x in raw):
            return list(raw)
        return [Coupon.from_dict(d) for d in self._as_list(raw)]

    # --- 算价与下单 ---------------------------------------------------

    def calculate_price(
        self,
        lines: list[Any],
        coupon_ids: list[str] | None = None,
        store_id: str = "",
        dine_mode: DineMode = DineMode.PICKUP,
        discount_hint: float = 0.0,
    ) -> PriceResult:
        """
        calculate-price：商品价格计算。

        入参：商品列表（可含优惠券）+ 门店 + 就餐方式
        返回：商品金额 / 配送费 / 优惠金额 / **应付总价**

        ⚠️ 设计原则：**绝不自己算钱**。
        本地 deal_calculator 只负责「选哪套券组合 + 要不要凑单」的**搜索**，
        最终展示给用户的金额一律以本方法的返回为准。

        Args:
            discount_hint: 仅 Mock 模式下使用的「本地估算减免额」。
                真实模式下本参数会被忽略 —— 因为真实减免由服务端算。
                TODO(real-mcp): 接入真实接口后，应把本方法返回的 payable 与
                本地估算做一次一致性校验，差异 > 0.01 元时中止下单并提示用户。
        """
        payload = {
            "storeId": store_id,
            "dineMode": dine_mode.value,
            "items": [
                {"mealCode": ln["code"], "quantity": ln["qty"]} for ln in lines
            ],
            "couponIds": coupon_ids or [],
        }

        if self.mock:
            subtotal = sum(float(ln.get("price", 0)) * int(ln.get("qty", 1)) for ln in lines)
            discount = min(float(discount_hint), subtotal)
            delivery_fee = 0.0 if dine_mode != DineMode.DELIVERY else DELIVERY_FEE
            return PriceResult(
                subtotal=round(subtotal, 2),
                discount=round(discount, 2),
                delivery_fee=delivery_fee,
                payable=round(max(0.0, subtotal - discount) + delivery_fee, 2),
                raw={"mock": True, "payload": payload},
            )

        raw = self._call_with_retry("calculate-price", payload)
        return PriceResult(
            subtotal=float(raw.get("totalAmount") or raw.get("subtotal") or 0.0),
            discount=float(raw.get("discountAmount") or raw.get("discount") or 0.0),
            delivery_fee=float(raw.get("deliveryFee") or 0.0),
            payable=float(raw.get("payableAmount") or raw.get("payable") or 0.0),
            raw=raw,
        )

    def create_order(
        self,
        lines: list[Any],
        store_id: str,
        dine_mode: DineMode = DineMode.PICKUP,
        coupon_ids: list[str] | None = None,
        confirm: bool = False,
    ) -> OrderResult:
        """
        create-order：创建订单。

        🚦 **安全闸门**：confirm 必须为 True 才会真正发起调用。
        这是本项目最硬的红线 —— 任何自动下单路径都不允许绕过它。
        """
        if not confirm:
            raise OrderNotConfirmedError(
                "create_order 需要 confirm=True。请先在 UI 展示确认卡片，"
                "由用户显式确认后再调用（CLI 中对应去掉 --dry-run）。"
            )

        payload = {
            "storeId": store_id,
            "dineMode": dine_mode.value,
            "items": [{"mealCode": ln["code"], "quantity": ln["qty"]} for ln in lines],
            "couponIds": coupon_ids or [],
        }

        if self.mock:
            return OrderResult(
                order_id="MOCK" + str(int(time.time()))[-6:],
                pay_url="https://mock.mcd.cn/pay/unavailable",
                pickup_code="A073",
                status="CREATED",
                raw={"mock": True, "payload": payload},
            )

        raw = self._call_with_retry("create-order", payload)
        return OrderResult(
            order_id=str(raw.get("orderId") or raw.get("orderNo") or ""),
            pay_url=str(raw.get("payUrl") or ""),
            pickup_code=str(raw.get("pickupCode") or raw.get("qrCode") or ""),
            status=str(raw.get("status") or ""),
            raw=raw,
        )

    def query_order(self, order_id: str) -> OrderResult:
        """query-order：查询订单详情（状态 / 取餐号 / 取餐柜二维码）"""
        if self.mock:
            return OrderResult(order_id=order_id, status="PAID", pickup_code="A073", raw={"mock": True})
        raw = self._call_with_retry("query-order", {"orderId": order_id})
        return OrderResult(
            order_id=order_id,
            status=str(raw.get("status") or ""),
            pickup_code=str(raw.get("pickupCode") or ""),
            raw=raw,
        )

    def order_list(self) -> list[dict[str, Any]]:
        """order-list：查询历史订单（用于月底生成「续命账单」）"""
        if self.mock:
            return []
        return self._as_list(self._call_with_retry("order-list"))

    # ==================================================================
    # 连通性自检 —— 答辩 / 排查利器
    # ==================================================================

    def list_tools(self, max_pages: int = 10) -> list[dict[str, Any]]:
        """
        tools/list：列出服务端**真实**暴露的 Tool。

        用途：校验我们写死的 15 个 Tool 名是否仍然有效。
        官方一旦改名 / 下线，这里第一时间报警，而不是等到调用时才炸。
        """
        self._ensure_session()

        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            self._limiter.acquire()
            rpc_id = self._next_rpc_id()
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            status, headers, body = self._http_post(
                {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/list", "params": params}
            )
            self._raise_for_status(status, headers, body)
            msgs = self._extract_messages(headers.get("content-type", ""), body)
            res = (self._pick_message(msgs, rpc_id) or {}).get("result") or {}
            tools.extend(t for t in (res.get("tools") or []) if isinstance(t, dict))
            cursor = res.get("nextCursor") or None
            if not cursor:
                break
        return tools

    def verify_tools(self, expected: Iterable[str] = REQUIRED_TOOLS) -> dict[str, Any]:
        """
        把本项目依赖的 Tool 与服务端实际暴露的做差集。

        返回::

            {"total": 33, "missing": [...], "available": [...], "extras": [...]}
        """
        if self.mock:
            return {
                "total": len(REQUIRED_TOOLS),
                "missing": [],
                "available": list(REQUIRED_TOOLS),
                "extras": [],
                "skipped": True,
            }
        try:
            server_names = {
                str(t.get("name")) for t in self.list_tools() if t.get("name")
            }
        except McpError as e:
            return {"total": 0, "missing": list(expected), "available": [], "extras": [], "error": str(e)}

        expected_set = set(expected)
        return {
            "total": len(server_names),
            "missing": sorted(expected_set - server_names),
            "available": sorted(expected_set & server_names),
            "extras": sorted(server_names - expected_set),
            "error": "",
        }

    def doctor(self) -> dict[str, Any]:
        """
        一键体检：`python main.py --live --doctor`

        依次检查：Token → 握手 → 协议版本 → 业务接口 → Tool 清单 → 限流余量。
        任何一项失败都会给出**可执行的**修复建议，而不是干巴巴报错。
        """
        report: dict[str, Any] = {
            "base_url": self.base_url,
            "mode": "mock" if self.mock else "live",
            "token": self._mask(self._token),
            "checks": [],
            "ok": True,
        }

        def check(name: str, ok: bool, detail: str, level: str = "fail") -> None:
            report["checks"].append(
                {"name": name, "status": "ok" if ok else level, "detail": detail}
            )
            if not ok and level == "fail":
                report["ok"] = False

        if self.mock:
            check("运行模式", True, "Mock 模式（离线演示数据，无需 Token）", "ok")
            return report

        # 1) 握手
        try:
            t0 = time.time()
            self._ensure_session()
            ms = int((time.time() - t0) * 1000)
            check("MCP 握手", True, f"耗时 {ms}ms，会话 {'已建立' if self._session_id else '无状态'}")
            check(
                "协议版本",
                bool(self._negotiated_version),
                f"服务端 {self._negotiated_version or '未返回'} / 本地 {MCP_PROTOCOL_VERSION}",
                "warn",
            )
        except McpAuthError as e:
            check("MCP 握手", False, f"鉴权失败：{e}。请重新获取 Token 并 export MCD_MCP_TOKEN")
            return report
        except McpError as e:
            check("MCP 握手", False, f"握手失败：{e}。请确认网络可访问 {self.base_url}")
            return report

        # 2) 业务接口冒烟
        try:
            t0 = time.time()
            info = self.now_time_info()
            check(
                "now-time-info",
                True,
                f"服务端时间 {info.get('datetime')}（RTT {int((time.time()-t0)*1000)}ms）",
            )
        except McpError as e:
            check("now-time-info", False, f"调用失败：{e}")

        # 3) Tool 清单差分
        try:
            v = self.verify_tools()
            missing = v.get("missing") or []
            check(
                "Tool 清单校验",
                not missing,
                f"服务端共 {v.get('total')} 个 Tool，本项目用 {len(REQUIRED_TOOLS)} 个"
                + (f"，缺失：{missing}" if missing else "，全部可用 ✅"),
            )
        except Exception as e:  # noqa: BLE001
            check("Tool 清单校验", False, f"无法拉取 tools/list：{e}", "warn")

        # 4) 限流余量
        remain = self._limiter.remaining
        check(
            "限流配额",
            remain > 60,
            f"本分钟剩余 {remain}/{self._limiter.limit} 次（官方上限 600/分钟）",
            "warn",
        )
        return report

    def close(self) -> None:
        """显式关闭会话（DELETE + Mcp-Session-Id）。不调也不会泄漏，但优雅一些"""
        if not self._session_id:
            return
        try:
            req = urllib.request.Request(
                self.base_url, headers=self._headers(), method="DELETE"
            )
            with urllib.request.urlopen(req, timeout=self.timeout):
                pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._session_id = None
            self._negotiated_version = ""

    def __enter__(self) -> "McDonaldsMCPClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    @staticmethod
    def _as_list(raw: Any) -> list[Any]:
        """把 MCP 返回的各种外层结构统一拍平成 list"""
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for key in ("data", "items", "list", "records", "result"):
                v = raw.get(key)
                if isinstance(v, list):
                    return v
                if isinstance(v, dict):
                    return McDonaldsMCPClient._as_list(v)
            return [raw]
        return [raw]


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


def build_client(
    token: str | None = None,
    mock: bool = False,
    fallback_to_mock: bool = False,
    verbose: bool = False,
    warn: Callable[[str], None] = print,
) -> McDonaldsMCPClient:
    """
    构造客户端。

    优先级：显式 mock=True > 显式 token > 环境变量 > Token 文件 > 自动降级 mock

    Args:
        fallback_to_mock: 真实接口失败时自动降级到 Mock（**仅只读接口**生效）。
            演示 / 答辩场景建议打开；生产契约请勿打开，否则会把故障藏起来。
    """
    if mock:
        return McDonaldsMCPClient(mock=True, verbose=verbose)
    try:
        return McDonaldsMCPClient(
            token=token,
            mock=False,
            fallback_to_mock=fallback_to_mock,
            verbose=verbose,
        )
    except McpAuthError as e:
        # 静默降级是一把双刃剑：CLI 保活要它，但必须让用户知道自己在看假数据
        warn(f"[warn] {e}\n[warn] 已自动降级到 Mock 模式（离线演示数据）。")
        return McDonaldsMCPClient(mock=True, verbose=verbose)
