import pytest

from app.agent.budget_adjust import analyze_budget_gap
from app.tools.builtin.budget import estimate_budget


def _obs_budget(result: dict, args: dict | None = None) -> list[dict]:
    return [
        {
            "tool": "estimate_budget",
            "args": args or {"budget_cny": result.get("user_budget_cny"), "days": 2},
            "result": result,
        }
    ]


@pytest.mark.asyncio
async def test_analyze_over_budget():
    out = await estimate_budget(
        city="杭州",
        days=2,
        budget_cny=200,
        style="comfort",
        route_legs=[{"from_name": "A", "to_name": "B", "distance_m": 20000}],
        attraction_count=6,
        food_cost_hints=[{"amap_avg_cost": 120}],
        hotel_cost_hints=[{"amap_avg_cost": 600}],
    )
    # Force over by tiny budget regardless of style bands
    out["user_budget_cny"] = 200
    out["total_range"] = [800, 2000]
    out["within_budget_low_end"] = False
    adj = analyze_budget_gap(_obs_budget(out))
    assert adj is not None
    assert adj.kind == "over"
    assert adj.target_style == "budget"
    assert adj.transport_mode == "public"
    assert "公交" in adj.nudge_message() or "public" in adj.nudge_message()


@pytest.mark.asyncio
async def test_analyze_under_budget():
    out = await estimate_budget(city="杭州", days=2, budget_cny=500, style="budget")
    out["user_budget_cny"] = 20000
    out["total_range"] = [500, 1200]
    out["within_budget_low_end"] = True
    adj = analyze_budget_gap(_obs_budget(out, {"budget_cny": 20000, "days": 2}))
    assert adj is not None
    assert adj.kind == "under"
    assert adj.target_style == "comfort"


@pytest.mark.asyncio
async def test_public_transport_cheaper_than_taxi():
    legs = [{"from_name": "A", "to_name": "B", "distance_m": 15000}]
    taxi = await estimate_budget(city="X", days=2, budget_cny=3000, route_legs=legs, transport_mode="taxi")
    bus = await estimate_budget(city="X", days=2, budget_cny=3000, route_legs=legs, transport_mode="public")
    assert bus["breakdown_range"]["local_transport"][1] < taxi["breakdown_range"]["local_transport"][1]
    assert bus["transport_mode"] == "public"


def test_no_adjust_when_fit_ok():
    obs = _obs_budget(
        {
            "ok": True,
            "user_budget_cny": 3000,
            "total_range": [1200, 2800],
            "within_budget_low_end": True,
        }
    )
    assert analyze_budget_gap(obs) is None
