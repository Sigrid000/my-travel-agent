from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ChatTurn:
    role: Role
    content: str
    name: str | None = None


@dataclass
class ShortTermMemory:
    """Sliding-window chat memory per session."""

    session_id: str
    max_turns: int = 24
    turns: list[ChatTurn] = field(default_factory=list)

    def add(self, role: Role, content: str, name: str | None = None) -> None:
        self.turns.append(ChatTurn(role=role, content=content, name=name))
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns :]

    def as_messages(self) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for t in self.turns:
            msg: dict[str, str] = {"role": t.role if t.role != "tool" else "user", "content": t.content}
            if t.name and t.role == "tool":
                msg["content"] = f"[tool:{t.name}] {t.content}"
            out.append(msg)
        return out

    def transcript(self) -> str:
        lines = []
        for t in self.turns:
            prefix = t.name or t.role
            lines.append(f"{prefix}: {t.content}")
        return "\n".join(lines)
