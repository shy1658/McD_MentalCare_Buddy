#!/usr/bin/env python3
"""
麦麦「打工人精神续命」搭子 —— 交互式试玩入口

不用记命令行参数，直接说话就行。适合：
  - 评委 / 朋友第一次体验（双击 启动试玩.bat 即可）
  - 自己快速试不同情绪，看续命值怎么变

用法：
    python demo.py

进入后可以输入：
    直接说一句话        → 出方案（例：还有40个bug要改 我想死）
    /style crazy|flex|report   切换文风：发疯文学 / 反向凡尔赛 / 续命日报
    /budget 40          → 设置预算上限（/budget off 取消）
    /moods              → 看看它认得哪些精神状态
    /help               → 帮助
    q                   → 退出

全程离线 Mock 数据，不会联网、不会下单、不需要 Token。
"""

import sys

# Windows 控制台默认可能是 GBK，强制 UTF-8 避免中文乱码
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from mcd_mood_buddy import copywriting                      # noqa: E402
from mcd_mood_buddy.mood_engine import MOOD_LIBRARY         # noqa: E402
from main import run                                        # noqa: E402

STYLE_ALIAS = {
    "crazy": copywriting.STYLE_CRAZY,
    "flex": copywriting.STYLE_FLEXY,
    "report": copywriting.STYLE_REPORT,
}
STYLE_DESC = {
    copywriting.STYLE_CRAZY: "发疯文学",
    copywriting.STYLE_FLEXY: "反向凡尔赛",
    copywriting.STYLE_REPORT: "续命日报",
}

WELCOME = """
╔══════════════════════════════════════════════════════╗
║   🍔  麦麦「打工人精神续命」搭子  ·  交互式试玩        ║
╚══════════════════════════════════════════════════════╝

  说一句话，我就给你算好今天该吃什么、要花多少、以及
  可以发朋友圈的那句话。

  现在，你今天怎么样？

  ────────────────────────────────────────────────
  /moods  看看它认得哪些精神状态
  /style  切换文风（crazy 发疯 / flex 凡尔赛 / report 日报）
  /budget 设置预算   /help 帮助   q 退出
  ────────────────────────────────────────────────
"""

HELP = """
  ── 可用命令 ──────────────────────────────────
  直接说话              出方案。例：还有40个bug要改 我想死
  /style crazy          发疯文学（默认，最解气）
  /style flex           反向凡尔赛（适合发薪日炫耀）
  /style report         续命日报（克制、适合真累的时候）
  /budget 40            预算上限 40 元
  /budget off           取消预算
  /moods                列出全部精神状态
  /help                 显示本帮助
  q                     退出
  ──────────────────────────────────────────────
  全程离线 Mock 数据：不联网、不下单、不需要 Token。
"""


def show_moods() -> None:
    print("\n  它认得这些精神状态（失血值 = 你今天有多惨）：\n")
    rows = sorted(MOOD_LIBRARY.values(), key=lambda p: -p.bleed_score)
    for p in rows:
        bar = "▓" * round(p.bleed_score / 5) + "░" * (20 - round(p.bleed_score / 5))
        print(f"    {p.label:<10} 失血 {p.bleed_score:>3}  {bar}")
    print("\n  试着输入上面任意一个词，或者你自己的一句话。\n")


def main() -> int:
    print(WELCOME)
    style = copywriting.STYLE_CRAZY
    budget: float | None = None

    while True:
        try:
            raw = input("你今天怎么样？ > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n  去吃点东西吧。🍔\n")
            return 0

        if not raw:
            continue

        low = raw.lower()
        if low in ("q", "quit", "exit", "bye", "退出"):
            print("\n  去吃点东西吧。🍔\n")
            return 0
        if low in ("/help", "help", "?", "/?"):
            print(HELP)
            continue
        if low in ("/moods", "/list", "/mood"):
            show_moods()
            continue

        if low.startswith("/style"):
            parts = raw.split()
            key = parts[1].lower() if len(parts) > 1 else ""
            if key in STYLE_ALIAS:
                style = STYLE_ALIAS[key]
                print(f"\n  ✅ 文风已切换为：{STYLE_DESC[style]}\n")
            else:
                print("\n  ⚠️ 可用文风：crazy（发疯）/ flex（凡尔赛）/ report（日报）\n")
            continue

        if low.startswith("/budget"):
            parts = raw.split()
            val = parts[1].lower() if len(parts) > 1 else ""
            if val in ("off", "none", "0"):
                budget = None
                print("\n  ✅ 已取消预算限制\n")
            else:
                try:
                    budget = float(val)
                    print(f"\n  ✅ 预算上限设为 ¥{budget:.0f}\n")
                except ValueError:
                    print("\n  ⚠️ 用法：/budget 40   或   /budget off\n")
            continue

        # 正常输入 → 出方案
        try:
            md, _order = run(text=raw, budget=budget, style=style, mock=True)
        except Exception as e:  # 不要让任何异常打断试玩
            print(f"\n  ❌ 出错了：{type(e).__name__}: {e}\n")
            continue

        print()
        print(md)
        print("\n  ───────────────────────────────────────────")
        print(f"  当前文风：{STYLE_DESC[style]}　预算：{'不限' if budget is None else '¥%.0f' % budget}")
        print("  ───────────────────────────────────────────")


if __name__ == "__main__":
    sys.exit(main())
