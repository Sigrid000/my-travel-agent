from unittest.mock import AsyncMock

import pytest

from app.agent.loop import ReActLoop, missing_required_plan_tools
from app.config import Settings
from app.llm.client import LLMResponse
from app.memory.short_term import ShortTermMemory
from app.observability.trace import TraceRecorder
from app.tools.registry import ToolRegistry

_REQUIRED = (
    "search_places",
    "search_food_nearby",
    "search_hotels",
    "get_weather",
    "estimate_route",
    "estimate_budget",
    "suggest_packing",
)

_GOOD_FINAL = """FINAL:
## 约束摘要
中卫3天
## 每日行程（含午餐建议）
Day1
## 住宿候选
酒店A
## 美食参考（自选）
人均 58
## 门票核验
携程 美团
## 分项预算
交通 餐饮 住宿
## 建议携带
防晒
## 风险与提醒
注意
## 需确认事项
日期
## 数据来源
高德
"""


def test_missing_required_plan_tools_detects_gaps():
    assert missing_required_plan_tools([]) == list(_REQUIRED)
    full = [{"tool": name} for name in _REQUIRED]
    assert missing_required_plan_tools(full) == []


@pytest.mark.asyncio
async def test_premature_final_rejected_then_accepted(tmp_path):
    settings = Settings(
        openai_api_key="test-key",
        trace_dir=str(tmp_path / "traces"),
        max_agent_steps=20,
        max_tool_calls=30,
    )
    reg = ToolRegistry()

    async def _ok(**kwargs):
        return {"ok": True, **kwargs}

    async def _route(**kwargs):
        return {"ok": True, "distance_m": 9000, "taxi_cny_range": [20, 40]}

    async def _food(**kwargs):
        return {
            "ok": True,
            "places": [{"name": "店", "avg_cost_per_person_cny": 40, "amap_avg_cost": 40}],
        }

    for name in _REQUIRED:
        if name == "estimate_route":
            reg.register(name, _route, description=name, permission="low")
        elif name == "search_food_nearby":
            reg.register(name, _food, description=name, permission="low")
        else:
            reg.register(name, _ok, description=name, permission="low")

    replies = [
        LLMResponse(content="FINAL:\n# 偷懒行程\n没调工具", prompt_tokens=1, completion_tokens=1),
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
            content='Action: get_weather\nAction Input: {"weather_scenario":"rainy"}',
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
                '{"city":"杭州","days":2,"route_legs":[{"from_name":"A","to_name":"B","distance_m":9000}],'
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
        LLMResponse(content=_GOOD_FINAL, prompt_tokens=1, completion_tokens=1),
    ]
    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=replies)

    loop = ReActLoop(llm, reg, settings=settings)
    mem = ShortTermMemory(session_id="t1")
    trace = TraceRecorder(session_id="t1", trace_dir=str(tmp_path / "traces"))
    result = await loop.run("已确认约束，去杭州", mem, trace)

    assert result.rejected_final >= 1
    assert "约束摘要" in result.final_text
    assert "偷懒" not in result.final_text
    tools = [o["tool"] for o in result.observations]
    assert "suggest_packing" in tools
    assert tools.count("search_food_nearby") >= 2


@pytest.mark.asyncio
async def test_max_steps_without_tools_reports_missing(tmp_path):
    settings = Settings(
        openai_api_key="test-key",
        trace_dir=str(tmp_path / "traces"),
        max_agent_steps=2,
        max_tool_calls=30,
    )
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=LLMResponse(content="FINAL:\n假完整行程", prompt_tokens=1, completion_tokens=1)
    )
    loop = ReActLoop(llm, ToolRegistry(), settings=settings)
    mem = ShortTermMemory(session_id="t2")
    trace = TraceRecorder(session_id="t2", trace_dir=str(tmp_path / "traces"))
    result = await loop.run("去杭州", mem, trace)
    assert "校验未通过" in result.final_text or "仍缺" in result.final_text
    assert result.rejected_final >= 1
