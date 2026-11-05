from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from app.config import get_settings
from app.tools.http_retry import once_with_retry
from app.tools.registry import ToolRegistry

_SCENARIO_HINTS = {
    "sunny": {
        "label": "晴天",
        "suggest_indoor": False,
        "hint": "偏户外景点、步行与湖边；注意防晒与补水。",
    },
    "cloudy": {
        "label": "阴天",
        "suggest_indoor": False,
        "hint": "户外仍可，强度适中；备薄外套，室内外搭配。",
    },
    "rainy": {
        "label": "雨天",
        "suggest_indoor": True,
        "hint": "优先博物馆/商场/室内场馆；缩短步行，备雨具。",
    },
}


async def get_weather(
    lat: float = 0.0,
    lon: float = 0.0,
    latitude: float | None = None,
    longitude: float | None = None,
    days: int = 3,
    start_date: str | None = None,
    weather_scenario: str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Forecast via Open-Meteo, or return scenario planning hints without claiming real weather."""
    settings = get_settings()
    days = max(1, min(int(days or 3), 14))
    try:
        lat = float(latitude if latitude is not None else lat or 0)
        lon = float(longitude if longitude is not None else lon or 0)
    except (TypeError, ValueError):
        lat, lon = 0.0, 0.0

    if weather_scenario in _SCENARIO_HINTS and not start_date:
        meta = _SCENARIO_HINTS[weather_scenario]
        return {
            "ok": True,
            "mode": "scenario",
            "weather_scenario": weather_scenario,
            "label": meta["label"],
            "suggest_indoor": meta["suggest_indoor"],
            "planning_hint": meta["hint"],
            "days": days,
            "note": "用户指定天气情景规划，非实时预报，不保证出行日真实天气。",
        }

    if not lat or not lon:
        return {
            "ok": False,
            "error": "lat_lon_required",
            "hint": "pass lat & lon (or latitude & longitude)",
        }

    # Open-Meteo forecast window is from "today"; request enough days then slice.
    forecast_days = 16
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "forecast_days": forecast_days,
        "timezone": "auto",
    }
    async def _once() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.tool_timeout_sec) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()

    try:
        data = await once_with_retry(_once)
    except Exception as e:
        return {"ok": False, "error": f"weather_failed: {e}", "source": "open-meteo"}

    daily = data.get("daily") or {}
    out = []
    times = daily.get("time") or []
    for i, day in enumerate(times):
        code = (daily.get("weathercode") or [None])[i] if i < len(daily.get("weathercode") or []) else None
        precip = (
            (daily.get("precipitation_probability_max") or [None])[i]
            if i < len(daily.get("precipitation_probability_max") or [])
            else None
        )
        out.append(
            {
                "date": day,
                "weathercode": code,
                "temp_max_c": (daily.get("temperature_2m_max") or [None])[i]
                if i < len(daily.get("temperature_2m_max") or [])
                else None,
                "temp_min_c": (daily.get("temperature_2m_min") or [None])[i]
                if i < len(daily.get("temperature_2m_min") or [])
                else None,
                "precip_prob_max": precip,
                "suggest_indoor": bool(precip and precip >= 50),
            }
        )

    note = "概率与编码为预报，非保证"
    if start_date:
        try:
            start = date.fromisoformat(start_date)
            end = start + timedelta(days=days - 1)
            sliced = [d for d in out if start.isoformat() <= d["date"] <= end.isoformat()]
            if not sliced:
                return {
                    "ok": True,
                    "source": "open-meteo",
                    "mode": "date_out_of_forecast",
                    "requested_start": start_date,
                    "requested_days": days,
                    "daily": [],
                    "note": f"请求日期 {start_date} 起不在近 {forecast_days} 天预报窗内，请以出行前官方预报为准。",
                }
            out = sliced
            note = f"已按 start_date={start_date} 截取 {len(out)} 天；" + note
        except ValueError:
            note = f"start_date 无效 ({start_date})，已返回从今天起的预报；" + note
    else:
        out = out[:days]

    result: dict[str, Any] = {"ok": True, "source": "open-meteo", "mode": "forecast", "daily": out, "note": note}
    if weather_scenario in _SCENARIO_HINTS:
        result["weather_scenario"] = weather_scenario
        result["scenario_hint"] = _SCENARIO_HINTS[weather_scenario]["hint"]
    return result


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "get_weather",
        get_weather,
        description=(
            'Weather. Args: {"lat":30.2,"lon":120.1,"start_date":"YYYY-MM-DD"} '
            "or {\"weather_scenario\":\"sunny|cloudy|rainy\"} (aliases: latitude/longitude)."
        ),
        permission="low",
    )
