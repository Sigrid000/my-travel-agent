from eval.run_eval import judge


def test_judge_require_food_meals_and_packing_and_legs():
    case = {
        "expect": {
            "contains_any": ["中卫"],
            "require_food_meals": True,
            "require_packing_tool": True,
            "require_budget_route_legs": True,
        }
    }
    meta = {
        "observations": [
            {"tool": "search_food_nearby", "args": {"meal": "lunch"}, "result": {"ok": True}},
            {
                "tool": "search_food_nearby",
                "args": {"meal": "dinner", "city": "中卫"},
                "result": {"ok": True},
            },
            {"tool": "suggest_packing", "args": {}, "result": {"ok": True}},
            {
                "tool": "estimate_route",
                "args": {},
                "result": {"ok": True, "distance_m": 9000},
            },
            {
                "tool": "estimate_budget",
                "args": {"route_legs": [{"distance_m": 9000}]},
                "result": {"ok": True},
            },
        ]
    }
    ok, err = judge(case, "中卫行程 建议携带", False, meta)
    assert ok and err == ""


def test_judge_fails_without_dinner():
    case = {"expect": {"contains_any": ["中卫"], "require_food_meals": True}}
    meta = {
        "observations": [
            {"tool": "search_food_nearby", "args": {"meal": "lunch"}, "result": {"ok": True}},
        ]
    }
    ok, err = judge(case, "中卫", False, meta)
    assert not ok and err == "tool_selection"


def test_safety_still_blocks():
    case = {"expect": {"blocked": True, "not_contains": ["已下单"]}}
    ok, err = judge(case, "需要您自行确认付款", True, {})
    assert ok
