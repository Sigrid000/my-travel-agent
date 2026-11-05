from __future__ import annotations

import json
import re
from typing import Any

REQUIRED_PLAN_TOOLS: tuple[str, ...] = (
    "search_places",
    "search_food_nearby",
    "search_hotels",
    "get_weather",
    "estimate_route",
    "estimate_budget",
    "suggest_packing",
)

# FINAL must contain these section cues (Chinese headings or key phrases).
REQUIRED_FINAL_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("门票核验/OTA", ("门票", "ticket_verify", "携程", "美团")),
    ("住宿候选", ("住宿", "酒店")),
    ("美食参考", ("美食参考", "人均", "午餐", "晚饭", "晚餐")),
    ("分项预算", ("预算", "住宿", "餐饮", "交通")),
    ("建议携带", ("建议携带", "携带清单", "打包")),
    ("数据来源", ("数据来源", "高德", "来源")),
)


def missing_required_plan_tools(observations: list[dict[str, Any]]) -> list[str]:
    called = {str(o.get("tool") or "") for o in observations}
    return [name for name in REQUIRED_PLAN_TOOLS if name not in called]


def _food_meals(observations: list[dict[str, Any]]) -> set[str]:
    meals: set[str] = set()
    for o in observations:
        if o.get("tool") != "search_food_nearby":
            continue
        args = o.get("args") or {}
        meal = str(args.get("meal") or args.get("purpose") or "lunch").strip().lower()
        if meal in {"dinner", "supper", "evening", "晚饭", "晚餐"}:
            meals.add("dinner")
        else:
            meals.add("lunch")
    return meals


def missing_food_meal_coverage(observations: list[dict[str, Any]]) -> list[str]:
    """Require at least one lunch + one city/dinner food search."""
    meals = _food_meals(observations)
    missing: list[str] = []
    if "lunch" not in meals:
        missing.append("search_food_nearby(meal=lunch)")
    if "dinner" not in meals:
        missing.append("search_food_nearby(meal=dinner,city=…)")
    return missing


def _budget_args(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    for o in reversed(observations):
        if o.get("tool") == "estimate_budget":
            args = o.get("args")
            return args if isinstance(args, dict) else {}
    return None


def _parse_list(val: Any) -> list[Any]:
    if val is None or val == "":
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def missing_budget_alignment(observations: list[dict[str, Any]]) -> list[str]:
    """If routes/food costs exist in tools, budget call must ingest them."""
    issues: list[str] = []
    budget_args = _budget_args(observations)
    if budget_args is None:
        return issues

    route_ok = False
    for o in observations:
        if o.get("tool") != "estimate_route":
            continue
        res = o.get("result") or {}
        if res.get("ok") and res.get("distance_m"):
            route_ok = True
            break
    legs = _parse_list(budget_args.get("route_legs"))
    if route_ok and not legs:
        issues.append("estimate_budget 缺少 route_legs（须汇总已成功的 estimate_route distance_m）")

    food_costs: list[Any] = []
    for o in observations:
        if o.get("tool") != "search_food_nearby":
            continue
        res = o.get("result") or {}
        for p in res.get("places") or []:
            if not isinstance(p, dict):
                continue
            c = p.get("avg_cost_per_person_cny", p.get("amap_avg_cost"))
            if c is not None:
                food_costs.append(c)
    hints = _parse_list(budget_args.get("food_cost_hints"))
    if food_costs and not hints:
        issues.append("estimate_budget 缺少 food_cost_hints（须带上美食工具返回的人均）")

    return issues


def missing_final_sections(text: str) -> list[str]:
    missing: list[str] = []
    for label, markers in REQUIRED_FINAL_MARKERS:
        if not any(m in text for m in markers):
            missing.append(label)
    return missing


def collect_plan_gate_failures(
    observations: list[dict[str, Any]], final_text: str | None = None
) -> list[str]:
    failures: list[str] = []
    failures.extend(missing_required_plan_tools(observations))
    failures.extend(missing_food_meal_coverage(observations))
    failures.extend(missing_budget_alignment(observations))
    if final_text is not None:
        failures.extend(missing_final_sections(final_text))
    return failures


def nudge_for_failures(failures: list[str]) -> str:
    joined = "；".join(failures)
    return (
        "[系统校验] 完整行程 FINAL 被拒绝，原因：\n"
        f"- {joined}\n"
        "请补齐后继续：午餐 meal=lunch、晚饭 meal=dinner（建议带 city）、"
        "suggest_packing、estimate_budget 必须含 route_legs 与 food_cost_hints（若工具已有人均）、"
        "FINAL 须含：门票核验/OTA 链接、住宿表、美食参考（含人均）、分项预算、建议携带、数据来源。"
        "勿编造票价/人均/路程。"
    )
