from __future__ import annotations

from typing import Any

from app.tools.registry import ToolRegistry


async def request_confirmation(
    action: str = "",
    options: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Ask the user to confirm a high-risk action — never executes side effects."""
    opts = options or [
        "我只要行程方案，自己去官网预订",
        "取消该高风险请求，继续规划",
    ]
    return {
        "ok": True,
        "requires_human": True,
        "action": action or "unspecified_high_risk",
        "executed": False,
        "message": "已记录待确认事项，系统不会自动执行付款/下单/提交凭据。",
        "options": opts,
    }


def register_tools(registry: ToolRegistry) -> None:
    registry.register(
        "request_confirmation",
        request_confirmation,
        description="Request human confirmation for high-risk actions; does not execute them.",
        permission="high",
    )
