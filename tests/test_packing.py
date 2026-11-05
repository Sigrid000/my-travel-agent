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


@pytest.mark.asyncio
async def test_packing_rainy():
    out = await suggest_packing(weather_scenario="rainy", days=2)
    assert any("雨" in x for x in out["checklist"])
