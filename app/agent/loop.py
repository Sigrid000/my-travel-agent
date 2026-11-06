from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.budget_adjust import analyze_budget_gap
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
1) 需要工具时（每次回复只允许一个 Action，等 Observation 后再调下一个；串行不可一次调多个）：
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
- search_food_nearby 午餐: {{"lat":..,"lon":..,"near_name":"沙坡头","meal":"lunch","budget_style":"budget","limit":5}}
- search_food_nearby 晚饭全市: {{"city":"中卫","lat":..,"lon":..,"meal":"dinner","budget_cny":1500,"days":2,"hotel_lat":..,"hotel_lon":..,"limit":8}}
- search_hotels: {{"lat":..,"lon":..,"near_name":"沙坡头","budget_style":"budget","limit":5}}
- estimate_route / estimate_budget / suggest_packing（get_weather 常已由编排预取）
- estimate_budget 必须带 route_legs 与 food_cost_hints（有人均时）:
  {{"city":"中卫","days":3,"budget_cny":3000,"route_legs":[{{"from_name":"A","to_name":"B","distance_m":12000}}],"attraction_count":4,"food_cost_hints":[{{"amap_avg_cost":58}}],"hotel_cost_hints":[]}}
- suggest_packing: {{"city":"中卫","days":3,"weather_scenario":"sunny","interests":["沙漠","黄河"],"temp_max_c":12,"temp_min_c":-3}}；高海拔可加 altitude_m

规则：
- 依赖顺序：先 search_places 拿景点坐标 → 再用该坐标 search_food_nearby(lunch) 与 search_hotels → dinner（全市）→ estimate_route → estimate_budget + suggest_packing → FINAL。天气影响排程（雨天偏室内），不替代 places；lunch/hotels 依赖景点坐标，places 之前不要调。
- 若消息含「[编排预取]」且天气已成功：不要再调 get_weather；packing 用预取温差/情景。
- 完整行程前须具备：search_places、search_food_nearby（至少一次 lunch + 一次 dinner）、search_hotels、get_weather（预取也算）、estimate_route、estimate_budget、suggest_packing
- 多日行程：尽量为主要游览日各调一次 lunch（步数不够时至少 1 次 lunch + 1 次 dinner）
- 消费档位：约束 JSON 里的 budget_style（经济/标准/舒适）。搜美食/酒店时传入 budget_style 或 budget_cny+days；优先选用 budget_fit=true 的候选；紧预算少安排高价体验
- 若收到「预算反馈调整」系统消息：按要求重搜住/吃并 estimate_budget(transport_mode=public 或对应档位)，FINAL 增加「预算调整说明」
- 约束摘要须写明预算金额与消费档位；分项预算用 estimate_budget 的 style/fit_note
- 门票：FINAL「门票核验」列出主要景点的 ticket_verify 链接（携程/美团）+ tel/opentime；不编造精确票价
- 美食参考：写人均 avg_cost_per_person_cny、距景点/酒店；无人均写「高德未提供」
- 住宿：表格式列出名称、距离、评分/人均（若有）；同一会话尽量沿用已给出的酒店候选，勿无故改搜成另一批
- 建议携带：必须使用 suggest_packing 的 sections/checklist（衣物、生活用品、场景提示），可略作整理；高海拔提示须保留「非医疗建议」表述
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
    budget_adjustments: int = 0


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
        *,
        seed_observations: list[dict[str, Any]] | None = None,
    ) -> LoopResult:
        memory.add("user", user_message)
        system = SYSTEM_PROMPT.format(tools=self.registry.catalog_for_prompt())
        observations: list[dict[str, Any]] = list(seed_observations or [])
        for seed in observations:
            tool = str(seed.get("tool") or "")
            if not tool:
                continue
            preview = json.dumps(seed.get("result"), ensure_ascii=False)[:4000]
            memory.add("tool", preview, name=tool)
            trace.log(
                "tool_result",
                tool=tool,
                args=seed.get("args") or {},
                result_preview=preview[:800],
                prefetched=True,
            )
        steps = 0
        tool_calls = 0
        rejected_final = 0
        budget_adjustments = 0

        for step in range(1, self.settings.max_agent_steps + 1):
            steps = step
            messages = [{"role": "system", "content": system}] + memory.as_messages()
            weather_done = any(
                o.get("tool") == "get_weather" and (o.get("result") or {}).get("ok")
                for o in observations
            )
            nudge_extra = (
                "天气已预取成功，勿再调 get_weather。"
                if weather_done
                else "若尚无天气则调 get_weather。"
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "继续规划。顺序：先 search_places，再用景点坐标做 lunch 与 search_hotels，"
                        "然后 dinner → route → budget+packing。"
                        f"{nudge_extra}"
                        "优先补齐：lunch/dinner、suggest_packing、"
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
                            budget_adjustments=budget_adjustments,
                        )
                    continue
                # One-round budget feedback before accepting FINAL
                if budget_adjustments < 1:
                    adj = analyze_budget_gap(observations)
                    if adj is not None:
                        budget_adjustments += 1
                        rejected_final += 1
                        memory.add("user", adj.nudge_message())
                        trace.log(
                            "budget_adjust",
                            kind=adj.kind,
                            gap_cny=adj.gap_cny,
                            target_style=adj.target_style,
                            transport_mode=adj.transport_mode,
                            step=step,
                        )
                        if step >= self.settings.max_agent_steps:
                            return LoopResult(
                                final_text=text,
                                steps=steps,
                                tool_calls=tool_calls,
                                observations=observations,
                                rejected_final=rejected_final,
                                budget_adjustments=budget_adjustments,
                            )
                        continue
                return LoopResult(
                    final_text=text,
                    steps=steps,
                    tool_calls=tool_calls,
                    observations=observations,
                    rejected_final=rejected_final,
                    budget_adjustments=budget_adjustments,
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
            budget_adjustments=budget_adjustments,
        )
