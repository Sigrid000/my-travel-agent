from __future__ import annotations

import math
from typing import Any
from urllib.parse import quote

import httpx

from app.config import get_settings
from app.tools.builtin.budget_style import (
    hotel_max_cny,
    infer_budget_style,
    meal_max_cny,
    rank_places_for_budget,
    style_label_zh,
)
from app.tools.http_retry import once_with_retry
from app.tools.registry import ToolRegistry

_OVERPASS_URLS = (
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


def _place_name(tags: dict[str, Any]) -> str | None:
    for key in ("name:zh", "name", "name:en", "alt_name"):
        val = tags.get(key)
        if val and str(val).strip():
            return str(val).strip()
    return None


def _parse_overpass_elements(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    places: list[dict[str, Any]] = []
    seen: set[str] = set()
    for el in payload.get("elements") or []:
        tags = el.get("tags") or {}
        name = _place_name(tags)
        if not name or name in seen:
            continue
        plat = el.get("lat")
        plon = el.get("lon")
        if plat is None or plon is None:
            center = el.get("center") or {}
            plat = center.get("lat")
            plon = center.get("lon")
        if plat is None or plon is None:
            continue
        seen.add(name)
        kinds = tags.get("tourism") or tags.get("historic") or "poi"
        places.append(
            {
                "name": name,
                "kinds": kinds,
                "lat": float(plat),
                "lon": float(plon),
                "source": "overpass_osm",
            }
        )
        if len(places) >= limit:
            break
    return places


async def _search_overpass(
    *,
    lat: float,
    lon: float,
    limit: int,
    user_agent: str,
    timeout_sec: float,
) -> list[dict[str, Any]]:
    """OSM Overpass: nearby tourism/historic POIs — no API key required."""
    radius_m = 8000
    # Keep query modest for public instance fair-use
    query = f"""
[out:json][timeout:{max(10, int(timeout_sec))}];
(
  nwr["tourism"~"attraction|museum|viewpoint|theme_park|zoo|gallery|artwork"](around:{radius_m},{lat},{lon});
  nwr["historic"~"monument|memorial|castle|ruins|archaeological_site"](around:{radius_m},{lat},{lon});
);
out center tags {max(limit * 3, 15)};
"""
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=max(timeout_sec, 35.0)) as client:
        for url in _OVERPASS_URLS:

            async def _once(u: str = url) -> list[dict[str, Any]]:
                # Public Overpass nodes are picky: try quoted form body, then httpx form, then GET
                attempts = [
                    (
                        "post_quoted",
                        {
                            "content": ("data=" + quote(query, safe="")).encode("utf-8"),
                            "headers": {
                                **headers,
                                "Content-Type": "application/x-www-form-urlencoded",
                            },
                        },
                    ),
                    ("post_form", {"data": {"data": query}, "headers": headers}),
                    ("get", {"params": {"data": query}, "headers": headers}),
                ]
                last: httpx.Response | None = None
                for kind, kwargs in attempts:
                    if kind == "get":
                        last = await client.get(u, **kwargs)
                    else:
                        last = await client.post(u, **kwargs)
                    if last.status_code == 200:
                        return _parse_overpass_elements(last.json(), limit)
                assert last is not None
                last.raise_for_status()
                return _parse_overpass_elements(last.json(), limit)

            try:
                places = await once_with_retry(_once)
                if places:
                    return places
                errors.append(f"{url}: empty_result")
            except Exception as e:
                errors.append(f"{url}: {e}")
    raise RuntimeError("; ".join(errors) if errors else "overpass_unavailable")


async def _search_opentripmap(
    *,
    lat: float,
    lon: float,
    kind: str,
    limit: int,
    api_key: str,
    timeout_sec: float,
) -> list[dict[str, Any]]:
    async def _once() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(
                "https://api.opentripmap.com/0.1/en/places/radius",
                params={
                    "radius": 8000,
                    "lon": lon,
                    "lat": lat,
                    "kinds": kind if kind != "tourist" else "interesting_places",
                    "limit": limit,
                    "apikey": api_key,
                },
            )
            r.raise_for_status()
            payload = r.json()
        features = payload if isinstance(payload, list) else payload.get("features") or []
        places = []
        for f in features[:limit]:
            props = f.get("properties") or f
            name = props.get("name") or props.get("xid")
            if not name or name == "unknown":
                continue
            places.append(
                {
                    "name": name,
                    "kinds": props.get("kinds"),
                    "lat": (f.get("geometry") or {}).get("coordinates", [None, None])[1]
                    if f.get("geometry")
                    else props.get("lat"),
                    "lon": (f.get("geometry") or {}).get("coordinates", [None, None])[0]
                    if f.get("geometry")
                    else props.get("lon"),
                    "source": "opentripmap",
                }
            )
        return places

    return await once_with_retry(_once)


async def _geocode_amap(q: str, *, api_key: str, timeout_sec: float) -> list[dict[str, Any]]:
    """Amap (Gaode) geocode — GCJ-02; requires AMAP_API_KEY."""

    async def _once() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(
                "https://restapi.amap.com/v3/geocode/geo",
                params={"address": q, "key": api_key, "output": "json"},
            )
            r.raise_for_status()
            payload = r.json()
        if str(payload.get("status")) != "1":
            raise RuntimeError(payload.get("info") or payload.get("infocode") or "amap_geocode_failed")
        out: list[dict[str, Any]] = []
        for item in payload.get("geocodes") or []:
            loc = str(item.get("location") or "")
            if "," not in loc:
                continue
            lon_s, lat_s = loc.split(",", 1)
            out.append(
                {
                    "display_name": str(item.get("formatted_address") or item.get("city") or q),
                    "lat": float(lat_s),
                    "lon": float(lon_s),
                }
            )
        return out

    return await once_with_retry(_once)


def _ticket_verify_links(name: str) -> dict[str, Any]:
    """OTA keyword search deep links — not partner APIs; user verifies live price."""
    q = quote(name)
    q_ticket = quote(f"{name}门票")
    return {
        "ctrip_search": f"https://m.ctrip.com/webapp/ticket/search?keyword={q}",
        "meituan_search": f"https://www.meituan.com/s/?q={q_ticket}",
        "disclaimer": (
            "第三方门票搜索入口，价格以打开后的实时页为准；"
            "本系统不代订、不抓取、不担保报价"
        ),
    }


def _attach_ticket_policy(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "")
    row["ticket_price_policy"] = (
        "无全国统一官方票价 API：优先 website/tel；否则用 ticket_verify 打开携程/美团核验；"
        "预算中的门票仅为经验区间，禁止写成精确成交价"
    )
    row["ticket_verify"] = _ticket_verify_links(name)
    return row


async def _amap_place_detail(
    poi_id: str, *, api_key: str, timeout_sec: float
) -> dict[str, Any] | None:
    """Fetch one POI detail — used to backfill biz_ext.cost (人均) when list search omits it."""

    async def _once() -> dict[str, Any] | None:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(
                "https://restapi.amap.com/v3/place/detail",
                params={"key": api_key, "id": poi_id, "output": "json"},
            )
            r.raise_for_status()
            payload = r.json()
        if str(payload.get("status")) != "1":
            return None
        pois = payload.get("pois") or []
        return pois[0] if pois and isinstance(pois[0], dict) else None

    try:
        return await once_with_retry(_once)
    except Exception:
        return None


def _apply_amap_cost_fields(row: dict[str, Any], biz: Any, *, is_food: bool = False) -> None:
    if isinstance(biz, list):
        biz = biz[0] if biz else {}
    if not isinstance(biz, dict):
        return
    cost_raw = biz.get("cost")
    if cost_raw not in (None, "", [], "[]"):
        try:
            cost_val: float | str = float(cost_raw)
        except (TypeError, ValueError):
            cost_val = str(cost_raw)
        row["amap_avg_cost"] = cost_val
        if is_food or row.get("kinds", "").startswith("餐饮"):
            row["avg_cost_per_person_cny"] = cost_val
            row["avg_cost_note"] = "高德人均参考（biz_ext.cost），非菜单实价"
        rating = biz.get("rating")
        if rating not in (None, "", []) and not row.get("rating"):
            row["rating"] = rating


async def _backfill_food_avg_cost(
    places: list[dict[str, Any]],
    *,
    api_key: str,
    timeout_sec: float,
    max_detail: int = 8,
) -> list[dict[str, Any]]:
    """Detail-fetch POIs missing 人均 so food lists expose avg cost when Amap has it."""
    filled = 0
    for row in places:
        if row.get("amap_avg_cost") is not None or row.get("avg_cost_per_person_cny") is not None:
            if row.get("avg_cost_per_person_cny") is None and row.get("amap_avg_cost") is not None:
                row["avg_cost_per_person_cny"] = row["amap_avg_cost"]
                row["avg_cost_note"] = "高德人均参考（biz_ext.cost），非菜单实价"
            continue
        poi_id = str(row.get("amap_poi_id") or "")
        if not poi_id or filled >= max_detail:
            continue
        detail = await _amap_place_detail(poi_id, api_key=api_key, timeout_sec=timeout_sec)
        filled += 1
        if not detail:
            continue
        _apply_amap_cost_fields(row, detail.get("biz_ext"), is_food=True)
        if not row.get("rating"):
            biz = detail.get("biz_ext") or {}
            if isinstance(biz, dict) and biz.get("rating") not in (None, "", []):
                row["rating"] = biz.get("rating")
    return places


def _amap_poi_row(poi: dict[str, Any]) -> dict[str, Any] | None:
    name = str(poi.get("name") or "").strip()
    loc = str(poi.get("location") or "")
    if not name or "," not in loc:
        return None
    lon_s, lat_s = loc.split(",", 1)
    biz = poi.get("biz_ext") or {}
    if isinstance(biz, list):
        biz = biz[0] if biz else {}
    website = poi.get("website")
    if isinstance(website, list):
        website = website[0] if website else ""
    tel = poi.get("tel")
    if isinstance(tel, list):
        tel = tel[0] if tel else ""
    kinds = str(poi.get("type") or "poi")
    is_scenic = "风景" in kinds or str(poi.get("biz_type") or "") == "tour"
    is_food = kinds.startswith("餐饮")
    row: dict[str, Any] = {
        "name": name,
        "kinds": kinds,
        "lat": float(lat_s),
        "lon": float(lon_s),
        "address": str(poi.get("address") or ""),
        "tel": str(tel or ""),
        "website": str(website or ""),
        "amap_poi_id": str(poi.get("id") or ""),
        "source": "amap",
    }
    if isinstance(biz, dict):
        _apply_amap_cost_fields(row, biz, is_food=is_food)
        if is_scenic and row.get("amap_avg_cost") is not None:
            row["ticket_price_hint_cny"] = row["amap_avg_cost"]
            row["ticket_price_status"] = "amap_cost_hint"
            row["cost_field_note"] = "高德 biz_ext.cost：景区偶发参考价（非官网实时票价）"
        elif is_scenic:
            row["ticket_price_status"] = "check_official_or_ota"
            row["ticket_price_note"] = (
                "高德未返回结构化门票价；请用 website/tel 或 ticket_verify（携程/美团搜索）核验"
            )
        elif is_food and row.get("amap_avg_cost") is not None:
            row["cost_field_note"] = "高德人均参考（非菜单实价）"
        rating = biz.get("rating")
        if rating not in (None, "", []):
            row["rating"] = rating
        opentime = biz.get("opentime2") or biz.get("open_time")
        if opentime not in (None, "", [], "[]"):
            row["opentime"] = opentime if isinstance(opentime, str) else str(opentime)
        level = biz.get("level")
        if level not in (None, "", [], "[]"):
            row["scenic_level"] = level
        if biz.get("ticket_ordering") not in (None, "", []):
            row["amap_ticket_ordering"] = biz.get("ticket_ordering")
    if is_scenic:
        _attach_ticket_policy(row)
    dist = poi.get("distance")
    if dist not in (None, ""):
        try:
            row["distance_m_from_center"] = int(float(dist))
        except (TypeError, ValueError):
            pass
    return row


def _ingest_amap_pois(
    pois: list[Any], places: list[dict[str, Any]], seen: set[str], limit: int
) -> None:
    for poi in pois:
        if not isinstance(poi, dict):
            continue
        row = _amap_poi_row(poi)
        if not row or row["name"] in seen:
            continue
        seen.add(row["name"])
        places.append(row)
        if len(places) >= limit:
            return


async def _search_amap_places(
    *,
    city: str,
    lat: float,
    lon: float,
    limit: int,
    api_key: str,
    timeout_sec: float,
    keywords: str = "风景名胜",
    around_keywords: str = "风景名胜|博物馆|公园",
    types: str = "110000",
    radius_m: int = 20000,
    extensions: str = "all",
) -> list[dict[str, Any]]:
    """Amap place text + around search."""

    async def _once() -> list[dict[str, Any]]:
        places: list[dict[str, Any]] = []
        seen: set[str] = set()
        text_err: str | None = None
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            if city.strip():
                r = await client.get(
                    "https://restapi.amap.com/v3/place/text",
                    params={
                        "key": api_key,
                        "keywords": keywords,
                        "city": city,
                        "citylimit": "true",
                        "offset": max(1, min(limit, 25)),
                        "page": 1,
                        "extensions": extensions,
                        "output": "json",
                    },
                )
                r.raise_for_status()
                payload = r.json()
                if str(payload.get("status")) == "1":
                    _ingest_amap_pois(payload.get("pois") or [], places, seen, limit)
                else:
                    text_err = str(
                        payload.get("info") or payload.get("infocode") or "amap_place_text_failed"
                    )

            if len(places) < limit and lat and lon:
                r2 = await client.get(
                    "https://restapi.amap.com/v3/place/around",
                    params={
                        "key": api_key,
                        "location": f"{lon},{lat}",
                        "keywords": around_keywords,
                        "types": types,
                        "radius": max(200, min(int(radius_m), 50000)),
                        "offset": max(1, min(limit, 25)),
                        "page": 1,
                        "extensions": extensions,
                        "output": "json",
                    },
                )
                r2.raise_for_status()
                around = r2.json()
                if str(around.get("status")) == "1":
                    _ingest_amap_pois(around.get("pois") or [], places, seen, limit)
                elif not places:
                    raise RuntimeError(
                        around.get("info") or text_err or "amap_place_failed"
                    )
            elif not places and text_err:
                raise RuntimeError(text_err)
        return places

    return await once_with_retry(_once)


async def _search_overpass_amenities(
    *,
    lat: float,
    lon: float,
    limit: int,
    user_agent: str,
    timeout_sec: float,
    filters: str,
    kind_label: str,
    radius_m: int = 1500,
) -> list[dict[str, Any]]:
    query = f"""
[out:json][timeout:{max(10, int(timeout_sec))}];
(
  {filters}
);
out center tags {max(limit * 3, 15)};
"""
    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    async with httpx.AsyncClient(timeout=max(timeout_sec, 35.0)) as client:
        last_err = "overpass_failed"
        for url in _OVERPASS_URLS:

            async def _once(u: str = url) -> list[dict[str, Any]]:
                r = await client.post(u, data={"data": query}, headers=headers)
                r.raise_for_status()
                payload = r.json()
                places: list[dict[str, Any]] = []
                seen: set[str] = set()
                for el in payload.get("elements") or []:
                    tags = el.get("tags") or {}
                    name = _place_name(tags)
                    if not name or name in seen:
                        continue
                    plat = el.get("lat")
                    plon = el.get("lon")
                    if plat is None or plon is None:
                        center = el.get("center") or {}
                        plat, plon = center.get("lat"), center.get("lon")
                    if plat is None or plon is None:
                        continue
                    seen.add(name)
                    places.append(
                        {
                            "name": name,
                            "kinds": tags.get("amenity")
                            or tags.get("tourism")
                            or kind_label,
                            "lat": float(plat),
                            "lon": float(plon),
                            "source": "overpass_osm",
                        }
                    )
                    if len(places) >= limit:
                        break
                return places

            try:
                return await once_with_retry(_once)
            except Exception as e:
                last_err = str(e)
                continue
        raise RuntimeError(last_err)


def _taxi_fare_range(distance_m: float) -> dict[str, Any]:
    km = max(0.0, float(distance_m) / 1000.0)
    low = 8 + max(0.0, km - 3.0) * 2.0
    high = 13 + max(0.0, km - 2.0) * 3.5
    return {
        "taxi_cny_range": [int(round(low)), int(round(high))],
        "fare_note": (
            "按国内出租车常见计价经验估算（起步约8–13元 + 超公里约2–3.5元/km），"
            "非实时计价器，不含等候费/夜间加价/网约车动态调价"
        ),
    }


async def _route_amap(
    *,
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    profile: str,
    api_key: str,
    timeout_sec: float,
) -> dict[str, Any]:
    path = "walking" if profile in {"foot", "walking"} else "driving"

    async def _once() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(
                f"https://restapi.amap.com/v3/direction/{path}",
                params={
                    "key": api_key,
                    "origin": f"{from_lon},{from_lat}",
                    "destination": f"{to_lon},{to_lat}",
                    "extensions": "base",
                    "output": "json",
                },
            )
            r.raise_for_status()
            payload = r.json()
        if str(payload.get("status")) != "1":
            raise RuntimeError(payload.get("info") or payload.get("infocode") or "amap_route_failed")
        route = payload.get("route") or {}
        paths = route.get("paths") or []
        if not paths:
            raise RuntimeError("no_route")
        best = paths[0]
        distance_m = float(best.get("distance") or 0)
        duration_s = float(best.get("duration") or 0)
        out: dict[str, Any] = {
            "ok": True,
            "source": "amap",
            "profile": "foot" if path == "walking" else "car",
            "distance_m": distance_m,
            "duration_s": duration_s,
            "note": "高德路径规划；时长为预估，非实时路况保证",
        }
        if path == "driving":
            out.update(_taxi_fare_range(distance_m))
        return out

    return await once_with_retry(_once)


async def _geocode_open_meteo(q: str, *, timeout_sec: float, user_agent: str) -> list[dict[str, Any]]:
    """Open-Meteo geocoding — usually reachable when OSM Nominatim is blocked."""

    async def _once() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": q, "count": 5, "language": "zh", "format": "json"},
                headers={"User-Agent": user_agent},
            )
            r.raise_for_status()
            payload = r.json()
        out: list[dict[str, Any]] = []
        for item in payload.get("results") or []:
            lat, lon = item.get("latitude"), item.get("longitude")
            if lat is None or lon is None:
                continue
            parts = [item.get("name"), item.get("admin1"), item.get("country")]
            display = " ".join(str(p) for p in parts if p)
            out.append(
                {
                    "display_name": display or str(item.get("name") or q),
                    "lat": float(lat),
                    "lon": float(lon),
                }
            )
        return out

    return await once_with_retry(_once)


