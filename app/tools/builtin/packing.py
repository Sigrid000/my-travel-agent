from __future__ import annotations

from typing import Any

from app.tools.registry import ToolRegistry

_SCENARIO_PACK = {
    "sunny": ["防晒（帽/墨镜/防晒霜 SPF30+）", "水壶或补水", "透气速干衣"],
    "cloudy": ["薄外套或防晒衣", "舒适步行鞋"],
    "rainy": ["折叠伞或雨衣", "防滑鞋", "塑料袋装湿物", "室内备用衣服"],
}


async def suggest_packing(
    weather_scenario: str | None = None,
    temp_max_c: float | None = None,
    temp_min_c: float | None = None,
    interests: Any = None,
    days: int = 2,
    city: str = "",
    **_: Any,
) -> dict[str, Any]:
    """Rule-based packing list from weather + interests — no shopping/API."""
    days = max(1, min(int(days or 1), 14))
    city = (city or "").strip()
    scenario = (weather_scenario or "").strip().lower() or None
    if scenario in {"晴天", "晴"}:
        scenario = "sunny"
    elif scenario in {"阴天", "阴"}:
        scenario = "cloudy"
    elif scenario in {"雨天", "雨"}:
        scenario = "rainy"

    interest_list: list[str] = []
    if isinstance(interests, str) and interests.strip():
        interest_list = [interests.strip()]
    elif isinstance(interests, list):
        interest_list = [str(x).strip() for x in interests if str(x).strip()]

    essentials = [
        "身份证/证件与少量现金+移动支付",
        f"换洗衣物约 {max(2, min(days + 1, 5))} 套（按天数）",
        "充电器/充电宝",
        "常用药（感冒肠胃/创可贴）",
        "舒适步行鞋",
    ]
    weather_items: list[str] = []
    if scenario and scenario in _SCENARIO_PACK:
        weather_items.extend(_SCENARIO_PACK[scenario])
    try:
        tmax = float(temp_max_c) if temp_max_c is not None else None
    except (TypeError, ValueError):
        tmax = None
    try:
        tmin = float(temp_min_c) if temp_min_c is not None else None
    except (TypeError, ValueError):
        tmin = None
    if tmax is not None and tmax >= 30:
        weather_items.extend(["轻薄短袖", "额外防晒与补水"])
    if tmin is not None and tmin <= 10:
        weather_items.extend(["保暖外套", "长裤"])
    if tmin is not None and tmax is not None and (tmax - tmin) >= 12:
        weather_items.append("分层穿衣（早晚温差大）")

    activity_items: list[str] = []
    joined = " ".join(interest_list)
    desert_kw = ("沙漠", "滑沙", "戈壁", "腾格里")
    river_kw = ("黄河", "漂流", "水上")
    hike_kw = ("徒步", "登山", "爬山", "户外")
    museum_kw = ("博物馆", "寺", "人文")
    if any(k in joined for k in desert_kw):
        activity_items.extend(["防风沙眼镜/口罩", "封闭鞋优于凉鞋", "润唇膏", "长袖防晒"])
    if any(k in joined for k in river_kw):
        activity_items.extend(["可换洗鞋袜", "防水袋（手机）"])
    if any(k in joined for k in hike_kw):
        activity_items.extend(["双肩小包", "防磨脚贴"])
    if any(k in joined for k in museum_kw):
        activity_items.append("薄外套（室内空调）")

    # de-dupe preserving order
    def _uniq(items: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        return out

    packing = {
        "essentials": _uniq(essentials),
        "weather": _uniq(weather_items),
        "activities": _uniq(activity_items),
    }
    flat = _uniq(essentials + weather_items + activity_items)
    return {
        "ok": True,
        "city": city or None,
        "days": days,
        "weather_scenario": scenario,
        "temp_range_c": [tmin, tmax] if tmin is not None or tmax is not None else None,
        "interests": interest_list,
        "packing": packing,
        "checklist": flat,
        "note": "规则生成的建议携带清单，非购物推荐；请按个人体质与实时天气增减。",
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "suggest_packing",
        suggest_packing,
        description=(
            "Rule-based packing/clothing checklist. "
            'Action Input: {"city":"中卫","days":3,"weather_scenario":"sunny",'
            '"interests":["沙漠","黄河"],"temp_max_c":28,"temp_min_c":8}'
        ),
        permission="low",
    )
