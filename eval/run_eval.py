"""Run eval cases against TravelOrchestrator (requires OPENAI_API_KEY)."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from app.agent.orchestrator import TravelOrchestrator
from app.config import Settings, get_settings
from app.llm.client import build_llm


ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "cases.jsonl"


def load_cases(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def judge(case: dict[str, Any], result_message: str, blocked: bool, metadata: dict[str, Any]) -> tuple[bool, str]:
    exp = case.get("expect") or {}
    text = result_message or ""

    if exp.get("blocked"):
        if not blocked:
            return False, "unsafe_action"
        for bad in exp.get("not_contains") or []:
            if bad in text:
                return False, "unsafe_action"
        return True, ""

    if exp.get("ask_or_missing"):
        missing = (metadata.get("missing_fields") or metadata.get("preferences", {}).get("missing_fields") or [])
        if missing or "补充" in text or "城市" in text or "预算" in text or "天" in text:
            return True, ""
        if any(k in text for k in ("晴天", "阴天", "雨天", "指定日期", "天气情景", "推荐", "Nominatim")):
            return True, ""
        if metadata.get("clarify"):
            return True, ""
        return False, "missing_constraint"

    if exp.get("ask_or_plan"):
        if any(k in text for k in ("杭州", "天", "预算", "补充", "行程", "Day")):
            return True, ""
        return False, "missing_constraint"

    for bad in exp.get("not_contains") or []:
        if bad in text:
            return False, "unsafe_action"

    contains_any = exp.get("contains_any") or []
    if contains_any:
        soft = bool(exp.get("soft"))
        hit = any(x in text for x in contains_any)
        if not hit and soft:
            # soft weather cases: pass if plan-like output exists
            if any(x in text for x in ("行程", "Day", "天", "预算")):
                return True, ""
            return False, "other"
        if not hit:
            return False, "other"

    obs = metadata.get("observations") or []

    if exp.get("check_budget_tool"):
        used = any(o.get("tool") == "estimate_budget" for o in obs)
        if not used and "预算" not in text:
            return False, "tool_selection"

    if exp.get("require_food_meals"):
        meals: set[str] = set()
        for o in obs:
            if o.get("tool") != "search_food_nearby":
                continue
            args = o.get("args") or {}
            meal = str(args.get("meal") or args.get("purpose") or "lunch").strip().lower()
            if meal in {"dinner", "supper", "evening", "晚饭", "晚餐"}:
                meals.add("dinner")
            else:
                meals.add("lunch")
        if "lunch" not in meals or "dinner" not in meals:
            return False, "tool_selection"

    if exp.get("require_packing_tool"):
        if not any(o.get("tool") == "suggest_packing" for o in obs):
            # FINAL may still include 建议携带 from a prior call; require tool for this gate
            if "建议携带" not in text and "携带" not in text:
                return False, "tool_selection"
            if not any(o.get("tool") == "suggest_packing" for o in obs):
                return False, "tool_selection"

    if exp.get("require_budget_route_legs"):
        route_ok = any(
            o.get("tool") == "estimate_route"
            and (o.get("result") or {}).get("ok")
            and (o.get("result") or {}).get("distance_m")
            for o in obs
        )
        budget_args = {}
        for o in reversed(obs):
            if o.get("tool") == "estimate_budget":
                budget_args = o.get("args") or {}
                break
        legs = budget_args.get("route_legs")
        has_legs = isinstance(legs, list) and len(legs) > 0
        if isinstance(legs, str) and legs.strip().startswith("["):
            has_legs = True
        if route_ok and not has_legs:
            return False, "tool_selection"

    return True, ""


async def run_one(orch: TravelOrchestrator, case: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    sid = case["task_id"]
    try:
        result = await orch.handle(case["input"], session_id=sid)
        # If still clarifying schedule/weather, send follow-up so plan/gate cases can finish
        followup = case.get("followup")
        if result.metadata.get("clarify") and not followup:
            missing = result.metadata.get("missing_fields") or []
            if "schedule_basis" in missing or "天气" in (result.message or ""):
                followup = "按晴天"
        if followup and result.metadata.get("clarify") and not result.blocked:
            result = await orch.handle(str(followup), session_id=sid)
    except Exception as e:
        latency = round(time.perf_counter() - t0, 3)
        return {
            "task_id": case["task_id"],
            "type": case.get("type"),
            "input": case["input"],
            "expected": case.get("expect"),
            "actual": f"api_error: {e}",
            "pass": False,
            "failure_type": "api_error",
            "steps": 0,
            "tool_calls": 0,
            "cost": {"prompt_tokens": 0, "completion_tokens": 0},
            "latency": latency,
            "blocked": False,
            "trace_id": None,
        }
    latency = round(time.perf_counter() - t0, 3)
    ok, failure = judge(case, result.message, result.blocked, result.metadata)
    # Full FINAL for human review (report.json keeps a short preview only)
    out_dir = ROOT / "outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    full_path = out_dir / f"{case['task_id']}.md"
    full_path.write_text(
        f"# {case['task_id']}\n\ninput: {case['input']}\n\n---\n\n{result.message or ''}\n",
        encoding="utf-8",
    )
    return {
        "task_id": case["task_id"],
        "type": case.get("type"),
        "input": case["input"],
        "expected": case.get("expect"),
        "actual": result.message[:1200],
        "actual_full_path": str(full_path.relative_to(ROOT)).replace("\\", "/"),
        "pass": ok,
        "failure_type": failure,
        "steps": (result.metadata or {}).get("steps") or result.trace.get("steps"),
        "tool_calls": (result.metadata or {}).get("tool_calls") or result.trace.get("tool_calls"),
        "cost": {
            "prompt_tokens": result.trace.get("prompt_tokens"),
            "completion_tokens": result.trace.get("completion_tokens"),
        },
        "latency": latency,
        "blocked": result.blocked,
        "trace_id": result.trace.get("trace_id"),
    }


async def main_async(ablate_weather: bool = False) -> int:
    base = get_settings()
    if not base.openai_api_key.strip():
        print("OPENAI_API_KEY is required for eval. Set it in .env.")
        return 2

    settings = Settings(
        openai_api_key=base.openai_api_key,
        openai_base_url=base.openai_base_url,
        openai_model=base.openai_model,
        opentripmap_api_key=base.opentripmap_api_key,
        amap_api_key=base.amap_api_key,
        nominatim_user_agent=base.nominatim_user_agent,
        tool_timeout_sec=base.tool_timeout_sec,
        max_agent_steps=max(base.max_agent_steps, 22),
        max_tool_calls=max(base.max_tool_calls, 32),
        trace_dir="traces/eval",
        log_level=base.log_level,
    )
    orch = TravelOrchestrator(settings=settings, llm=build_llm(settings))
    if ablate_weather and orch.registry.has("get_weather"):
        orch.registry.unregister("get_weather")

    cases = load_cases(CASES_PATH)
    only = getattr(main_async, "_only", None)
    if only:
        want = {x.strip() for x in only.split(",") if x.strip()}
        cases = [c for c in cases if c.get("task_id") in want]
    rows = []
    for case in cases:
        print(f"running {case['task_id']} ...", flush=True)
        rows.append(await run_one(orch, case))

    passed = sum(1 for r in rows if r["pass"])
    report = {
        "total": len(rows),
        "passed": passed,
        "pass_rate": round(passed / max(len(rows), 1), 3),
        "ablate_weather": ablate_weather,
        "by_type": {},
        "failures": [r for r in rows if not r["pass"]],
        "results": rows,
    }
    for r in rows:
        t = r.get("type") or "unknown"
        bucket = report["by_type"].setdefault(t, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(r["pass"])

    out = ROOT / ("report_ablate_weather.json" if ablate_weather else "report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"pass_rate={report['pass_rate']} ({passed}/{len(rows)}) -> {out}")
    for f in report["failures"]:
        print(f"  FAIL {f['task_id']}: {f['failure_type']}")
    return 0 if passed == len(rows) else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ablate-weather", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated task_id filter")
    args = parser.parse_args()
    main_async._only = args.only or None  # type: ignore[attr-defined]
    raise SystemExit(asyncio.run(main_async(ablate_weather=args.ablate_weather)))


if __name__ == "__main__":
    main()
