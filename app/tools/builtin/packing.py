from __future__ import annotations

from typing import Any

from app.tools.registry import ToolRegistry

_SCENARIO_PACK = {
    "sunny": ["防晒（帽/墨镜/防晒霜 SPF30+）", "水壶或补水", "透气速干衣"],
    "cloudy": ["薄外套或防晒衣", "舒适步行鞋"],
    "rainy": ["折叠伞或雨衣", "防滑鞋", "塑料袋装湿物", "室内备用衣服"],
}

# Scenario keywords — not a per-city tips DB (AGENTS: no fake city guidebook).
_ALTITUDE_KW = (
    "高海拔",
    "高原",
    "缺氧",
    "拉萨",
    "日喀则",
    "林芝",
    "香格里拉",
    "丽江",
    "稻城",
    "亚丁",
    "玉树",
    "西宁",
    "格尔木",
    "那曲",
    "川西",
    "四姑娘",
    "贡嘎",
    "墨脱",
)
_DESERT_KW = ("沙漠", "滑沙", "戈壁", "腾格里", "鸣沙")
_RIVER_KW = ("黄河", "漂流", "水上", "海边", "海滩", "潜水")
_HIKE_KW = ("徒步", "登山", "爬山", "户外", "露营")
_MUSEUM_KW = ("博物馆", "寺", "人文", "展馆")
_COLD_KW = ("滑雪", "冰雪", "东北", "哈尔滨", "长白山")


def _uniq(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def _parse_interests(interests: Any) -> list[str]:
    if isinstance(interests, str) and interests.strip():
        return [interests.strip()]
    if isinstance(interests, list):
        return [str(x).strip() for x in interests if str(x).strip()]
    return []


def _norm_scenario(weather_scenario: str | None) -> str | None:
    scenario = (weather_scenario or "").strip().lower() or None
    if scenario in {"晴天", "晴"}:
        return "sunny"
    if scenario in {"阴天", "阴"}:
        return "cloudy"
    if scenario in {"雨天", "雨"}:
        return "rainy"
    return scenario


def _has_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


async def suggest_packing(
    weather_scenario: str | None = None,
    temp_max_c: float | None = None,
    temp_min_c: float | None = None,
    interests: Any = None,
    days: int = 2,
    city: str = "",
    altitude_m: float | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Rule-based packing: clothes + toiletries + scenario alerts (no shopping/API)."""
    days = max(1, min(int(days or 1), 14))
    city = (city or "").strip()
    scenario = _norm_scenario(weather_scenario)
    interest_list = _parse_interests(interests)
    context = " ".join([city, *interest_list])

    try:
        alt = float(altitude_m) if altitude_m is not None else None
    except (TypeError, ValueError):
        alt = None
    high_altitude = (alt is not None and alt >= 2500) or _has_any(context, _ALTITUDE_KW)

    # —— 衣物参考 ——
    clothes = [
        f"换洗衣物约 {max(2, min(days + 1, 5))} 套（按天数）",
        "舒适步行鞋 + 一双备用袜",
        "内衣内裤按天数 +1",
    ]
    # —— 生活用品参考 ——
    toiletries = [
        "身份证/证件与少量现金+移动支付",
        "充电器/充电宝/数据线",
        "洗漱套装（牙刷牙膏、毛巾或酒店自备确认）",
        "常用药（感冒/肠胃/创可贴/个人处方药）",
        "水杯或折叠水壶",
    ]
    # —— 场景健康/安全提示（参考，非医疗建议）——
    alerts: list[str] = []

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
        clothes.append("轻薄短袖/速干为主")
        weather_items.extend(["额外防晒与补水"])
    if tmin is not None and tmin <= 10:
        clothes.extend(["保暖外套", "长裤"])
    if tmin is not None and tmin <= 0:
        clothes.extend(["羽绒服或厚棉服", "手套/围巾/帽子"])
    if tmin is not None and tmax is not None and (tmax - tmin) >= 12:
        clothes.append("分层穿衣（早晚温差大，洋葱式）")
        weather_items.append("早晚加外套，中午可减层")

    activity_items: list[str] = []
    if _has_any(context, _DESERT_KW):
        activity_items.extend(["防风沙眼镜/口罩", "封闭鞋优于凉鞋", "润唇膏", "长袖防晒"])
    if _has_any(context, _RIVER_KW):
        activity_items.extend(["可换洗鞋袜", "防水袋（手机）", "速干短裤（如亲水）"])
    if _has_any(context, _HIKE_KW):
        activity_items.extend(["双肩小包", "防磨脚贴", "头灯或小手电（若早晚出发）"])
        clothes.append("防滑户外鞋优于时装鞋")
    if _has_any(context, _MUSEUM_KW):
        clothes.append("薄外套（室内空调）")
    if _has_any(context, _COLD_KW):
        clothes.extend(["防滑雪服或厚外套", "防滑靴"])
        toiletries.append("护手霜/防裂膏")

    if high_altitude:
        alt_note = f"（约 {int(alt)} m）" if alt is not None else ""
        alerts.extend(
            [
                f"高海拔{alt_note}：提前了解自身心肺状况；剧烈运动循序渐进（参考提示，非医疗建议）",
                "可考虑便携氧气袋/小氧气瓶（按航空与景区规定携带；敏感体质先咨询医生）",
                "红景天等高原适应类保健品因人而异，用药前咨询医生或药师，勿自行替代正规诊疗",
                "防晒加强（高原紫外线强）：高倍防晒+墨镜+唇膏",
                "保湿与多饮水；少饮酒，首日避免剧烈登山",
            ]
        )
        clothes.append("防风保暖外套（高原昼夜温差大）")
        toiletries.append("润唇膏与保湿霜（干燥）")

    packing = {
        "clothes": _uniq(clothes),
        "toiletries": _uniq(toiletries),
        "weather": _uniq(weather_items),
        "activities": _uniq(activity_items),
        "alerts": _uniq(alerts),
        # backward-compatible alias used by older prompts/tests
        "essentials": _uniq(toiletries[:1] + clothes[:1] + toiletries[1:]),
    }
    flat = _uniq(
        packing["clothes"]
        + packing["toiletries"]
        + weather_items
        + activity_items
        + alerts
    )
    return {
        "ok": True,
        "city": city or None,
        "days": days,
        "weather_scenario": scenario,
        "temp_range_c": [tmin, tmax] if tmin is not None or tmax is not None else None,
        "altitude_m": alt,
        "high_altitude": high_altitude,
        "interests": interest_list,
        "packing": packing,
        "checklist": flat,
        "sections": {
            "衣物参考": packing["clothes"],
            "生活用品参考": packing["toiletries"],
            "天气相关": packing["weather"],
            "活动相关": packing["activities"],
            "场景提示": packing["alerts"],
        },
        "note": (
            "规则生成的建议携带清单（衣物/生活用品/场景提示），非购物推荐、非医疗建议；"
            "请按个人体质、航司安检与实时天气增减。"
        ),
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "suggest_packing",
        suggest_packing,
        description=(
            "Rule-based packing checklist: clothes, toiletries, scenario alerts "
            "(e.g. high-altitude oxygen/rhodiola tips). Not medical or shopping advice. "
            'Action Input: {"city":"拉萨","days":3,"weather_scenario":"sunny",'
            '"interests":["高原","布达拉宫"],"temp_max_c":18,"temp_min_c":5,'
            '"altitude_m":3650}'
        ),
        permission="low",
    )
