from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.tools.builtin.geo import geocode_location
from app.tools.builtin.preferences import TravelPreferences
from app.tools.builtin.weather import get_weather
from app.tools.registry import ToolRegistry

GeoFn = Callable[..., Awaitable[dict[str, Any]]]
WeatherFn = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class PrefetchResult:
    """Deterministic pre-loop tool results (no LLM step)."""

    observations: list[dict[str, Any]] = field(default_factory=list)
    center: dict[str, float] | None = None
    context_block: str = ""
    ok_geocode: bool = False
    ok_weather: bool = False


def _center_from_geo(geo: dict[str, Any]) -> dict[str, float] | None:
    results = geo.get("results") or []
    if not results:
        return None
    first = results[0] or {}
    try:
        lat = float(first.get("lat") if "lat" in first else first.get("latitude"))
        lon = float(first.get("lon") if "lon" in first else first.get("longitude"))
    except (TypeError, ValueError):
        return None
    return {"lat": lat, "lon": lon}


def _bind_from_registry(registry: ToolRegistry | None) -> tuple[GeoFn, WeatherFn]:
    if registry is None:
        return geocode_location, get_weather

    async def _geo(**kwargs: Any) -> dict[str, Any]:
        if registry.has("geocode_location"):
            return await registry.invoke("geocode_location", kwargs)
        return await geocode_location(**kwargs)

    async def _weather(**kwargs: Any) -> dict[str, Any]:
        if registry.has("get_weather"):
            return await registry.invoke("get_weather", kwargs)
        return await get_weather(**kwargs)

    return _geo, _weather


async def prefetch_plan_context(
    prefs: TravelPreferences,
    *,
    registry: ToolRegistry | None = None,
) -> PrefetchResult:
    """Geocode city + weather before ReAct — saves 1 LLM step for get_weather.

    Does NOT prefetch food/hotels (need attraction coords from search_places).
    Uses registry when provided (tests/stubs); else builtin implementations.
    """
    geocode_fn, weather_fn = _bind_from_registry(registry)
    out = PrefetchResult()
    city = (prefs.city or "").strip()
    if not city:
        return out

    geo_args: dict[str, Any] = {"query": city}
    geo = await geocode_fn(**geo_args)
    if not isinstance(geo, dict):
        geo = {"ok": False, "error": "invalid_geocode_result"}
    out.observations.append(
        {
            "tool": "geocode_location",
            "args": geo_args,
            "result": geo,
            "prefetched": True,
        }
    )
    out.ok_geocode = bool(geo.get("ok"))
    center = _center_from_geo(geo) if out.ok_geocode else None
    # Stub geocode may not return results[]; still allow scenario weather
    if center is None and out.ok_geocode and geo.get("lat") is not None:
        try:
            center = {"lat": float(geo["lat"]), "lon": float(geo["lon"])}
        except (KeyError, TypeError, ValueError):
            center = None
    out.center = center

    weather_args: dict[str, Any] = {"days": max(1, min(int(prefs.days or 3), 14))}
    if prefs.start_date:
        weather_args["start_date"] = prefs.start_date
    if prefs.weather_scenario:
        weather_args["weather_scenario"] = prefs.weather_scenario
    if center:
        weather_args["lat"] = center["lat"]
        weather_args["lon"] = center["lon"]
    elif prefs.weather_scenario and not prefs.start_date:
        pass
    else:
        lines = [
            "[编排预取]",
            f"city={city}",
            f"geocode={'ok' if out.ok_geocode else 'failed'}",
            "weather=skipped（无坐标且无纯情景）",
            "勿重复 geocode；请先 search_places，lunch/hotels 须用景点坐标。",
        ]
        out.context_block = "\n".join(lines)
        return out

    weather = await weather_fn(**weather_args)
    if not isinstance(weather, dict):
        weather = {"ok": False, "error": "invalid_weather_result"}
    out.observations.append(
        {
            "tool": "get_weather",
            "args": weather_args,
            "result": weather,
            "prefetched": True,
        }
    )
    out.ok_weather = bool(weather.get("ok"))

    lines = [
        "[编排预取 — 已计入门禁 Observation，勿再调 get_weather，除非结果 ok=false]",
        f"city={city}",
    ]
    if center:
        lines.append(f"center_lat={center['lat']}, center_lon={center['lon']}")
    lines.append(f"weather_ok={out.ok_weather}")
    if weather.get("mode"):
        lines.append(f"weather_mode={weather.get('mode')}")
    if weather.get("note"):
        lines.append(f"weather_note={weather.get('note')}")
    daily = weather.get("daily") or []
    if daily:
        d0 = daily[0]
        lines.append(
            "day0_temp="
            f"{d0.get('temp_min_c')}~{d0.get('temp_max_c')}C "
            f"precip={d0.get('precip_prob_max')}"
        )
    if weather.get("planning_hint"):
        lines.append(f"planning_hint={weather.get('planning_hint')}")
    lines.append(
        "推荐顺序：search_places →（用景点坐标）lunch+search_hotels → dinner → "
        "estimate_route → estimate_budget + suggest_packing → FINAL。"
        "lunch/hotels 依赖景点坐标，不可在 places 之前瞎调。"
    )
    out.context_block = "\n".join(lines)
    return out
