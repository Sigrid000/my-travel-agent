from __future__ import annotations

import json
from typing import Any

from app.tools.registry import ToolRegistry

# Transparent heuristic bands — never claimed as live quotes.
_MEAL_CNY = {"budget": (15, 35), "mid": (30, 80), "comfort": (60, 150)}
_HOTEL_NIGHT_CNY = {"budget": (80, 180), "mid": (150, 350), "comfort": (300, 700)}
# Scenic gate / activity experience when no official ticket API (labeled as such)
_ATTRACTION_TICKET_CNY = (40, 180)
_ACTIVITY_EXTRA_CNY = (0, 200)  # 滑沙等体验另计，无官方价时给宽区间


def _parse_list(val: Any) -> list[Any]:
    if val is None or val == "":
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _style_key(style: str) -> str:
    s = (style or "mid").strip().lower()
    if s in {"budget", "cheap", "economy"}:
        return "budget"
    if s in {"comfort", "luxury", "high"}:
        return "comfort"
    return "mid"


def _taxi_from_distance_m(distance_m: float) -> tuple[int, int]:
    """CN taxi-style range from measured distance — not a live meter."""
    km = max(0.0, float(distance_m) / 1000.0)
    low = 8 + max(0.0, km - 3.0) * 2.0
    high = 13 + max(0.0, km - 2.0) * 3.5
    return int(round(low)), int(round(high))


