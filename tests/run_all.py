"""
一键跑全部测试（零第三方依赖，不需要 pytest）::

    python tests/run_all.py

会依次执行：
    tests/test_smoke.py            —— 情绪/打分/凑单/安全闸门
    tests/test_live_transport.py   —— MCP 传输层（握手/SSE/429/会话自愈/toon）
    tests/test_e2e_fake_server.py  —— 拟真服务端上的完整点餐链路
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

SUITES = [
    ("安全层测试（心理危机干预）", "test_safety.py"),
    ("券叠加测试（不可叠加/互斥组）", "test_deal_stacking.py"),
    ("冒烟测试（业务逻辑）", "test_smoke.py"),
    ("传输层测试（MCP 协议）", "test_live_transport.py"),
    ("端到端测试（拟真服务端）", "test_e2e_fake_server.py"),
]


def main() -> int:
    failed = 0
    for title, name in SUITES:
        print("=" * 64)
        print(f"▶ {title}  ——  {name}")
        print("=" * 64)
        r = subprocess.run([PY, str(HERE / name)], check=False)
        if r.returncode != 0:
            failed += 1
        print()

    print("=" * 64)
    if failed:
        print(f"❌ {failed}/{len(SUITES)} 个套件未通过")
        return 1
    print(f"✅ 全部 {len(SUITES)} 个套件通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
