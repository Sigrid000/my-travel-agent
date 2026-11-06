import pytest

from app.tools.builtin.packing import suggest_packing


@pytest.mark.asyncio
async def test_packing_desert_sunny():
    out = await suggest_packing(
        city="中卫",
        days=3,
        weather_scenario="sunny",
        interests=["沙漠", "黄河"],
        temp_max_c=28,
        temp_min_c=8,
    )
    assert out["ok"] is True
    text = " ".join(out["checklist"])
    assert "防晒" in text
    assert "防风沙" in text or "口罩" in text
    assert "分层" in text or "温差" in text
    assert out["sections"]["衣物参考"]
    assert out["sections"]["生活用品参考"]


@pytest.mark.asyncio
async def test_packing_rainy():
    out = await suggest_packing(weather_scenario="rainy", days=2)
    assert any("雨" in x for x in out["checklist"])


@pytest.mark.asyncio
async def test_packing_high_altitude_by_city():
    out = await suggest_packing(
        city="拉萨",
        days=4,
        weather_scenario="sunny",
        interests=["布达拉宫"],
        temp_max_c=18,
        temp_min_c=5,
    )
    assert out["high_altitude"] is True
    alerts = " ".join(out["packing"]["alerts"])
    assert "氧气" in alerts
    assert "红景天" in alerts
    assert "非医疗" in alerts or "咨询医生" in alerts


@pytest.mark.asyncio
async def test_packing_high_altitude_by_meters():
    out = await suggest_packing(
        city="某处",
        days=2,
        altitude_m=3200,
        interests=["徒步"],
    )
    assert out["high_altitude"] is True
    assert any("氧气" in x for x in out["checklist"])
