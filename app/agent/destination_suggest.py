from __future__ import annotations

import json
import re
from typing import Any

from app.llm.client import LLMClient
from app.tools.builtin.geo import geocode_location

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None


async def propose_destination_candidates(
    llm: LLMClient,
    *,
    interests: list[str],
    notes: str = "",
    origin_city: str | None = None,
    days: int | None = None,
    budget_cny: int | None = None,
    limit: int = 3,
) -> dict[str, Any]:
    """LLM proposes cities from interests; geocode_location must verify each — no hardcoded catalog."""
    limit = max(1, min(int(limit or 3), 5))
    interest_text = "、".join(interests) if interests else "未标注"
    user_blob = {
        "interests": interests,
        "notes": notes,
        "origin_city": origin_city,
        "days": days,
        "budget_cny": budget_cny,
        "limit": limit,
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是休闲旅行目的地推荐助手。根据用户兴趣提出中国境内可选目的地城市（短名，如青岛、厦门）。"
                "不要写死只推荐固定名单；按兴趣与约束多样选择。"
                "不要编造精确票价、不要声称已预订。"
                "只输出 JSON："
                '{"candidates":[{"city":"城市短名","reason":"一句话理由","match":"与兴趣的对应点"}]}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"兴趣：{interest_text}\n"
                f"约束 JSON：{json.dumps(user_blob, ensure_ascii=False)}\n"
                f"请给出至多 {limit} 个候选城市。"
            ),
        },
    ]
    resp = await llm.chat(messages, temperature=0.3)
    parsed = _extract_json(resp.content)
    if not parsed:
        return {
            "ok": False,
            "error": "suggest_parse_failed",
            "raw_preview": (resp.content or "")[:300],
            "candidates": [],
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
        }

    raw_cands = parsed.get("candidates") or []
    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in raw_cands:
        if not isinstance(item, dict):
            continue
        city = str(item.get("city") or "").strip()
        if not city:
            continue
        geo = await geocode_location(city)
        if not geo.get("ok") or not geo.get("results"):
            rejected.append({"city": city, "reason": "geocode_failed", "detail": geo})
            continue
        top = geo["results"][0]
        verified.append(
            {
                "city": city,
                "reason": str(item.get("reason") or "").strip(),
                "match": str(item.get("match") or "").strip(),
                "lat": top.get("lat"),
                "lon": top.get("lon"),
                "display_name": top.get("display_name"),
                "source": "llm_propose+nominatim_verify",
            }
        )
        if len(verified) >= limit:
            break

    if not verified:
        return {
            "ok": False,
            "error": "no_verified_candidates",
            "candidates": [],
            "rejected": rejected,
            "prompt_tokens": resp.prompt_tokens,
            "completion_tokens": resp.completion_tokens,
        }

    return {
        "ok": True,
        "candidates": verified,
        "rejected": rejected,
        "prompt_tokens": resp.prompt_tokens,
        "completion_tokens": resp.completion_tokens,
        "note": "候选由模型提出，并经地理编码核实；具体景点请选定城市后再 search_places。",
    }


def format_destination_suggestions(
    result: dict[str, Any],
    *,
    interests: list[str],
    still_missing: list[str],
) -> str:
    labels = {
        "days": "天数",
        "budget_cny": "预算",
        "schedule_basis": "出行日期或天气情景（晴/阴/雨）",
        "city": "目的地城市",
    }
    interest_text = "、".join(interests) if interests else "你的描述"
    if not result.get("ok") or not result.get("candidates"):
        extra = [labels.get(x, x) for x in still_missing if x != "city"]
        bits = ["目的地城市"] + extra
        return (
            f"根据「{interest_text}」暂未能核实可用目的地推荐（地图编码失败或解析失败）。\n"
            "请直接回复一个目的地城市名"
            + ("；并补充：" + "、".join(extra) if extra else "")
            + "。"
        )

    lines = [f"你还没定城市。根据兴趣「{interest_text}」，先推荐几个可选目的地（已用地理编码核实存在）："]
    for i, c in enumerate(result["candidates"], 1):
        reason = c.get("reason") or c.get("match") or ""
        lines.append(f"{i}) {c['city']}" + (f"：{reason}" if reason else ""))
    lines.append("请回复其中一个城市名（或你更想去的其他城市）。")
    other = [labels.get(x, x) for x in still_missing if x != "city"]
    if other:
        lines.append("选定城市后，还请补充：" + "、".join(other) + "。")
    lines.append("选定前不会按错误城市生成行程；具体景点会在你确认城市后用地图工具查询。")
    return "\n".join(lines)
