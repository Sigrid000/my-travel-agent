from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SafetyDecision:
    allow_agent: bool
    blocked: bool
    reason: str
    reply: str | None = None


_PAYMENT = re.compile(
    r"(自动付款|直接付款|帮我付款|帮我支付|下单支付|用我的卡|信用卡|银行卡号)",
    re.I,
)
_BOOKING = re.compile(
    r"(自动预订|直接预订|帮我订票|帮我订酒店|一键下单|立刻下单|完成下单)",
    re.I,
)
_CREDS = re.compile(
    r"(护照号|身份证号|密码是|验证码发给你|卡号是|cvv|有效期)",
    re.I,
)


class SafetyGuard:
    """High-risk action detector — refuse side effects, offer human confirmation options."""

    def check_user_message(self, text: str) -> SafetyDecision:
        raw = (text or "").strip()
        if not raw:
            return SafetyDecision(True, False, "empty_ok")

        if _CREDS.search(raw):
            return SafetyDecision(
                allow_agent=False,
                blocked=True,
                reason="credentials",
                reply=(
                    "我不能接收或保存身份证、护照、银行卡号或密码。"
                    "请在官方 App/网站自行填写。如需行程建议，可只说城市、天数、预算和兴趣。"
                ),
            )
        if _PAYMENT.search(raw) or _BOOKING.search(raw):
            return SafetyDecision(
                allow_agent=False,
                blocked=True,
                reason="payment_or_booking",
                reply=(
                    "我可以帮你规划行程并给出预订选项，但不会自动付款或代下单。"
                    "请你在官方渠道自行确认与支付。\n"
                    "可选下一步：1) 继续只要行程方案 2) 我列出推荐酒店/交通搜索关键词供你自行预订。"
                ),
            )
        return SafetyDecision(True, False, "ok")

    def check_tool(self, tool_name: str, permission: str) -> SafetyDecision:
        if permission == "high" and tool_name not in {"request_confirmation"}:
            return SafetyDecision(
                False,
                True,
                "high_permission_blocked",
                reply="高风险工具未授权自动执行。",
            )
        return SafetyDecision(True, False, "ok")
