from app.agent.plan_gates import (
    collect_plan_gate_failures,
    missing_budget_alignment,
    missing_final_sections,
    missing_food_meal_coverage,
    missing_required_plan_tools,
)


def test_missing_tools_includes_packing():
    assert "suggest_packing" in missing_required_plan_tools([])


def test_food_requires_lunch_and_dinner():
    obs = [{"tool": "search_food_nearby", "args": {"meal": "lunch"}, "result": {"ok": True}}]
    assert "dinner" in " ".join(missing_food_meal_coverage(obs))
    obs.append({"tool": "search_food_nearby", "args": {"meal": "dinner", "city": "中卫"}, "result": {"ok": True}})
    assert missing_food_meal_coverage(obs) == []


def test_budget_needs_route_legs_when_routes_exist():
    obs = [
        {
            "tool": "estimate_route",
            "args": {},
            "result": {"ok": True, "distance_m": 9000},
        },
        {"tool": "estimate_budget", "args": {"city": "中卫", "days": 2}, "result": {"ok": True}},
    ]
    issues = missing_budget_alignment(obs)
    assert any("route_legs" in x for x in issues)

    obs[-1]["args"] = {
        "city": "中卫",
        "days": 2,
        "route_legs": [{"from_name": "A", "to_name": "B", "distance_m": 9000}],
        "food_cost_hints": [{"amap_avg_cost": 40}],
    }
    obs.append(
        {
            "tool": "search_food_nearby",
            "args": {"meal": "lunch"},
            "result": {"ok": True, "places": [{"name": "x", "avg_cost_per_person_cny": 40}]},
        }
    )
    assert missing_budget_alignment(obs) == []


def test_final_sections():
    weak = "# 行程\n去玩"
    assert missing_final_sections(weak)
    strong = """
## 约束摘要
## 每日行程
## 住宿候选 酒店A
## 美食参考 人均 58
## 门票核验 携程 美团
## 分项预算 交通 餐饮
## 建议携带 防晒
## 数据来源 高德
"""
    assert missing_final_sections(strong) == []


def test_collect_gates():
    fails = collect_plan_gate_failures([], "随便")
    assert "search_places" in fails
    assert any("lunch" in f for f in fails)