async def _geocode_photon(q: str, *, timeout_sec: float, user_agent: str) -> list[dict[str, Any]]:
    async def _once() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(
                "https://photon.komoot.io/api/",
                params={"q": q, "limit": 5},
                headers={"User-Agent": user_agent},
            )
            r.raise_for_status()
            payload = r.json()
        out: list[dict[str, Any]] = []
        for feat in payload.get("features") or []:
            props = feat.get("properties") or {}
            coords = (feat.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            name = props.get("name") or props.get("city") or q
            parts = [name, props.get("state"), props.get("country")]
            display = ", ".join(str(p) for p in parts if p)
            out.append({"display_name": display, "lat": lat, "lon": lon})
        return out

    return await once_with_retry(_once)


async def _geocode_nominatim(q: str, *, timeout_sec: float, user_agent: str) -> list[dict[str, Any]]:
    async def _once() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "json", "limit": 5},
                headers={"User-Agent": user_agent},
            )
            r.raise_for_status()
            data = r.json()
        return [
            {
                "display_name": item.get("display_name"),
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
            }
            for item in data
            if item.get("lat") and item.get("lon")
        ]

    return await once_with_retry(_once)


async def geocode_location(
    query: str = "",
    place: str = "",
    q: str = "",
    city: str = "",
    text: str = "",
    **_: Any,
) -> dict[str, Any]:
    """Geocode place name. Amap (if key) → Open-Meteo → Photon → Nominatim."""
    settings = get_settings()
    q = (query or place or q or city or text or "").strip()
    if not q:
        return {"ok": False, "error": "empty_query", "hint": "pass query=城市名"}

    headers_ua = settings.nominatim_user_agent
    timeout = settings.tool_timeout_sec
    errors: dict[str, str] = {}

    if settings.amap_api_key.strip():
        try:
            results = await _geocode_amap(q, api_key=settings.amap_api_key.strip(), timeout_sec=timeout)
            if results:
                return {"ok": True, "source": "amap", "query": q, "results": results}
            errors["amap"] = "empty_result"
        except Exception as e:
            errors["amap"] = str(e) or type(e).__name__

    backends = (
        ("open-meteo", _geocode_open_meteo),
        ("photon", _geocode_photon),
        ("nominatim", _geocode_nominatim),
    )
    for source, fn in backends:
        try:
            results = await fn(q, timeout_sec=timeout, user_agent=headers_ua)
            if results:
                return {"ok": True, "source": source, "query": q, "results": results}
            errors[source] = "empty_result"
        except Exception as e:
            errors[source] = str(e) or type(e).__name__

    return {
        "ok": False,
        "error": "geocode_failed",
        "query": q,
        "upstream_errors": errors,
        "note": "地理编码均不可用；不编造坐标",
    }


