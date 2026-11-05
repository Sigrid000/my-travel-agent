from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.plan_gates import (
    REQUIRED_PLAN_TOOLS,
    collect_plan_gate_failures,
    missing_required_plan_tools,
    nudge_for_failures,
)
from app.config import Settings, get_settings
from app.llm.client import LLMClient
from app.memory.short_term import ShortTermMemory
from app.observability.trace import TraceRecorder
from app.safety.guard import SafetyGuard
from app.tools.registry import ToolRegistry, to_jsonable

# Re-export for tests/eval that import from loop
__all__ = [
    "ReActLoop",
    "LoopResult",
    "REQUIRED_PLAN_TOOLS",
    "missing_required_plan_tools",
    "SYSTEM_PROMPT",
]


_ACTION_RE = re.compile(
    r"Action\s*:\s*([a-zA-Z0-9_]+)\s*\nAction Input\s*:\s*(\{.*\}|\[.*\]|.+)",
    re.S | re.I,
)
_FINAL_RE = re.compile(r"FINAL\s*:\s*(.*)", re.S | re.I)


SYSTEM_PROMPT = """你是休闲旅行规划 Agent。用中文回复。
你可以通过工具获取真实/区间信息，禁止编造精确票价或假装已付款下单。
禁止编造无工具来源的 POI、坐标、路线距离、餐厅、酒店；工具失败时宁可说明无法可靠规划。

可用工具：
{tools}

输出格式二选一：
1) 需要工具时（每次回复只允许一个 Action，等 Observation 后再调下一个）：
Thought: ...
Action: tool_name
Action Input: {{JSON}}

2) 结束时 FINAL 必须是 Markdown，且包含以下标题（缺一会被代码拒绝）：
## 约束摘要
## 每日行程（含午餐建议）
## 住宿候选
## 美食参考（自选）
## 门票核验
## 分项预算
## 建议携带
## 风险与提醒
## 需确认事项
## 数据来源

参数示例：
- search_places: {{"city":"中卫","limit":6}}
- search_food_nearby 午餐: {{"lat":..,"lon":..,"near_name":"沙坡头","meal":"lunch","limit":5}}
- search_food_nearby 晚饭全市: {{"city":"中卫","lat":..,"lon":..,"meal":"dinner","hotel_lat":..,"hotel_lon":..,"limit":8}}
- search_hotels: {{"lat":..,"lon":..,"near_name":"沙坡头","limit":5}}
- get_weather / estimate_route / estimate_budget / suggest_packing
- estimate_budget 必须带 route_legs 与 food_cost_hints（有人均时）:
  {{"city":"中卫","days":3,"budget_cny":3000,"route_legs":[{{"from_name":"A","to_name":"B","distance_m":12000}}],"attraction_count":4,"food_cost_hints":[{{"amap_avg_cost":58}}],"hotel_cost_hints":[]}}
- suggest_packing: {{"city":"中卫","days":3,"weather_scenario":"sunny","interests":["沙漠","黄河"]}}

规则：
- 完整行程前须调用：search_places、search_food_nearby（至少一次 lunch + 一次 dinner）、search_hotels、get_weather、estimate_route、estimate_budget、suggest_packing
- 多日行程：尽量为主要游览日各调一次 lunch（步数不够时至少 1 次 lunch + 1 次 dinner）
- 门票：FINAL「门票核验」列出主要景点的 ticket_verify 链接（携程/美团）+ tel/opentime；不编造精确票价
- 美食参考：写人均 avg_cost_per_person_cny、距景点/酒店；无人均写「高德未提供」
- 住宿：表格式列出名称、距离、评分/人均（若有）
- 建议携带：必须使用 suggest_packing 的 checklist，可略作整理
- 预算：必须与 route_legs、food_cost_hints 对齐
- 工具 ok=false：不得用记忆补全；预订只能 request_confirmation
"""


@dataclass
class LoopResult:
    final_text: str
    steps: int = 0
    tool_calls: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)
    rejected_final: int = 0


