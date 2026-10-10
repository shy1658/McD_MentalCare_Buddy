"""
薄转发器 —— 让 Skill 入口与仓库根入口完全等价
=================================================

    python skill/scripts/main.py "累成狗"     ≡     python main.py "累成狗"

为什么用转发而不是复制一份代码？
--------------------------------
本项目的**唯一实现源**在仓库根目录：

    <ROOT>/main.py                  CLI 入口 + 流程编排
    <ROOT>/mcd_mood_buddy/*.py      MCP 调用层 / 情绪引擎 / 券优化 / 文案 / 渲染
    <ROOT>/data/mock/*.json         离线演示数据
    <ROOT>/data/mappings/*.json     口味标注映射表

Skill 只是「大赛要求的交付形态」，因此这里只做路径转发，
**不复制任何业务逻辑** —— 避免两份代码不同步这种必然发生的灾难。

目录关系::

    <ROOT>/
      ├── main.py                ← 真正的实现
      ├── mcd_mood_buddy/
      ├── data/
      └── skill/
          └── scripts/
              └── main.py        ← 本文件（parents[2] 即 <ROOT>）
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# skill/scripts/main.py → parents[0]=scripts, parents[1]=skill, parents[2]=仓库根
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    # 必须前置：根 main.py 里有 `from mcd_mood_buddy import ...`
    sys.path.insert(0, str(ROOT))

_ENTRY = ROOT / "main.py"
if not _ENTRY.exists():  # pragma: no cover - 目录被破坏时才触发
    raise SystemExit(f"[skill] 找不到入口脚本：{_ENTRY}")

# 用文件路径加载，避免与根 main.py 同名模块冲突
_spec = importlib.util.spec_from_file_location("_mcd_root_main", _ENTRY)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise SystemExit(f"[skill] 无法加载入口脚本：{_ENTRY}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)  # 此时 __name__ != "__main__"，不会自动执行

if __name__ == "__main__":
    sys.exit(_mod.main())