async def estimate_budget(
    city: str = "",
    days: int = 2,
    budget_cny: int | None = None,
    style: str = "mid",
    route_legs: Any = None,
    attraction_count: int | None = None,
    food_cost_hints: Any = None,
    hotel_cost_hints: Any = None,
    meals_per_day: int = 3,
    include_activity_extras: bool = True,
    **_: Any,
) -> dict[str, Any]:
    """Budget ranges grounded in route distances + optional Amap cost hints.

    Exact tickets are never invented: without official prices, attraction fees use
    a labeled national scenic experience band.
    """
    days = max(1, min(int(days or 1), 14))
    nights = max(1, days - 1) if days > 1 else 1
    city = (city or "").strip() or "未指定城市"
    tier = _style_key(style)
    meals_per_day = max(1, min(int(meals_per_day or 3), 4))

    legs = _parse_list(route_legs)
    food_hints = _parse_list(food_cost_hints)
    hotel_hints = _parse_list(hotel_cost_hints)

    transport_parts: list[dict[str, Any]] = []
    t_low = t_high = 0
    total_m = 0.0
    for i, leg in enumerate(legs):
        if not isinstance(leg, dict):
            continue
        try:
            dist = float(leg.get("distance_m") or leg.get("distance") or 0)
        except (TypeError, ValueError):
            dist = 0.0
        if dist <= 0:
            continue
        total_m += dist
        lo, hi = _taxi_from_distance_m(dist)
        t_low += lo
        t_high += hi
        transport_parts.append(
            {
                "leg": i + 1,
                "from": leg.get("from_name") or leg.get("from") or "",
                "to": leg.get("to_name") or leg.get("to") or "",
                "distance_m": int(dist),
                "taxi_cny_range": [lo, hi],
            }
        )
    if not transport_parts:
        # No measured legs → weak daily local-transport band (labeled)
        t_low, t_high = 40 * days, 120 * days
        transport_note = "未传入 route_legs，市内交通用弱启发式区间；请先 estimate_route 再汇总。"
    else:
        transport_note = (
            "市内交通按各段 estimate_route 距离用出租车经验公式估算"
            "（起步约8–13元 + 超公里约2–3.5元/km），非实时计价、不含网约车溢价。"
        )

    meal_lo, meal_hi = _MEAL_CNY[tier]
    hint_vals: list[float] = []
    for h in food_hints:
        try:
            if isinstance(h, dict) and h.get("amap_avg_cost") is not None:
                hint_vals.append(float(h["amap_avg_cost"]))
            else:
                hint_vals.append(float(h))
        except (TypeError, ValueError):
            continue
    if hint_vals:
        avg = sum(hint_vals) / len(hint_vals)
        meal_lo = max(15, int(avg * 0.7))
        meal_hi = max(meal_lo + 10, int(avg * 1.4))
        food_note = f"餐饮参考高德返回的人均字段（样本{len(hint_vals)}家）推导餐均区间，非菜单实价。"
    else:
        food_note = "无高德人均样本时用档位餐均启发式；请搭配 search_food_nearby 结果写进行程。"
    food_low = meal_lo * meals_per_day * days
    food_high = meal_hi * meals_per_day * days

    hot_lo, hot_hi = _HOTEL_NIGHT_CNY[tier]
    hotel_hint_vals: list[float] = []
    for h in hotel_hints:
        try:
            if isinstance(h, dict) and h.get("amap_avg_cost") is not None:
                hotel_hint_vals.append(float(h["amap_avg_cost"]))
            else:
                hotel_hint_vals.append(float(h))
        except (TypeError, ValueError):
            continue
    if hotel_hint_vals:
        avg_h = sum(hotel_hint_vals) / len(hotel_hint_vals)
        hot_lo = max(60, int(avg_h * 0.8))
        hot_hi = max(hot_lo + 20, int(avg_h * 1.3))
        lodging_note = f"住宿参考高德消费字段样本{len(hotel_hint_vals)}家；预订以平台实时价为准。"
    else:
        lodging_note = "住宿为档位晚均启发式，非实时房价。"
    lodging_low = hot_lo * nights
    lodging_high = hot_hi * nights

    n_attr = attraction_count
    if n_attr is None:
        n_attr = max(2, min(days * 2, 8))
    else:
        n_attr = max(0, min(int(n_attr), 20))
    tk_lo, tk_hi = _ATTRACTION_TICKET_CNY
    tickets_low = tk_lo * n_attr
    tickets_high = tk_hi * n_attr
    ticket_note = (
        f"门票按约 {n_attr} 个景点×经验区间 {tk_lo}–{tk_hi} 元/处估算；"
        "无全国官方票价 API。请用景点的 website/tel 或 ticket_verify（携程/美团搜索）核验实时价；"
        "禁止把此区间当作精确成交价。"
    )

    act_low = act_high = 0
    if include_activity_extras:
        act_low, act_high = _ACTIVITY_EXTRA_CNY[0] * days, _ACTIVITY_EXTRA_CNY[1] * days
    activity_note = (
        "沙漠项目/漂流等体验另计：无官方报价 API，仅给宽区间；不确定则写「另计、查官网」。"
        if include_activity_extras
        else "未计入体验加项。"
    )

    total_low = int(lodging_low + food_low + t_low + tickets_low + act_low)
    total_high = int(lodging_high + food_high + t_high + tickets_high + act_high)

    within = None
    if budget_cny is not None:
        within = total_low <= budget_cny
        note_fit = (
            f"用户预算 {budget_cny} 元相对估算区间 [{total_low}, {total_high}]："
            + (
                "可能够用（偏紧）"
                if within and budget_cny < total_high
                else ("偏紧或不够" if not within else "较宽裕")
            )
        )
    else:
        note_fit = "未提供总预算，仅给出区间参考。"

    return {
        "ok": True,
        "city": city,
        "days": days,
        "nights": nights,
        "currency": "CNY",
        "style": tier,
        "total_range": [total_low, total_high],
        "breakdown_range": {
            "lodging": [int(lodging_low), int(lodging_high)],
            "food": [int(food_low), int(food_high)],
            "local_transport": [int(t_low), int(t_high)],
            "tickets_misc": [int(tickets_low), int(tickets_high)],
            "activity_extras": [int(act_low), int(act_high)],
        },
        "transport_legs": transport_parts,
        "transport_distance_m_total": int(total_m),
        "meal_cny_range": [meal_lo, meal_hi],
        "hotel_night_cny_range": [hot_lo, hot_hi],
        "attraction_count_assumed": n_attr,
        "user_budget_cny": budget_cny,
        "within_budget_low_end": within,
        "notes": {
            "transport": transport_note,
            "food": food_note,
            "lodging": lodging_note,
            "tickets": ticket_note,
            "activities": activity_note,
        },
        "disclaimer": (
            "区间估算：交通基于实测路程公式；餐饮/住宿优先用高德人均字段否则启发式；"
            "门票/体验无官方实时价 API。不含城际大交通。禁止把区间写成精确成交价。"
        ),
        "fit_note": note_fit,
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "estimate_budget",
        estimate_budget,
        description=(
            "Budget CNY ranges from route_legs + optional Amap cost hints. "
            'Action Input: {"city":"中卫","days":3,"budget_cny":3000,'
            '"route_legs":[{"from_name":"A","to_name":"B","distance_m":12000}],'
            '"attraction_count":4,'
            '"food_cost_hints":[{"amap_avg_cost":40}],'
            '"hotel_cost_hints":[{"amap_avg_cost":200}]}'
        ),
        permission="low",
    )
