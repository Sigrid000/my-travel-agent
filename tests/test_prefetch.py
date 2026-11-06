from unittest.mock import AsyncMock

import pytest

from app.agent.loop import ReActLoop
from app.agent.plan_gates import missing_required_plan_tools
from app.agent.prefetch import prefetch_plan_context
from app.config import Settings
from app.llm.client import LLMResponse
from app.memory.short_term import ShortTermMemory
from app.observability.trace import TraceRecorder
from app.tools.builtin.preferences import TravelPreferences
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_prefetch_weather_scenario_without_coords():
    prefs = TravelPreferences(
        city="杭州",
        days=2,
        budget_cny=2000,
        weather_scenario="rainy",
        missing_fields=[],
    )
    reg = ToolRegistry()

    async def geo(**kwargs):
        return {"ok": True, "results": [{"lat": 30.25, "lon": 120.15}]}

    async def weather(**kwargs):
        assert kwargs.get("weather_scenario") == "rainy"
        return {"ok": True, "mode": "scenario", "planning_hint": "室内优先"}

    reg.register("geocode_location", geo, description="g", permission="low")
    reg.register("get_weather", weather, description="w", permission="low")

    out = await prefetch_plan_context(prefs, registry=reg)
    assert out.ok_geocode and out.ok_weather
    assert out.center == {"lat": 30.25, "lon": 120.15}
    tools = [o["tool"] for o in out.observations]
    assert tools == ["geocode_location", "get_weather"]
    assert "get_weather" not in missing_required_plan_tools(out.observations)
    assert "编排预取" in out.context_block


@pytest.mark.asyncio
async def test_prefetch_skips_weather_without_center_or_scenario():
    prefs = TravelPreferences(city="杭州", days=2, budget_cny=2000, start_date="2026-11-22")
    reg = ToolRegistry()

    async def geo_fail(**kwargs):
        return {"ok": False, "error": "nope"}

    reg.register("geocode_location", geo_fail, description="g", permission="low")

    async def weather(**kwargs):
        raise AssertionError("weather must not run")

    reg.register("get_weather", weather, description="w", permission="low")

    out = await prefetch_plan_context(prefs, registry=reg)
    assert out.ok_geocode is False
    assert out.ok_weather is False
    assert [o["tool"] for o in out.observations] == ["geocode_location"]


@pytest.mark.asyncio
async def test_loop_seed_observations_satisfy_weather_gate(tmp_path):
    settings = Settings(openai_api_key="x", trace_dir=str(tmp_path / "t"), max_agent_steps=3)
    reg = ToolRegistry()

    async def _ok(**kwargs):
        return {"ok": True}

    for name in ("search_places", "search_hotels", "estimate_route", "estimate_budget", "suggest_packing"):
        reg.register(name, _ok, description=name, permission="low")

    async def food(**kwargs):
        return {"ok": True, "places": []}

    reg.register("search_food_nearby", food, description="f", permission="low")

    seed = [
        {
            "tool": "get_weather",
            "args": {"weather_scenario": "sunny"},
            "result": {"ok": True, "mode": "scenario"},
            "prefetched": True,
        }
    ]
    assert "get_weather" not in missing_required_plan_tools(seed)

    replies = [
        LLMResponse(content='Action: search_places\nAction Input: {"city":"杭"}', prompt_tokens=1, completion_tokens=1),
        LLMResponse(content="FINAL:\n## 约束摘要\nx\n## 每日行程（含午餐建议）\nd\n## 住宿候选\nh\n## 美食参考（自选）\n人均\n## 门票核验\n携程\n## 分项预算\n预算 住宿 餐饮 交通\n## 建议携带\np\n## 风险与提醒\nr\n## 需确认事项\nc\n## 数据来源\n高德", prompt_tokens=1, completion_tokens=1),
    ]
    i = {"n": 0}

    async def chat(messages, temperature=0.2):
        r = replies[min(i["n"], len(replies) - 1)]
        i["n"] += 1
        return r

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=chat)
    loop = ReActLoop(llm, reg, settings=settings)
    mem = ShortTermMemory(session_id="s")
    trace = TraceRecorder(session_id="s", trace_dir=str(tmp_path / "t"))
    result = await loop.run("go", mem, trace, seed_observations=seed)
    assert any(o.get("tool") == "get_weather" and o.get("prefetched") for o in result.observations)
    # FINAL will still fail other gates; just ensure seed stayed
    assert result.observations[0]["tool"] == "get_weather"
