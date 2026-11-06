"""Map total trip budget → spend tier, and filter POIs by 人均 bands."""

from __future__ import annotations

from typing import Any, Literal

BudgetStyle = Literal["budget", "mid", "comfort"]

# Soft caps for ranking/filtering (高德人均参考，非成交价)
_MEAL_MAX_CNY: dict[str, float] = {"budget": 45.0, "mid": 90.0, "comfort": 200.0}
_HOTEL_MAX_CNY: dict[str, float] = {"budget": 200.0, "mid": 400.0, "comfort": 900.0}

_STYLE_ALIASES = {
    "budget": "budget",
    "cheap": "budget",
    "economy": "budget",
    "经济": "budget",
    "穷游": "budget",
    "mid": "mid",
    "medium": "mid",
    "标准": "mid",
    "comfort": "comfort",
    "luxury": "comfort",
    "high": "comfort",
    "舒适": "comfort",
    "豪华": "comfort",
}


def normalize_style(style: str | None) -> BudgetStyle | None:
    if style is None:
        return None
    s = str(style).strip().lower()
    if not s or s in {"auto", "default", ""}:
        return None
    return _STYLE_ALIASES.get(s)  # type: ignore[return-value]


def infer_budget_style(
    budget_cny: int | float | None = None,
    days: int | None = None,
    party_size: int | None = None,
    style: str | None = None,
) -> BudgetStyle:
    """Resolve spend tier.

    Explicit style wins when recognized. Otherwise use per-person daily budget
    (total / days / party), excluding intercity transport.
    """
    explicit = normalize_style(style)
    if explicit:
        return explicit

    if budget_cny is None:
        return "mid"
    try:
        total = float(budget_cny)
    except (TypeError, ValueError):
        return "mid"
    if total <= 0:
        return "mid"

    d = max(1, int(days or 1))
    p = max(1, int(party_size or 1))
    daily = total / d / p
    # Empirically: <400 紧 / 400–999 中等 / ≥1000 宽松（含住+吃+市内+票）
    if daily < 400:
        return "budget"
    if daily >= 1000:
        return "comfort"
    return "mid"


def meal_max_cny(style: BudgetStyle | str) -> float:
    return _MEAL_MAX_CNY.get(normalize_style(style) or "mid", 90.0)


def hotel_max_cny(style: BudgetStyle | str) -> float:
    return _HOTEL_MAX_CNY.get(normalize_style(style) or "mid", 400.0)


def _place_cost(place: dict[str, Any]) -> float | None:
    for key in ("avg_cost_per_person_cny", "amap_avg_cost"):
        val = place.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def rank_places_for_budget(
    places: list[dict[str, Any]],
    *,
    style: BudgetStyle | str,
    max_avg_cost: float | None = None,
    prefer_low_cost: bool | None = None,
) -> list[dict[str, Any]]:
    """Tag budget_fit and sort: in-band first; budget→cheaper, comfort→rating."""
    tier: BudgetStyle = normalize_style(style) or "mid"
    cap = float(max_avg_cost) if max_avg_cost and max_avg_cost > 0 else meal_max_cny(tier)
    if prefer_low_cost is None:
        prefer_low_cost = tier == "budget"

    tagged: list[dict[str, Any]] = []
    for p in places:
        row = dict(p)
        cost = _place_cost(row)
        if cost is None:
            row["budget_fit"] = None  # unknown
            row["budget_style"] = tier
        else:
            row["budget_fit"] = cost <= cap
            row["budget_style"] = tier
            row["budget_cap_cny"] = cap
        tagged.append(row)

    def sort_key(p: dict[str, Any]) -> tuple:
        fit = p.get("budget_fit")
        fit_rank = 0 if fit is True else (1 if fit is None else 2)
        cost = _place_cost(p)
        cost_val = cost if cost is not None else (0.0 if not prefer_low_cost else 10**9)
        rating = -float(p.get("rating") or 0)
        dist = int(p.get("distance_m_from_center") or 10**9)
        if prefer_low_cost:
            return (fit_rank, cost_val, dist, rating)
        return (fit_rank, rating, dist, cost_val if cost is not None else 10**9)

    return sorted(tagged, key=sort_key)


def style_label_zh(style: BudgetStyle | str) -> str:
    return {"budget": "经济", "mid": "标准", "comfort": "舒适"}.get(
        normalize_style(style) or "mid", "标准"
    )
