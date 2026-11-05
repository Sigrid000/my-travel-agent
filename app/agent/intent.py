from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    GREETING = "greeting"
    PLAN = "plan"
    SAFETY_BLOCK = "safety_block"
    OTHER = "other"


@dataclass
class IntentResult:
    intent: Intent
    lane: str  # fast | slow
    reason: str


_GREET = re.compile(r"^(你好|您好|hi|hello|嗨)[\s!！。.]*$", re.I)


def classify_intent(text: str, *, safety_blocked: bool = False) -> IntentResult:
    if safety_blocked:
        return IntentResult(Intent.SAFETY_BLOCK, "fast", "safety_guard")
    raw = (text or "").strip()
    if _GREET.match(raw):
        return IntentResult(Intent.GREETING, "fast", "greeting_rule")
    plan_keys = ("规划", "行程", "去", "玩", "旅行", "旅游", "几天", "预算", "安排")
    if any(k in raw for k in plan_keys):
        return IntentResult(Intent.PLAN, "slow", "plan_keywords")
    return IntentResult(Intent.OTHER, "slow", "default_slow")