async def search_places(
    city: str = "",
    kind: str = "tourist",
    limit: int = 5,
    **_: Any,
) -> dict[str, Any]:
    """Search POIs: Amap (if key) → Overpass → optional OpenTripMap. No fake catalog."""
    settings = get_settings()
    city = (city or "").strip()
    if not city:
        return {"ok": False, "error": "city_required"}

    geo = await geocode_location(city)
    if not geo.get("ok") or not geo.get("results"):
        return {"ok": False, "error": "geocode_failed", "detail": geo}
    lat = geo["results"][0]["lat"]
    lon = geo["results"][0]["lon"]
    limit = max(1, min(int(limit or 5), 15))

    errors: dict[str, str] = {}

    if settings.amap_api_key.strip():
        try:
            places = await _search_amap_places(
                city=city,
                lat=lat,
                lon=lon,
                limit=limit,
                api_key=settings.amap_api_key.strip(),
                timeout_sec=settings.tool_timeout_sec,
                extensions="all",
            )
            if places:
                return {
                    "ok": True,
                    "city": city,
                    "center": {"lat": lat, "lon": lon},
                    "places": places,
                    "source": "amap",
                    "note": (
                        "高德地图数据（GCJ-02）：点位/电话/开放时间；"
                        "门票无结构化价时见 ticket_verify（携程/美团搜索）与经验预算区间；"
                        "不代订"
                    ),
                }
            errors["amap"] = "empty_result"
        except Exception as e:
            errors["amap"] = str(e)

    try:
        places = await _search_overpass(
            lat=lat,
            lon=lon,
            limit=limit,
            user_agent=settings.nominatim_user_agent,
            timeout_sec=settings.tool_timeout_sec,
        )
        if places:
            places = [_attach_ticket_policy(dict(p)) for p in places]
            return {
                "ok": True,
                "city": city,
                "center": {"lat": lat, "lon": lon},
                "places": places,
                "source": "overpass_osm",
                "note": "OpenStreetMap 数据；门票请用 ticket_verify / 官网核验，不代订",
            }
        errors["overpass"] = "empty_result"
    except Exception as e:
        errors["overpass"] = str(e)

    if settings.opentripmap_api_key.strip():
        try:
            places = await _search_opentripmap(
                lat=lat,
                lon=lon,
                kind=kind,
                limit=limit,
                api_key=settings.opentripmap_api_key,
                timeout_sec=settings.tool_timeout_sec,
            )
            if places:
                return {
                    "ok": True,
                    "city": city,
                    "center": {"lat": lat, "lon": lon},
                    "places": places,
                    "source": "opentripmap",
                }
            errors["opentripmap"] = "empty_result"
        except Exception as e:
            errors["opentripmap"] = str(e)

    return {
        "ok": False,
        "error": "poi_unavailable",
        "city": city,
        "center": {"lat": lat, "lon": lon},
        "places": [],
        "upstream_errors": errors,
        "note": "在线 POI 查询失败；不编造景点，请稍后重试或换城市关键词",
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))


