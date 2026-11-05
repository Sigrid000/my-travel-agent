from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry

WeatherScenario = Literal["sunny", "cloudy", "rainy"]

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


class TravelPreferences(BaseModel):
    city: str | None = None  # destination
    origin_city: str | None = None
    days: int | None = Field(default=None, ge=1, le=14)
    budget_cny: int | None = Field(default=None, ge=0)
    interests: list[str] = Field(default_factory=list)
    companions: str | None = None
    party_size: int | None = Field(default=None, ge=1, le=20)
    start_date: str | None = None  # YYYY-MM-DD
    end_date: str | None = None
    weather_scenario: WeatherScenario | None = None
    notes: str = ""
    missing_fields: list[str] = Field(default_factory=list)
    summary: str = ""


SCHEDULE_ASK = (
    "已记录行程天数。还请补充「时间怎么定」之一即可：\n"
    "1) 指定日期（例：11月5日–6日，或 2026-11-05 起 2 天）\n"
    "2) 不指定日期，按天气情景试规划——回复「晴天」「阴天」或「雨天」其一\n"
    "（情景模式按该类天气安排室内/室外节奏，不是保证真实那天一定是这种天气。）"
)


def _clean_place(name: str | None) -> str | None:
    if not name:
        return None
    s = str(name).strip().strip("的了呢啊呀")
    for suf in ("市", "地区", "自治州", "盟"):
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
            break
    return s or None


def _fill_end_from_days(pref: TravelPreferences) -> None:
    if pref.start_date and not pref.end_date and pref.days:
        try:
            start = date.fromisoformat(pref.start_date)
            pref.end_date = (start + timedelta(days=max(pref.days - 1, 0))).isoformat()
        except ValueError:
            pass
    if pref.start_date and pref.end_date and not pref.days:
        try:
            start = date.fromisoformat(pref.start_date)
            end = date.fromisoformat(pref.end_date)
            pref.days = max((end - start).days + 1, 1)
        except ValueError:
            pass


def recompute_missing_and_summary(pref: TravelPreferences) -> TravelPreferences:
    missing: list[str] = []
    if not pref.city:
        missing.append("city")
    if not pref.days:
        missing.append("days")
    if pref.budget_cny is None:
        missing.append("budget_cny")
    if not pref.start_date and not pref.weather_scenario:
        missing.append("schedule_basis")
    pref.missing_fields = missing

    bits: list[str] = []
    if pref.origin_city:
        bits.append(f"出发 {pref.origin_city}")
    if pref.city:
        bits.append(f"目的地 {pref.city}")
    if pref.days:
        bits.append(f"{pref.days} 天")
    if pref.start_date:
        bits.append(f"日期 {pref.start_date}" + (f"～{pref.end_date}" if pref.end_date else ""))
    if pref.weather_scenario:
        label = {"sunny": "晴天", "cloudy": "阴天", "rainy": "雨天"}[pref.weather_scenario]
        bits.append(f"天气情景：{label}")
    if pref.budget_cny is not None:
        bits.append(f"预算约 {pref.budget_cny} 元")
    if pref.party_size:
        bits.append(f"{pref.party_size} 人")
    if pref.interests:
        bits.append("兴趣：" + "、".join(pref.interests))
    if pref.companions:
        bits.append(f"同行：{pref.companions}")
    if missing:
        bits.append("待补充：" + "、".join(missing))
    pref.summary = "；".join(bits) if bits else "未能解析出有效约束"
    return pref


def empty_preferences(notes: str = "") -> TravelPreferences:
    return recompute_missing_and_summary(TravelPreferences(notes=notes or ""))


def _infer_weather_scenario(text: str, weather: str | None) -> str | None:
    """Fallback when model misses 按晴天/雨天情景 etc."""
    if weather in ("sunny", "cloudy", "rainy"):
        return weather
    t = text or ""
    if any(k in t for k in ("雨天", "下雨", "按雨")):
        return "rainy"
    if any(k in t for k in ("阴天", "按阴")):
        return "cloudy"
    if any(k in t for k in ("晴天", "按晴", "晴天情景")):
        return "sunny"
    return None


def pref_from_llm_dict(data: dict[str, Any], notes: str) -> TravelPreferences:
    weather = data.get("weather_scenario")
    if weather not in (None, "sunny", "cloudy", "rainy"):
        weather = None
    weather = _infer_weather_scenario(notes, weather)
    interests = data.get("interests") or []
    if isinstance(interests, str):
        interests = [interests]
    interests = [str(x).strip() for x in interests if str(x).strip()]

    days = data.get("days")
    try:
        days = int(days) if days is not None else None
    except (TypeError, ValueError):
        days = None
    if days is not None:
        days = max(1, min(days, 14))

    budget = data.get("budget_cny")
    try:
        budget = int(budget) if budget is not None else None
    except (TypeError, ValueError):
        budget = None

    party = data.get("party_size")
    try:
        party = int(party) if party is not None else None
    except (TypeError, ValueError):
        party = None

    pref = TravelPreferences(
        city=_clean_place(data.get("city")),
        origin_city=_clean_place(data.get("origin_city")),
        days=days,
        budget_cny=budget,
        interests=list(dict.fromkeys(interests)),
        companions=(str(data["companions"]).strip() if data.get("companions") else None),
        party_size=party,
        start_date=data.get("start_date") or None,
        end_date=data.get("end_date") or None,
        weather_scenario=weather,
        notes=notes,
    )
    _fill_end_from_days(pref)
    return recompute_missing_and_summary(pref)


