from __future__ import annotations

from typing import Any

from app.tools.registry import ToolRegistry


async def echo(text: str = "") -> dict[str, Any]:
    """Echo text back — for harness tests."""
    return {"ok": True, "echo": text}


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "echo",
        echo,
        description="Echo input text (dev/test).",
        permission="low",
        json_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    )
