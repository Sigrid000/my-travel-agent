from __future__ import annotations

from app.tools.registry import ToolRegistry

from . import budget, confirmation, echo, geo, packing, preferences, weather


def register_builtin_tools(registry: ToolRegistry) -> None:
    echo.register_tools(registry)
    preferences.register_tools(registry)
    geo.register_tools(registry)
    weather.register_tools(registry)
    budget.register_tools(registry)
    packing.register_tools(registry)
    confirmation.register_tools(registry)
