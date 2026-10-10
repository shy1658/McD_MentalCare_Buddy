"""
麦麦「打工人精神续命」搭子 (McD_MentalCare_Buddy)

一个听得懂你今天有多惨，并给你算好券的麦当劳 AI 搭子。

模块一览
--------
    mcp_client      MCP 调用层（Streamable HTTP 全实现 + 15 个 Tool + Mock 降级）
    annotation      本地口味标注层（真实菜单不含口味向量时的补钙）
    mood_engine     情绪识别 + 情绪→商品打分（RP 续命值）
    deal_calculator 券组合搜索 + 凑单决策
    copywriting     发疯文学 / 续命日报 / 反向凡尔赛
    renderer        Markdown 渲染
    models          全项目数据结构

快速开始
--------
    from mcd_mood_buddy.mcp_client import build_client
    from mcd_mood_buddy.mood_engine import detect_mood, rank_items
    from mcd_mood_buddy.annotation import annotate_menu

    client = build_client(mock=True)          # 无需 Token
    mood = detect_mood("改了 40 个 bug 我想死")
    menu = client.enrich_nutrition(client.query_meals("S001"))
    menu = annotate_menu(menu)                # ⭐ 真实数据必须过这一层
    top = rank_items(menu, mood, top_k=5)
"""

__version__ = "0.1.0"
__author__ = "McD_MentalCare_Buddy Team"

__all__ = [
    "mcp_client",
    "annotation",
    "mood_engine",
    "deal_calculator",
    "copywriting",
    "renderer",
    "models",
]
