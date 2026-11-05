from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.tools.builtin.geo import estimate_route, geocode_location, search_places


@pytest.mark.asyncio
async def test_search_places_no_curated_fallback_on_overpass_failure():
    geo_ok = {
        "ok": True,
        "results": [{"display_name": "杭州", "lat": 30.2741, "lon": 120.1551}],
    }
    with (
        patch("app.tools.builtin.geo.geocode_location", new=AsyncMock(return_value=geo_ok)),
        patch(
            "app.tools.builtin.geo._search_overpass",
            new=AsyncMock(side_effect=RuntimeError("502 busy")),
        ),
        patch("app.tools.builtin.geo.get_settings") as gs,
    ):
        gs.return_value.opentripmap_api_key = ""
        gs.return_value.amap_api_key = ""
        gs.return_value.tool_timeout_sec = 15
        gs.return_value.nominatim_user_agent = "my-travel-agent/test"
        out = await search_places(city="杭州", limit=5)

    assert out["ok"] is False
    assert out["error"] == "poi_unavailable"
    assert out["places"] == []
    assert "西湖" not in str(out)


@pytest.mark.asyncio
async def test_geocode_no_local_city_coords_on_network_failure():
    with (
        patch("app.tools.builtin.geo.get_settings") as gs,
        patch("app.tools.builtin.geo.httpx.AsyncClient") as client_cls,
    ):
        gs.return_value.tool_timeout_sec = 5
        gs.return_value.amap_api_key = ""
        gs.return_value.nominatim_user_agent = "my-travel-agent/test"
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("blocked"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        out = await geocode_location("杭州")

    assert out["ok"] is False
    assert out["error"] == "geocode_failed"
    assert "results" not in out or not out.get("results")
    assert "local_fallback" not in str(out)
    assert "open-meteo" in (out.get("upstream_errors") or {})


@pytest.mark.asyncio
async def test_geocode_uses_open_meteo_when_available():
    payload = {
        "results": [
            {
                "name": "中卫",
                "latitude": 37.51129,
                "longitude": 105.19067,
                "admin1": "宁夏",
                "country": "中国",
            }
        ]
    }

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return payload

    with (
        patch("app.tools.builtin.geo.get_settings") as gs,
        patch("app.tools.builtin.geo.httpx.AsyncClient") as client_cls,
    ):
        gs.return_value.tool_timeout_sec = 5
        gs.return_value.amap_api_key = ""
        gs.return_value.nominatim_user_agent = "my-travel-agent/test"
        client = AsyncMock()
        client.get = AsyncMock(return_value=_Resp())
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        out = await geocode_location(query="中卫")

    assert out["ok"] is True
    assert out["source"] == "open-meteo"
    assert out["results"][0]["lat"] == pytest.approx(37.51129)


@pytest.mark.asyncio
async def test_estimate_route_no_approx_on_osrm_failure():
    with (
        patch("app.tools.builtin.geo.get_settings") as gs,
        patch("app.tools.builtin.geo.httpx.AsyncClient") as client_cls,
    ):
        gs.return_value.tool_timeout_sec = 5
        gs.return_value.amap_api_key = ""
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        out = await estimate_route(
            from_lat=30.27,
            from_lon=120.15,
            to_lat=30.25,
            to_lon=120.16,
        )

    assert out["ok"] is False
    assert out["error"] == "route_unavailable"
    assert "approx_fallback" not in str(out)
    assert "distance_m" not in out
