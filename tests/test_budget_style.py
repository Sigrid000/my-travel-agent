import pytest

from app.tools.builtin.budget import estimate_budget
from app.tools.builtin.budget_style import (
    infer_budget_style,
    rank_places_for_budget,
    style_label_zh,
)
from app.tools.builtin.preferences import TravelPreferences, recompute_missing_and_summary


def test_infer_style_from_daily_budget():
    assert infer_budget_style(budget_cny=300, days=1) == "budget"
    assert infer_budget_style(budget_cny=600, days=2) == "budget"  # 300/day
    assert infer_budget_style(budget_cny=800, days=2) == "mid"  # 400/day
    assert infer_budget_style(budget_cny=1500, days=2) == "mid"  # 750/day
    assert infer_budget_style(budget_cny=15000, days=2) == "comfort"  # 7500/day
    assert infer_budget_style(budget_cny=3000, days=3, style="comfort") == "comfort"
    assert style_label_zh("budget") == "经济"


def test_rank_places_prefers_in_band_for_budget():
    places = [
        {"name": "贵", "avg_cost_per_person_cny": 120, "rating": "4.9"},
        {"name": "便宜", "avg_cost_per_person_cny": 30, "rating": "4.0"},
        {"name": "未知", "rating": "5.0"},
    ]
    ranked = rank_places_for_budget(places, style="budget")
    assert ranked[0]["name"] == "便宜"
    assert ranked[0]["budget_fit"] is True
    assert ranked[1]["name"] == "未知"
    assert ranked[2]["budget_fit"] is False


@pytest.mark.asyncio
async def test_estimate_budget_auto_style_differs_tight_vs_loose():
    tight = await estimate_budget(city="杭州", days=2, budget_cny=600)
    loose = await estimate_budget(city="杭州", days=2, budget_cny=15000)
    assert tight["style"] == "budget"
    assert loose["style"] == "comfort"
    assert tight["meal_cny_range"][1] < loose["meal_cny_range"][1]
    assert tight["hotel_night_cny_range"][1] < loose["hotel_night_cny_range"][1]
    assert "经济" in tight["fit_note"]
    assert "舒适" in loose["fit_note"]


def test_preferences_attach_budget_style():
    pref = recompute_missing_and_summary(
        TravelPreferences(city="成都", days=2, budget_cny=1500, weather_scenario="sunny")
    )
    assert pref.budget_style == "mid"
    assert "消费档位" in pref.summary
