import pytest

from app.tools.builtin.budget import estimate_budget


@pytest.mark.asyncio
async def test_budget_uses_route_legs_for_transport():
    out = await estimate_budget(
        city="中卫",
        days=3,
        budget_cny=3000,
        route_legs=[
            {"from_name": "沙坡头", "to_name": "黄河区", "distance_m": 23000},
            {"from_name": "黄河区", "to_name": "金沙岛", "distance_m": 17000},
        ],
        attraction_count=5,
        food_cost_hints=[{"amap_avg_cost": 45}],
        hotel_cost_hints=[{"amap_avg_cost": 220}],
    )
    assert out["ok"] is True
    assert len(out["transport_legs"]) == 2
    assert out["breakdown_range"]["local_transport"][0] > 0
    assert out["breakdown_range"]["food"][0] > 0
    assert "门票" in out["notes"]["tickets"]
    assert out["total_range"][0] <= out["total_range"][1]


@pytest.mark.asyncio
async def test_budget_without_legs_still_ok_but_notes_weak_transport():
    out = await estimate_budget(city="杭州", days=2)
    assert out["ok"] is True
    assert "route_legs" in out["notes"]["transport"]
