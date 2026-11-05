from unittest.mock import AsyncMock, patch

import pytest

from app.agent.destination_suggest import (
    format_destination_suggestions,
    propose_destination_candidates,
)
from app.agent.orchestrator import TravelOrchestrator
from app.config import Settings
from app.llm.client import LLMResponse
from app.tools.builtin.preferences import extract_preferences_with_llm
from app.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_llm_extract_see_sea_as_interest_not_city():
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=LLMResponse(
            content=(
                '{"city":null,"origin_city":null,"days":null,"budget_cny":null,'
                '"interests":["看海"],"companions":null,"party_size":null,'
                '"start_date":null,"end_date":null,"weather_scenario":null}'
            ),
            prompt_tokens=1,
            completion_tokens=1,
        )
    )
    p = await extract_preferences_with_llm(llm, "我想去看海")
    assert p.city is None
    assert "看海" in p.interests


@pytest.mark.asyncio
async def test_propose_verifies_with_nominatim_no_hardcoded_list():
    llm = AsyncMock()
    llm.chat = AsyncMock(
        return_value=LLMResponse(
            content='{"candidates":[{"city":"青岛","reason":"海岸线长","match":"看海"},'
            '{"city":"虚构岛市","reason":"假的","match":"看海"}]}',
            prompt_tokens=5,
            completion_tokens=8,
        )
    )

    async def fake_geocode(query: str = "", **_: object):
        if "青岛" in query:
            return {
                "ok": True,
                "results": [{"display_name": "青岛市", "lat": 36.0, "lon": 120.0}],
            }
        return {"ok": False, "error": "geocode_empty"}

    with patch("app.agent.destination_suggest.geocode_location", new=fake_geocode):
        out = await propose_destination_candidates(llm, interests=["看海"], notes="看海")

    assert out["ok"] is True
    assert [c["city"] for c in out["candidates"]] == ["青岛"]
    assert any(r["city"] == "虚构岛市" for r in out["rejected"])


def test_format_suggestions_asks_user_to_pick():
    text = format_destination_suggestions(
        {
            "ok": True,
            "candidates": [{"city": "厦门", "reason": "沙滩与步行友好"}],
        },
        interests=["看海"],
        still_missing=["city", "days", "budget_cny", "schedule_basis"],
    )
    assert "厦门" in text
    assert "地理编码" in text
    assert "天数" in text


@pytest.mark.asyncio
async def test_orchestrator_suggests_when_only_interest(tmp_path):
    settings = Settings(openai_api_key="test-key", trace_dir=str(tmp_path / "traces"))

    async def chat_side_effect(messages, temperature: float = 0.2):
        system = messages[0]["content"]
        if "目的地推荐" in system:
            return LLMResponse(
                content='{"candidates":[{"city":"青岛","reason":"适合看海","match":"看海"}]}',
                prompt_tokens=1,
                completion_tokens=2,
            )
        return LLMResponse(
            content=(
                '{"city":null,"origin_city":null,"days":null,"budget_cny":null,'
                '"interests":["看海"],"companions":null,"party_size":null,'
                '"start_date":null,"end_date":null,"weather_scenario":null}'
            ),
            prompt_tokens=1,
            completion_tokens=1,
        )

    llm = AsyncMock()
    llm.chat = AsyncMock(side_effect=chat_side_effect)

    async def fake_geocode(query: str = "", **_: object):
        return {
            "ok": True,
            "results": [{"display_name": query, "lat": 36.0, "lon": 120.0}],
        }

    with patch("app.agent.destination_suggest.geocode_location", new=fake_geocode):
        orch = TravelOrchestrator(settings=settings, llm=llm, registry=ToolRegistry())
        r = await orch.handle("我想去看海", session_id="sea1")

    assert r.lane == "suggest"
    assert "青岛" in r.message
    assert r.metadata.get("clarify") is True
    llm.chat.assert_awaited()
