from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T", bound=Callable[..., Awaitable[Any]])


@dataclass
class RegisteredTool:
    name: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    permission: str = "low"  # low | high
    json_schema: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        handler: T,
        *,
        description: str = "",
        permission: str = "low",
        json_schema: dict[str, Any] | None = None,
    ) -> T:
        self._tools[name] = RegisteredTool(
            name=name,
            description=(description or inspect.getdoc(handler) or "").strip(),
            handler=handler,
            permission=permission,
            json_schema=json_schema or {},
        )
        return handler

    def get(self, name: str) -> RegisteredTool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def list_tools(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def catalog_for_prompt(self) -> str:
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t.name} [{t.permission}]: {t.description}")
        return "\n".join(lines)

    async def invoke(self, name: str, arguments: Mapping[str, Any] | None = None) -> Any:
        tool = self.get(name)
        args = dict(arguments or {})
        result = tool.handler(**args)
        if inspect.isawaitable(result):
            return await result
        return result


def to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, (dict, list, str, int, float, bool)) or obj is None:
        return obj
    return json.loads(json.dumps(obj, default=str))