def _enrich_food_refs(
    places: list[dict[str, Any]],
    *,
    anchor_lat: float,
    anchor_lon: float,
    hotel_lat: float = 0.0,
    hotel_lon: float = 0.0,
    hotel_name: str = "",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in places:
        row = dict(p)
        plat, plon = float(row.get("lat") or 0), float(row.get("lon") or 0)
        if plat and plon:
            if row.get("distance_m_from_center") is None:
                row["distance_m_from_center"] = _haversine_m(anchor_lat, anchor_lon, plat, plon)
            if hotel_lat and hotel_lon:
                row["distance_m_from_hotel"] = _haversine_m(hotel_lat, hotel_lon, plat, plon)
                if hotel_name:
                    row["hotel_ref_name"] = hotel_name
        out.append(row)
    return out


async def search_food_nearby(
    lat: float = 0.0,
    lon: float = 0.0,
    city: str = "",
    near_name: str = "",
    radius_m: int = 0,
    limit: int = 5,
    keywords: str = "",
    meal: str = "lunch",
    purpose: str = "",
    hotel_lat: float = 0.0,
    hotel_lon: float = 0.0,
    hotel_name: str = "",
    budget_style: str = "",
    budget_cny: int | float | None = None,
    days: int = 0,
    party_size: int = 1,
    max_avg_cost: float = 0,
    **_: Any,
) -> dict[str, Any]:
    """Food near a POI: lunch=close convenience; dinner=wider + rating-aware reference list."""
    settings = get_settings()
    lat = _coord(lat)
    lon = _coord(lon)
    hotel_lat = _coord(hotel_lat)
    hotel_lon = _coord(hotel_lon)
    meal_key = (meal or purpose or "lunch").strip().lower()
    if meal_key in {"dinner", "supper", "evening", "晚饭", "晚餐"}:
        meal_key = "dinner"
    else:
        meal_key = "lunch"
    city = (city or "").strip()
    limit = max(1, min(int(limit or 5), 10))
    tier = infer_budget_style(
        budget_cny=budget_cny,
        days=days or None,
        party_size=party_size,
        style=budget_style,
    )
    cost_cap = float(max_avg_cost) if max_avg_cost and float(max_avg_cost) > 0 else meal_max_cny(tier)
    # lunch: tight around POI; dinner: city-wide (text in city) or wide radius fallback
    if not radius_m:
        radius_m = 25000 if meal_key == "dinner" else 1500
    radius_m = max(300, min(int(radius_m), 50000))
    if not keywords:
        keywords = (
            "特色菜|火锅|地方菜|烧烤|餐厅"
            if meal_key == "dinner"
            else "餐厅|美食|快餐"
        )
    if not lat or not lon:
        if meal_key == "dinner" and city:
            geo = await geocode_location(city)
            if geo.get("ok") and geo.get("results"):
                lat = float(geo["results"][0]["lat"])
                lon = float(geo["results"][0]["lon"])
        if not lat or not lon:
            return {
                "ok": False,
                "error": "coords_or_city_required",
                "hint": "lunch needs lat/lon; dinner prefers city=城市名（全市）+ 可选景点/酒店坐标算距离",
            }

    errors: dict[str, str] = {}
    if settings.amap_api_key.strip():
        try:
            # Dinner: search within city (citylimit); lunch: around-only near POI
            search_city = city if meal_key == "dinner" and city else ""
            places = await _search_amap_places(
                city=search_city,
                lat=lat,
                lon=lon,
                limit=max(limit, min(limit * 2, 15)),
                api_key=settings.amap_api_key.strip(),
                timeout_sec=settings.tool_timeout_sec,
                keywords=keywords,
                around_keywords=keywords,
                types="050000",
                radius_m=radius_m,
                extensions="all",
            )
            places = _enrich_food_refs(
                places,
                anchor_lat=lat,
                anchor_lon=lon,
                hotel_lat=hotel_lat,
                hotel_lon=hotel_lon,
                hotel_name=hotel_name,
            )
            places = await _backfill_food_avg_cost(
                places,
                api_key=settings.amap_api_key.strip(),
                timeout_sec=settings.tool_timeout_sec,
                max_detail=max(limit, 8),
            )
            places = rank_places_for_budget(
                places,
                style=tier,
                max_avg_cost=cost_cap,
                prefer_low_cost=(tier == "budget" or meal_key == "lunch"),
            )
            if places:
                sliced = places[:limit]
                with_cost = sum(
                    1 for p in sliced if p.get("avg_cost_per_person_cny") is not None
                )
                in_band = sum(1 for p in sliced if p.get("budget_fit") is True)
                return {
                    "ok": True,
                    "meal": meal_key,
                    "city": city or "",
                    "scope": "city" if meal_key == "dinner" and search_city else "nearby",
                    "near_name": near_name or "",
                    "hotel_name": hotel_name or "",
                    "center": {"lat": lat, "lon": lon},
                    "radius_m": radius_m,
                    "places": sliced,
                    "budget_style": tier,
                    "budget_style_label": style_label_zh(tier),
                    "max_avg_cost_cny": cost_cap,
                    "budget_fit_count": f"{in_band}/{len(sliced)}",
                    "avg_cost_coverage": f"{with_cost}/{min(limit, len(places))}",
                    "source": "amap",
                    "note": (
                        "晚餐/美食参考：全市检索；字段 avg_cost_per_person_cny=高德人均；"
                        "含距景点/酒店距离。自选，不强制排进日程。"
                        if meal_key == "dinner"
                        else "午餐候选：贴近景点；avg_cost_per_person_cny=高德人均（若有）。"
                    )
                    + f" 已按「{style_label_zh(tier)}」档排序（人均上限约 {int(cost_cap)} 元）；"
                    "budget_fit=true 表示落入档位。人均为参考非菜单实价；不代订。",
                }
            errors["amap"] = "empty_result"
        except Exception as e:
            errors["amap"] = str(e)

    try:
        r = max(300, radius_m)
        filters = (
            f'nwr["amenity"~"restaurant|cafe|fast_food"](around:{r},{lat},{lon});'
        )
        places = await _search_overpass_amenities(
            lat=lat,
            lon=lon,
            limit=limit,
            user_agent=settings.nominatim_user_agent,
            timeout_sec=settings.tool_timeout_sec,
            filters=filters,
            kind_label="restaurant",
            radius_m=radius_m,
        )
        places = _enrich_food_refs(
            places,
            anchor_lat=lat,
            anchor_lon=lon,
            hotel_lat=hotel_lat,
            hotel_lon=hotel_lon,
            hotel_name=hotel_name,
        )
        if places:
            return {
                "ok": True,
                "meal": meal_key,
                "near_name": near_name or "",
                "hotel_name": hotel_name or "",
                "center": {"lat": lat, "lon": lon},
                "radius_m": radius_m,
                "places": places,
                "source": "overpass_osm",
                "note": "OSM 餐饮 POI；通常无人均价；含距锚点/酒店距离供自选",
            }
        errors["overpass"] = "empty_result"
    except Exception as e:
        errors["overpass"] = str(e)

    return {
        "ok": False,
        "error": "food_unavailable",
        "meal": meal_key,
        "near_name": near_name or "",
        "center": {"lat": lat, "lon": lon},
        "places": [],
        "upstream_errors": errors,
        "note": "周边美食查询失败；不编造餐厅",
    }


async def search_hotels(
    city: str = "",
    lat: float = 0.0,
    lon: float = 0.0,
    near_name: str = "",
    radius_m: int = 8000,
    limit: int = 5,
    keywords: str = "酒店",
    budget_style: str = "",
    budget_cny: int | float | None = None,
    days: int = 0,
    party_size: int = 1,
    max_avg_cost: float = 0,
    **_: Any,
) -> dict[str, Any]:
    """Hotel/homestay candidates near coords (preferred) or city — list only, never book."""
    settings = get_settings()
    city = (city or "").strip()
    lat = _coord(lat)
    lon = _coord(lon)
    limit = max(1, min(int(limit or 5), 10))
    radius_m = max(1000, min(int(radius_m or 8000), 30000))
    tier = infer_budget_style(
        budget_cny=budget_cny,
        days=days or None,
        party_size=party_size,
        style=budget_style,
    )
    cost_cap = float(max_avg_cost) if max_avg_cost and float(max_avg_cost) > 0 else hotel_max_cny(tier)
    if not city and not (lat and lon):
        return {"ok": False, "error": "city_or_coords_required"}

    if not (lat and lon) and city:
        geo = await geocode_location(city)
        if geo.get("ok") and geo.get("results"):
            lat = geo["results"][0]["lat"]
            lon = geo["results"][0]["lon"]

    errors: dict[str, str] = {}
    if settings.amap_api_key.strip() and lat and lon:
        try:
            # Around-first (same idea as food) so results are near the stay/scenic area
            places = await _search_amap_places(
                city="",
                lat=lat,
                lon=lon,
                limit=limit,
                api_key=settings.amap_api_key.strip(),
                timeout_sec=settings.tool_timeout_sec,
                keywords=keywords,
                around_keywords=keywords,
                types="100000",
                radius_m=radius_m,
                extensions="all",
            )
            places = rank_places_for_budget(
                places,
                style=tier,
                max_avg_cost=cost_cap,
                prefer_low_cost=(tier == "budget"),
            )
            # Secondary key: distance for ties / unknown cost
            places = sorted(
                places,
                key=lambda p: (
                    0 if p.get("budget_fit") is True else (1 if p.get("budget_fit") is None else 2),
                    int(p.get("distance_m_from_center") or 10**9),
                ),
            )
            if places:
                sliced = places[:limit]
                return {
                    "ok": True,
                    "city": city,
                    "near_name": near_name or "",
                    "center": {"lat": lat, "lon": lon},
                    "radius_m": radius_m,
                    "places": sliced,
                    "budget_style": tier,
                    "budget_style_label": style_label_zh(tier),
                    "max_avg_cost_cny": cost_cap,
                    "source": "amap",
                    "note": (
                        f"住宿候选（周边）；已按「{style_label_zh(tier)}」档偏好排序"
                        f"（参考人均/消费上限约 {int(cost_cap)} 元）。"
                        "amap_avg_cost 若有仅为参考，非实时房价。预订须用户自行完成（HITL）"
                    ),
                }
            errors["amap"] = "empty_result"
        except Exception as e:
            errors["amap"] = str(e)

    # Fallback: city text search if around empty/unavailable
    if settings.amap_api_key.strip() and city:
        try:
            places = await _search_amap_places(
                city=city,
                lat=lat or 0.0,
                lon=lon or 0.0,
                limit=limit,
                api_key=settings.amap_api_key.strip(),
                timeout_sec=settings.tool_timeout_sec,
                keywords=keywords,
                around_keywords=keywords,
                types="100000",
                radius_m=radius_m,
                extensions="all",
            )
            if places:
                places = rank_places_for_budget(
                    places,
                    style=tier,
                    max_avg_cost=cost_cap,
                    prefer_low_cost=(tier == "budget"),
                )
                return {
                    "ok": True,
                    "city": city,
                    "near_name": near_name or "",
                    "center": {"lat": lat, "lon": lon} if lat and lon else None,
                    "places": places[:limit],
                    "budget_style": tier,
                    "budget_style_label": style_label_zh(tier),
                    "max_avg_cost_cny": cost_cap,
                    "source": "amap",
                    "note": (
                        f"住宿候选（城市关键字回退）；「{style_label_zh(tier)}」档排序；"
                        "非实时房价；不代订"
                    ),
                }
            errors["amap_text"] = "empty_result"
        except Exception as e:
            errors["amap_text"] = str(e)

    if lat and lon:
        try:
            filters = (
                f'nwr["tourism"~"hotel|guest_house|hostel|motel"]'
                f"(around:8000,{lat},{lon});"
            )
            places = await _search_overpass_amenities(
                lat=lat,
                lon=lon,
                limit=limit,
                user_agent=settings.nominatim_user_agent,
                timeout_sec=settings.tool_timeout_sec,
                filters=filters,
                kind_label="hotel",
                radius_m=8000,
            )
            if places:
                return {
                    "ok": True,
                    "city": city,
                    "center": {"lat": lat, "lon": lon},
                    "places": places,
                    "source": "overpass_osm",
                    "note": "OSM 住宿 POI；无实时房价，仅候选",
                }
            errors["overpass"] = "empty_result"
        except Exception as e:
            errors["overpass"] = str(e)

    return {
        "ok": False,
        "error": "hotels_unavailable",
        "city": city,
        "places": [],
        "upstream_errors": errors,
        "note": "住宿检索失败；不编造酒店与房价",
    }


def _coord(val: Any, *keys: str) -> float:
    if isinstance(val, dict):
        for k in keys:
            if val.get(k) is not None:
                try:
                    return float(val[k])
                except (TypeError, ValueError):
                    pass
        return 0.0
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


async def estimate_route(
    from_lat: float = 0.0,
    from_lon: float = 0.0,
    to_lat: float = 0.0,
    to_lon: float = 0.0,
    profile: str = "car",
    mode: str = "",
    origin: Any = None,
    destination: Any = None,
    origin_lat: float = 0.0,
    origin_lon: float = 0.0,
    dest_lat: float = 0.0,
    dest_lon: float = 0.0,
    from_name: str = "",
    to_name: str = "",
    **_: Any,
) -> dict[str, Any]:
    """Route distance/duration: Amap if key else OSRM; taxi range when driving."""
    settings = get_settings()
    from_lat = _coord(from_lat) or _coord(origin_lat) or _coord(origin, "lat", "latitude")
    from_lon = _coord(from_lon) or _coord(origin_lon) or _coord(origin, "lon", "lng", "longitude")
    to_lat = _coord(to_lat) or _coord(dest_lat) or _coord(destination, "lat", "latitude")
    to_lon = _coord(to_lon) or _coord(dest_lon) or _coord(destination, "lon", "lng", "longitude")
    if mode in {"foot", "car", "bike", "driving", "walking"}:
        profile = "car" if mode in {"car", "driving"} else ("foot" if mode in {"foot", "walking"} else mode)
    if profile in {"driving"}:
        profile = "car"
    if profile in {"walking"}:
        profile = "foot"
    if not all([from_lat, from_lon, to_lat, to_lon]):
        return {
            "ok": False,
            "error": "coords_required",
            "hint": "pass from_lat,from_lon,to_lat,to_lon",
        }

    errors: dict[str, str] = {}
    if settings.amap_api_key.strip():
        try:
            out = await _route_amap(
                from_lat=from_lat,
                from_lon=from_lon,
                to_lat=to_lat,
                to_lon=to_lon,
                profile=profile,
                api_key=settings.amap_api_key.strip(),
                timeout_sec=settings.tool_timeout_sec,
            )
            if from_name:
                out["from_name"] = from_name
            if to_name:
                out["to_name"] = to_name
            return out
        except Exception as e:
            errors["amap"] = str(e) or type(e).__name__

    osrm_profile = "car" if profile == "car" else "foot"
    url = (
        f"https://router.project-osrm.org/route/v1/{osrm_profile}/"
        f"{from_lon},{from_lat};{to_lon},{to_lat}"
    )

    async def _once() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=settings.tool_timeout_sec) as client:
            r = await client.get(url, params={"overview": "false"})
            r.raise_for_status()
            data = r.json()
        routes = data.get("routes") or []
        if not routes:
            raise RuntimeError("no_route")
        route = routes[0]
        distance_m = float(route.get("distance") or 0)
        out: dict[str, Any] = {
            "ok": True,
            "source": "osrm",
            "profile": osrm_profile,
            "distance_m": distance_m,
            "duration_s": route.get("duration"),
            "note": "公共 OSRM 演示服务，非实时路况保证",
        }
        if osrm_profile == "car":
            out.update(_taxi_fare_range(distance_m))
        if from_name:
            out["from_name"] = from_name
        if to_name:
            out["to_name"] = to_name
        return out

    try:
        return await once_with_retry(_once)
    except Exception as e:
        err = str(e)
        return {
            "ok": False,
            "error": "no_route" if err == "no_route" else "route_unavailable",
            "source": "osrm",
            "detail": err,
            "upstream_errors": errors or None,
            "note": "路线服务不可用；不编造距离/时长",
        }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "geocode_location",
        geocode_location,
        description=(
            'Geocode place name (Amap if AMAP_API_KEY else Open-Meteo/Photon/Nominatim). '
            'Action Input: {"query":"中卫"}'
        ),
        permission="low",
    )
    registry.register(
        "search_places",
        search_places,
        description=(
            'Search tourist POIs (Amap if AMAP_API_KEY, else Overpass). '
            'Action Input: {"city":"中卫","limit":5}. Never invents POIs.'
        ),
        permission="low",
    )
    registry.register(
        "search_food_nearby",
        search_food_nearby,
        description=(
            "Food search. meal=lunch: near POI (~1.5km). meal=dinner: city-wide (pass city=). "
            "Pass budget_style=budget|mid|comfort or budget_cny+days to rank by 人均档位. "
            'Action Input: {"lat":37.53,"lon":104.99,"near_name":"沙坡头","meal":"lunch",'
            '"budget_style":"budget","limit":5} '
            'or {"city":"中卫","lat":37.53,"lon":104.99,"meal":"dinner","budget_cny":1500,"days":2,'
            '"hotel_lat":37.52,"hotel_lon":105.19,"limit":8}'
        ),
        permission="low",
    )
    registry.register(
        "search_hotels",
        search_hotels,
        description=(
            "Hotels near lat/lon (preferred) or city; no booking. "
            "Pass budget_style or budget_cny+days to prefer in-band 人均. "
            'Action Input: {"lat":37.53,"lon":104.99,"near_name":"沙坡头","budget_style":"budget","limit":5}'
            ' or {"city":"中卫","budget_cny":15000,"days":2,"limit":5}'
        ),
        permission="low",
    )
    registry.register(
        "estimate_route",
        estimate_route,
        description=(
            "Route via Amap if key else OSRM; driving adds taxi_cny_range. "
            'Action Input: {"from_lat":37.53,"from_lon":104.99,"to_lat":37.46,"to_lon":105.0,'
            '"profile":"car","from_name":"沙坡头","to_name":"黄河区"}'
        ),
        permission="low",
    )
