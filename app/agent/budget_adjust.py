"""One-round budget feedback: over → tighten stay/food/transit; under → optional upgrade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

GapKind = Literal["over", "under"]


@dataclass
class BudgetAdjustment:
    kind: GapKind
    user_budget_cny: int
    total_low: int
    total_high: int
    gap_cny: int  # over: how much low-end exceeds budget; under: slack vs high-end
    target_style: str
    transport_mode: str
    reason: str

    def nudge_message(self) -> str:
        if self.kind == "over":
            return (
                "【预算反馈调整·第1轮·必须执行】\n"
                f"当前 estimate_budget：区间约 [{self.total_low}, {self.total_high}] 元，"
                f"用户预算 {self.user_budget_cny} 元，低端已超约 {self.gap_cny} 元（{self.reason}）。\n"
                "请按顺序只调工具（每次一个 Action），全部完成后再 FINAL：\n"
                f'1) search_hotels：budget_style="{self.target_style}"（或同传 budget_cny+days），'
                "优先 budget_fit=true 的便宜候选\n"
                f'2) search_food_nearby：meal=lunch 与 meal=dinner 各至少一次，budget_style="{self.target_style}"\n'
                f'3) estimate_budget：同一 route_legs，style="{self.target_style}"，'
                f'transport_mode="{self.transport_mode}"（市内以公交/地铁为主，打车改为可选），'
                "food_cost_hints/hotel_cost_hints 用刚搜到的更低人均\n"
                "FINAL 须增加小节「## 预算调整说明」：写清换了哪些住/吃、交通如何从打车降为公交。\n"
                "禁止在未完成上述工具前输出 FINAL。"
            )
        return (
            "【预算反馈调整·第1轮·可选升级】\n"
            f"当前 estimate_budget：区间约 [{self.total_low}, {self.total_high}] 元，"
            f"用户预算 {self.user_budget_cny} 元，相对高端仍宽裕约 {self.gap_cny} 元（{self.reason}）。\n"
            "请再调工具后 FINAL（每次一个 Action）：\n"
            f'1) search_hotels + search_food_nearby（dinner 即可）使用 budget_style="{self.target_style}"\n'
            f'2) estimate_budget：style="{self.target_style}"，transport_mode="taxi"（可保留打车体验）\n'
            "FINAL「预算调整说明」写清可升级的住/吃选项；不要无故抬高到花光预算。"
        )


def _last_budget_result(observations: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for o in reversed(observations):
        if o.get("tool") != "estimate_budget":
            continue
        result = o.get("result") or {}
        if not isinstance(result, dict) or not result.get("ok"):
            continue
        args = o.get("args") or {}
        return args if isinstance(args, dict) else {}, result
    return None


def analyze_budget_gap(
    observations: list[dict[str, Any]],
    *,
    under_ratio: float = 1.8,
) -> BudgetAdjustment | None:
    """Return one adjustment plan if clearly over or under budget; else None."""
    packed = _last_budget_result(observations)
    if not packed:
        return None
    args, result = packed
    try:
        user_budget = int(result.get("user_budget_cny") or args.get("budget_cny") or 0)
    except (TypeError, ValueError):
        user_budget = 0
    if user_budget <= 0:
        return None

    tr = result.get("total_range") or [0, 0]
    try:
        total_low = int(tr[0])
        total_high = int(tr[1])
    except (TypeError, ValueError, IndexError):
        return None

    within = result.get("within_budget_low_end")
    # Over: low-end already above budget
    if within is False or total_low > user_budget:
        gap = max(0, total_low - user_budget)
        return BudgetAdjustment(
            kind="over",
            user_budget_cny=user_budget,
            total_low=total_low,
            total_high=total_high,
            gap_cny=gap,
            target_style="budget",
            transport_mode="public",
            reason="估算低端已超过用户预算，需降住/吃/行",
        )

    # Under: budget much higher than high-end estimate
    if total_high > 0 and user_budget >= int(total_high * under_ratio):
        gap = user_budget - total_high
        return BudgetAdjustment(
            kind="under",
            user_budget_cny=user_budget,
            total_low=total_low,
            total_high=total_high,
            gap_cny=gap,
            target_style="comfort",
            transport_mode="taxi",
            reason="预算相对估算高端明显宽裕，可适度升级住/吃",
        )
    return None
