import pytest
from unittest.mock import AsyncMock

from app.llm.client import LLMResponse
from app.tools.builtin.preferences import (
    empty_preferences,
    extract_preferences_with_llm,
    merge_preferences,
    pref_from_llm_dict,
)


def _llm(content: str) -> AsyncMock:
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=LLMResponse(content=content, prompt_tokens=1, completion_tokens=1)
    )
    return llm


@pytest.mark.asyncio
async def test_llm_parse_full_still_needs_schedule_basis():
    llm = _llm(
        '{"city":"杭州","origin_city":null,"days":2,"budget_cny":2000,'
        '"interests":["博物馆"],"companions":"solo","party_size":1,'
        '"start_date":null,"end_date":null,"weather_scenario":null}'
    )
    p = await extract_preferences_with_llm(llm, "去杭州玩2天，预算2000，喜欢博物馆，一个人")
    assert p.city == "杭州"
    assert p.days == 2
    assert p.budget_cny == 2000
    assert "博物馆" in p.interests
    assert p.party_size == 1
    assert "schedule_basis" in p.missing_fields


@pytest.mark.asyncio
async def test_llm_parse_with_dates_complete():
    llm = _llm(
        '{"city":"杭州","days":2,"budget_cny":2000,"interests":[],'
        '"start_date":"2026-11-05","end_date":"2026-11-06","weather_scenario":null,'
        '"origin_city":null,"companions":null,"party_size":null}'
    )
    p = await extract_preferences_with_llm(llm, "去杭州玩2天，预算2000，11月5日到6日")
    assert p.city == "杭州"
    assert p.start_date == "2026-11-05"
    assert p.missing_fields == []


@pytest.mark.asyncio
async def test_llm_parse_weather_scenario():
    llm = _llm(
        '{"city":"北京","days":3,"budget_cny":3000,"interests":[],'
        '"weather_scenario":"rainy","start_date":null,"end_date":null,'
        '"origin_city":null,"companions":null,"party_size":null}'
    )
    p = await extract_preferences_with_llm(llm, "去北京玩3天预算3000按雨天规划")
    assert p.city == "北京"
    assert p.weather_scenario == "rainy"
    assert p.missing_fields == []


def test_infer_weather_from_notes_when_llm_null():
    p = pref_from_llm_dict(
        {
            "city": "中卫",
            "days": 3,
            "budget_cny": 3000,
            "interests": ["沙漠"],
            "weather_scenario": None,
        },
        notes="去中卫玩3天，预算3000，按晴天情景规划",
    )
    assert p.weather_scenario == "sunny"
    assert p.missing_fields == []


@pytest.mark.asyncio
async def test_merge_followup_rainy():
    base = pref_from_llm_dict(
        {
            "city": "杭州",
            "days": 2,
            "budget_cny": 2000,
            "interests": ["博物馆"],
        },
        notes="base",
    )
    assert "schedule_basis" in base.missing_fields
    delta = await extract_preferences_with_llm(
        _llm(
            '{"city":null,"days":null,"budget_cny":null,"interests":[],'
            '"weather_scenario":"rainy","start_date":null,"end_date":null,'
            '"origin_city":null,"companions":null,"party_size":null}'
        ),
        "按雨天吧",
    )
    merged = merge_preferences(base, delta)
    assert merged.city == "杭州"
    assert merged.weather_scenario == "rainy"
    assert merged.missing_fields == []


@pytest.mark.asyncio
async def test_llm_parse_missing_budget():
    llm = _llm(
        '{"city":"北京","days":3,"budget_cny":null,"interests":["美食"],'
        '"start_date":null,"end_date":null,"weather_scenario":null,'
        '"origin_city":null,"companions":null,"party_size":null}'
    )
    p = await extract_preferences_with_llm(llm, "去北京玩3天，喜欢美食")
    assert p.city == "北京"
    assert "budget_cny" in p.missing_fields


@pytest.mark.asyncio
async def test_llm_parse_garbage_no_city():
    llm = _llm(
        '{"city":null,"days":null,"budget_cny":null,"interests":[],'
        '"start_date":null,"end_date":null,"weather_scenario":null,'
        '"origin_city":null,"companions":null,"party_size":null}'
    )
    p = await extract_preferences_with_llm(llm, "嗯嗯随便看看")
    assert "city" in p.missing_fields


@pytest.mark.asyncio
async def test_llm_parse_any_city_name():
    llm = _llm(
        '{"city":"赣州","days":3,"budget_cny":1500,"interests":["古镇"],'
        '"start_date":null,"end_date":null,"weather_scenario":null,'
        '"origin_city":null,"companions":null,"party_size":null}'
    )
    p = await extract_preferences_with_llm(llm, "去赣州玩3天，预算1500，喜欢古镇")
    assert p.city == "赣州"


@pytest.mark.asyncio
async def test_llm_zhongwei_from_nanjing():
    llm = _llm(
        '{"city":"中卫","origin_city":"南京","days":3,"budget_cny":4000,'
        '"interests":["看星星","滑沙","美食"],"companions":null,"party_size":2,'
        '"start_date":"2026-11-25","end_date":"2026-11-27","weather_scenario":null}'
    )
    p = await extract_preferences_with_llm(
        llm,
        "我从南京出发，2个人，总体预算4000，去中卫看星星，计划11月25号到27号，"
        "包括机票高铁美食酒店景点都帮我规划好，我对看星星和滑沙最感兴趣，想吃这边的羊肉据说很好吃",
    )
    assert p.city == "中卫"
    assert p.origin_city == "南京"
    assert p.party_size == 2
    assert p.budget_cny == 4000
    assert p.days == 3
    assert p.missing_fields == []


def test_empty_preferences_all_missing():
    p = empty_preferences("x")
    assert "city" in p.missing_fields
