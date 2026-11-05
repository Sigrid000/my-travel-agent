from unittest.mock import AsyncMock

import pytest

from app.agent.orchestrator import TravelOrchestrator
from app.config import Settings
from app.llm.client import LLMResponse
from app.tools.registry import ToolRegistry

_PREFS_HANGZHOU = (
    '{"city":"杭州","origin_city":null,"days":2,"budget_cny":2000,'
    '"interests":["博物馆"],"companions":"solo","party_size":1,'
    '"start_date":null,"end_date":null,"weather_scenario":null}'
)
_PREFS_RAINY = (
    '{"city":null,"origin_city":null,"days":null,"budget_cny":null,'
    '"interests":[],"companions":null,"party_size":null,'
    '"start_date":null,"end_date":null,"weather_scenario":"rainy"}'
)

_GOOD_FINAL = """FINAL:
## 约束摘要
雨天情景杭州
## 每日行程（含午餐建议）
Day1
## 住宿候选
酒店
## 美食参考（自选）
人均 40
## 门票核验
携程 美团
## 分项预算
交通 餐饮 住宿
## 建议携带
雨伞
## 风险与提醒
注意
## 需确认事项
日期
## 数据来源
高德
"""


def _stub_plan_registry() -> ToolRegistry:
    reg = ToolRegistry()

    async def _ok(**kwargs):
        return {"ok": True, "stub": True, **kwargs}

    async def _route(**kwargs):
        return {"ok": True, "distance_m": 5000, "taxi_cny_range": [12, 25]}

    async def _food(**kwargs):
        return {
            "ok": True,
            "places": [{"name": "店", "avg_cost_per_person_cny": 40, "amap_avg_cost": 40}],
        }

    for name in (
        "search_places",
        "search_hotels",
        "get_weather",
        "estimate_budget",
        "suggest_packing",
        "geocode_location",
    ):
        reg.register(name, _ok, description=name, permission="low")
    reg.register("estimate_route", _route, description="estimate_route", permission="low")
    reg.register("search_food_nearby", _food, description="search_food_nearby", permission="low")
    return reg


@pytest.mark.asyncio
async def test_clarify_schedule_before_planning_loop(tmp_path):
    settings = Settings(openai_api_key="test-key", trace_dir=str(tmp_path / "traces"))
    llm = AsyncMock()
    llm.chat = AsyncMock(return_value=LLMResponse(content=_PREFS_HANGZHOU, prompt_tokens=1, completion_tokens=1))
    orch = TravelOrchestrator(settings=settings, llm=llm)
    r = await orch.handle("去杭州玩2天，预算2000，喜欢博物馆", session_id="s1")
    assert r.metadata.get("clarify") is True
    assert "晴天" in r.message or "日期" in r.message
    assert llm.chat.await_count == 1


@pytest.mark.asyncio
async def test_plan_after_weather_scenario(tmp_path):
    settings = Settings(
        openai_api_key="test-key",
        trace_dir=str(tmp_path / "traces"),
        max_agent_steps=20,
        max_tool_calls=30,
    )
    reg = _stub_plan_registry()
    plan_replies = [
        LLMResponse(content='Action: search_places\nAction Input: {"city":"杭州"}', prompt_tokens=1, completion_tokens=1),
        LLMResponse(
            content='Action: search_food_nearby\nAction Input: {"lat":30.2,"lon":120.1,"meal":"lunch"}',
            prompt_tokens=1,
            completion_tokens=1,
        ),
        LLMResponse(
            content='Action: search_food_nearby\nAction Input: {"city":"杭州","lat":30.2,"lon":120.1,"meal":"dinner"}',
            prompt_tokens=1,
            completion_tokens=1,
        ),
        LLMResponse(content='Action: search_hotels\nAction Input: {"city":"杭州"}', prompt_tokens=1, completion_tokens=1),
        LLMResponse(
            content='Action: get_weather\nAction Input: {"lat":30.2,"lon":120.1,"weather_scenario":"rainy"}',
            prompt_tokens=1,
            completion_tokens=1,
        ),
        LLMResponse(
            content='Action: estimate_route\nAction Input: {"from_lat":30.2,"from_lon":120.1,"to_lat":30.25,"to_lon":120.2}',
            prompt_tokens=1,
            completion_tokens=1,
        ),
        LLMResponse(
            content=(
                "Action: estimate_budget\nAction Input: "
                '{"city":"杭州","days":2,"budget_cny":2000,'
                '"route_legs":[{"from_name":"A","to_name":"B","distance_m":5000}],'
                '"food_cost_hints":[{"amap_avg_cost":40}]}'
            ),
            prompt_tokens=1,
            completion_tokens=1,
        ),
        LLMResponse(
            content='Action: suggest_packing\nAction Input: {"city":"杭州","days":2,"weather_scenario":"rainy"}',
            prompt_tokens=1,
            completion_tokens=1,
        ),
        LLMResponse(content=_GOOD_FINAL, prompt_tokens=10, completion_tokens=20),
    ]
    plan_i = {"n": 0}

    async def chat_side_effect(messages, temperature: float = 0.2):
        system = messages[0]["content"]
        user = messages[-1]["content"]
        if "结构化字段" in system:
            if "雨" in user:
                return LLMResponse(content=_PREFS_RAINY, prompt_tokens=1, completion_tokens=1)
            return LLMResponse(content=_PREFS_HANGZHOU, prompt_tokens=1, completion_tokens=1)
        reply = plan_replies[min(plan_i["n"], len(plan_replies) - 1)]
        plan_i["n"] += 1
        return reply

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=chat_side_effect)
    orch = TravelOrchestrator(settings=settings, llm=llm, registry=reg)
    sid = "s-plan"
    r1 = await orch.handle("去杭州玩2天，预算2000，喜欢博物馆", session_id=sid)
    assert r1.metadata.get("clarify")
    r2 = await orch.handle("按雨天", session_id=sid)
    assert "杭州" in r2.message or "约束摘要" in r2.message
    assert r2.trace.get("trace_id")
    assert (tmp_path / "traces").exists()
    tools = [o["tool"] for o in (r2.metadata.get("observations") or [])]
    assert "estimate_route" in tools
    assert "estimate_budget" in tools
    assert "suggest_packing" in tools


@pytest.mark.asyncio
async def test_safety_fast_lane(tmp_path):
    settings = Settings(openai_api_key="test-key", trace_dir=str(tmp_path / "traces"))
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=AssertionError("LLM must not run on safety block"))
    orch = TravelOrchestrator(settings=settings, llm=llm)
    r = await orch.handle("请帮我自动付款下单")
    assert r.blocked
    assert "付款" in r.message or "下单" in r.message
    llm.chat.assert_not_awaited()
