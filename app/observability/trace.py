from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TraceRecorder:
    """Append-only JSONL trajectory logger."""

    session_id: str
    trace_dir: str = "traces"
    events: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.perf_counter)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tool_calls: int = 0

    def __post_init__(self) -> None:
        Path(self.trace_dir).mkdir(parents=True, exist_ok=True)
        self.trace_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        self.path = Path(self.trace_dir) / f"{self.trace_id}.jsonl"

    def log(self, kind: str, **payload: Any) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "kind": kind,
            **payload,
        }
        self.events.append(row)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def log_final(self, content: str) -> Path:
        """Persist full FINAL text (JSONL + sidecar .final.md for human review)."""
        text = content or ""
        self.log("final", content=text, content_chars=len(text))
        side = self.path.with_name(f"{self.trace_id}.final.md")
        side.write_text(text, encoding="utf-8")
        return side

    def add_tokens(self, prompt: int = 0, completion: int = 0) -> None:
        self.prompt_tokens += int(prompt or 0)
        self.completion_tokens += int(completion or 0)

    def summary(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "path": str(self.path),
            "final_path": str(self.path.with_name(f"{self.trace_id}.final.md")),
            "steps": sum(1 for e in self.events if e.get("kind") == "agent_step"),
            "tool_calls": self.tool_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_sec": round(time.perf_counter() - self.started_at, 3),
        }