async def extract_preferences_with_llm(llm: Any, text: str) -> TravelPreferences:
    """All preference slots from LLM — no regex field extraction."""
    raw = (text or "").strip()
    if not raw:
        return empty_preferences("")

    messages = [
        {
            "role": "system",
            "content": (
                "从用户旅行需求中抽取结构化字段。不要编造用户未提及的城市或预算数字。"
                f"今天是 {date.today().isoformat()}。"
                "city 只能是地名；看海/看星星/滑沙/滑雪/赏花等是 interests，不是 city。"
                "未指定城市时 city 必须为 null。用户只回「青岛」这类短地名时，city 取该名。"
                "用户说晴天/阴天/雨天、按晴天/按雨天、晴天情景规划时，weather_scenario 填 sunny|cloudy|rainy。"
                "日期转为 YYYY-MM-DD；只有月日时：若该月日在今天之后用今年，否则用明年。"
                "只输出 JSON："
                '{"city":null或字符串,"origin_city":null或字符串,"days":null或1-14整数,'
                '"budget_cny":null或整数,"interests":[],"companions":null或字符串,'
                '"party_size":null或整数,"start_date":null或YYYY-MM-DD,'
                '"end_date":null或YYYY-MM-DD,"weather_scenario":null或sunny|cloudy|rainy}'
            ),
        },
        {"role": "user", "content": raw},
    ]
    content = ""
    try:
        resp = await llm.chat(messages, temperature=0.0)
        content = (resp.content or "").strip()
        data: Any = json.loads(content)
    except Exception:
        block = _JSON_BLOCK.search(content)
        if not block:
            return empty_preferences(raw)
        try:
            data = json.loads(block.group(0))
        except Exception:
            return empty_preferences(raw)

    if not isinstance(data, dict):
        return empty_preferences(raw)
    return pref_from_llm_dict(data, notes=raw)


def merge_preferences(base: TravelPreferences | dict[str, Any] | None, delta: TravelPreferences) -> TravelPreferences:
    """Merge a new parse onto session prefs; non-empty delta fields win."""
    if base is None:
        return delta
    if isinstance(base, dict):
        cur = TravelPreferences.model_validate(base)
    else:
        cur = base.model_copy(deep=True)

    for key in (
        "city",
        "origin_city",
        "days",
        "budget_cny",
        "companions",
        "party_size",
        "start_date",
        "end_date",
        "weather_scenario",
    ):
        val = getattr(delta, key)
        if val is not None and val != "" and val != []:
            setattr(cur, key, val)
    if delta.interests:
        cur.interests = list(dict.fromkeys([*(cur.interests or []), *delta.interests]))
    if delta.notes:
        cur.notes = (cur.notes + " | " + delta.notes).strip(" |") if cur.notes else delta.notes
    _fill_end_from_days(cur)
    return recompute_missing_and_summary(cur)


def ask_message_for(pref: TravelPreferences) -> str:
    if "schedule_basis" in pref.missing_fields and pref.city and pref.days and pref.budget_cny is not None:
        return SCHEDULE_ASK
    if pref.missing_fields:
        labels = {
            "city": "目的地城市",
            "days": "天数",
            "budget_cny": "预算",
            "schedule_basis": "出行日期或天气情景（晴/阴/雨）",
        }
        shown = [labels.get(x, x) for x in pref.missing_fields]
        return "请补充：" + "、".join(shown) + "（补充一次即可）。\n若已有天数但未定日期，也可直接回复「晴天」「阴天」或「雨天」做情景规划。"
    return ""


async def collect_preferences(text: str = "", **_: Any) -> dict[str, Any]:
    """
    Tool stub: preference extraction runs in the orchestrator via LLM.
    Calling this alone without LLM cannot fully parse free text.
    """
    pref = empty_preferences(text)
    return {
        "ok": True,
        "preferences": pref.model_dump(),
        "ask_once": ask_message_for(pref),
        "complete": False,
        "note": "完整偏好解析由编排层 LLM 抽槽完成；请在对话中直接说明城市/天数/预算等。",
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "collect_preferences",
        collect_preferences,
        description=(
            "Preference checklist helper. Full NL parsing is done by the orchestrator LLM; "
            "prefer stating constraints in chat."
        ),
        permission="low",
        json_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
