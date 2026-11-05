from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.agent.destination_suggest import (
    format_destination_suggestions,
    propose_destination_candidates,
)
from app.agent.intent import Intent, classify_intent
from app.agent.loop import ReActLoop
from app.config import Settings, get_settings
from app.llm.client import LLMClient, build_llm
from app.memory.short_term import ShortTermMemory
from app.observability.trace import TraceRecorder
from app.safety.guard import SafetyGuard
from app.tools.builtin import register_builtin_tools
from app.tools.builtin.preferences import (
    TravelPreferences,
    ask_message_for,
    extract_preferences_with_llm,
    merge_preferences,
)
from app.tools.registry import ToolRegistry


@dataclass
class OrchestratorResult:
    session_id: str
    message: str
    intent: str
    lane: str
    blocked: bool = False
    trace: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class TravelOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm = llm or build_llm(self.settings)
        self.registry = registry or ToolRegistry()
        if registry is None:
            register_builtin_tools(self.registry)
        self.guard = SafetyGuard()
        self.loop = ReActLoop(self.llm, self.registry, settings=self.settings, guard=self.guard)
        self._sessions: dict[str, ShortTermMemory] = {}
        self._prefs: dict[str, TravelPreferences] = {}

    def memory_for(self, session_id: str) -> ShortTermMemory:
        if session_id not in self._sessions:
            self._sessions[session_id] = ShortTermMemory(session_id=session_id)
        return self._sessions[session_id]

    def prefs_for(self, session_id: str) -> TravelPreferences | None:
        return self._prefs.get(session_id)

    async def handle(self, message: str, session_id: str | None = None) -> OrchestratorResult:
        sid = session_id or str(uuid.uuid4())
        trace = TraceRecorder(session_id=sid, trace_dir=self.settings.trace_dir)
        trace.log("user_message", content=message)

        safety = self.guard.check_user_message(message)
        intent = classify_intent(message, safety_blocked=safety.blocked)
        trace.log("intent", intent=intent.intent.value, lane=intent.lane, reason=intent.reason)

        if safety.blocked:
            reply = safety.reply or "该请求涉及高风险操作，已拒绝自动执行。"
            trace.log("safety_block", reason=safety.reason)
            return OrchestratorResult(
                session_id=sid,
                message=reply,
                intent=intent.intent.value,
                lane=intent.lane,
                blocked=True,
                trace=trace.summary(),
                metadata={"safety_reason": safety.reason},
            )

        if intent.intent == Intent.GREETING:
            reply = (
                "你好，我是旅行规划助手。告诉我城市、天数、预算和兴趣"
                "（例如：去杭州玩 2 天，预算 2000，喜欢博物馆）。"
                "确定天数后，请再告知具体日期，或选择按「晴天 / 阴天 / 雨天」情景试规划。"
                "我不会自动付款或代下单。"
            )
            self.memory_for(sid).add("user", message)
            self.memory_for(sid).add("assistant", reply)
            return OrchestratorResult(
                session_id=sid,
                message=reply,
                intent=intent.intent.value,
                lane=intent.lane,
                trace=trace.summary(),
            )

        delta = await extract_preferences_with_llm(self.llm, message)
        merged = merge_preferences(self._prefs.get(sid), delta)
        self._prefs[sid] = merged
        trace.log(
            "preferences",
            summary=merged.summary,
            missing=merged.missing_fields,
            start_date=merged.start_date,
            weather_scenario=merged.weather_scenario,
        )

        # Missing destination but has interests → suggest cities (LLM + geocode), not hardcoded list
        if "city" in merged.missing_fields and merged.interests:
            suggest = await propose_destination_candidates(
                self.llm,
                interests=merged.interests,
                notes=merged.notes,
                origin_city=merged.origin_city,
                days=merged.days,
                budget_cny=merged.budget_cny,
            )
            trace.add_tokens(
                int(suggest.get("prompt_tokens") or 0),
                int(suggest.get("completion_tokens") or 0),
            )
            reply = format_destination_suggestions(
                suggest,
                interests=merged.interests,
                still_missing=merged.missing_fields,
            )
            self.memory_for(sid).add("user", message)
            self.memory_for(sid).add("assistant", reply)
            trace.log(
                "destination_suggest",
                ok=suggest.get("ok"),
                candidates=[c.get("city") for c in (suggest.get("candidates") or [])],
                error=suggest.get("error"),
            )
            return OrchestratorResult(
                session_id=sid,
                message=reply,
                intent=intent.intent.value,
                lane="suggest",
                trace=trace.summary(),
                metadata={
                    "clarify": True,
                    "destination_suggest": suggest,
                    "preferences": merged.model_dump(),
                    "missing_fields": merged.missing_fields,
                },
            )

        # Deterministic clarify: dates OR sunny/cloudy/rainy scenario
        if merged.missing_fields:
            reply = ask_message_for(merged)
            self.memory_for(sid).add("user", message)
            self.memory_for(sid).add("assistant", reply)
            trace.log("clarify", missing=merged.missing_fields)
            return OrchestratorResult(
                session_id=sid,
                message=reply,
                intent=intent.intent.value,
                lane="fast",
                trace=trace.summary(),
                metadata={
                    "clarify": True,
                    "preferences": merged.model_dump(),
                    "missing_fields": merged.missing_fields,
                },
            )

        mem = self.memory_for(sid)
        enriched = (
            f"{message}\n\n[已确认约束 JSON]\n{json.dumps(merged.model_dump(), ensure_ascii=False)}"
        )
        result = await self.loop.run(enriched, mem, trace)
        trace.log("final", preview=result.final_text[:500])
        return OrchestratorResult(
            session_id=sid,
            message=result.final_text,
            intent=intent.intent.value,
            lane=intent.lane,
            trace=trace.summary(),
            metadata={
                "steps": result.steps,
                "tool_calls": result.tool_calls,
                "observations": result.observations,
                "preferences": merged.model_dump(),
            },
        )