class ReActLoop:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        *,
        settings: Settings | None = None,
        guard: SafetyGuard | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.settings = settings or get_settings()
        self.guard = guard or SafetyGuard()

    async def run(
        self,
        user_message: str,
        memory: ShortTermMemory,
        trace: TraceRecorder,
    ) -> LoopResult:
        memory.add("user", user_message)
        system = SYSTEM_PROMPT.format(tools=self.registry.catalog_for_prompt())
        observations: list[dict[str, Any]] = []
        steps = 0
        tool_calls = 0
        rejected_final = 0

        for step in range(1, self.settings.max_agent_steps + 1):
            steps = step
            messages = [{"role": "system", "content": system}] + memory.as_messages()
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "继续规划。优先补齐：lunch/dinner 美食、suggest_packing、"
                        "带 route_legs+food_cost_hints 的 estimate_budget。"
                        "FINAL 必须含门票核验、住宿、美食参考（人均）、分项预算、建议携带、数据来源。"
                        "若门禁已满足则输出 FINAL，否则只调一个工具。"
                    ),
                }
            )
            trace.log("llm_request", step=step, prompt_chars=sum(len(m["content"]) for m in messages))
            resp = await self.llm.chat(messages)
            trace.add_tokens(resp.prompt_tokens, resp.completion_tokens)
            trace.log(
                "llm_response",
                step=step,
                content_preview=resp.content[:500],
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
            )
            memory.add("assistant", resp.content)
            trace.log("agent_step", step=step)

            final_m = _FINAL_RE.search(resp.content)
            action_m = _ACTION_RE.search(resp.content)
            wants_final = bool(final_m and not action_m) or (not action_m and not final_m)
            if wants_final:
                text = final_m.group(1).strip() if final_m and not action_m else resp.content.strip()
                failures = collect_plan_gate_failures(observations, text)
                if failures:
                    rejected_final += 1
                    nudge = nudge_for_failures(failures)
                    memory.add("user", nudge)
                    trace.log("final_rejected", failures=failures, step=step)
                    if step >= self.settings.max_agent_steps:
                        return LoopResult(
                            final_text=(
                                "无法输出完整行程：校验未通过（"
                                + "；".join(failures)
                                + "）。请重试或缩小需求。"
                            ),
                            steps=steps,
                            tool_calls=tool_calls,
                            observations=observations,
                            rejected_final=rejected_final,
                        )
                    continue
                return LoopResult(
                    final_text=text,
                    steps=steps,
                    tool_calls=tool_calls,
                    observations=observations,
                    rejected_final=rejected_final,
                )

            tool_name = action_m.group(1).strip()
            raw_args = action_m.group(2).strip()
            try:
                args = json.loads(raw_args)
                if not isinstance(args, dict):
                    args = {"value": args}
            except json.JSONDecodeError:
                args = {"text": raw_args.strip().strip('"')}

            if not self.registry.has(tool_name):
                obs = {"ok": False, "error": f"unknown_tool:{tool_name}"}
            else:
                reg = self.registry.get(tool_name)
                decision = self.guard.check_tool(tool_name, reg.permission)
                if decision.blocked and tool_name != "request_confirmation":
                    obs = {"ok": False, "error": decision.reason, "message": decision.reply}
                else:
                    if tool_calls >= self.settings.max_tool_calls:
                        obs = {"ok": False, "error": "max_tool_calls"}
                    else:
                        try:
                            result = await self.registry.invoke(tool_name, args)
                            obs = to_jsonable(result)
                        except Exception as e:
                            obs = {"ok": False, "error": str(e)}
                        tool_calls += 1
                        trace.tool_calls = tool_calls

            observations.append({"tool": tool_name, "args": args, "result": obs})
            obs_text = json.dumps(obs, ensure_ascii=False)[:4000]
            memory.add("tool", obs_text, name=tool_name)
            trace.log("tool_result", tool=tool_name, args=args, result_preview=obs_text[:800])

        failures = collect_plan_gate_failures(observations)
        suffix = f"（仍缺：{'；'.join(failures)}）" if failures else ""
        return LoopResult(
            final_text=f"已达最大步数{suffix}。请根据已有 Observation 缩小需求再试。",
            steps=steps,
            tool_calls=tool_calls,
            observations=observations,
            rejected_final=rejected_final,
        )
