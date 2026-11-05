from app.safety.guard import SafetyGuard


def test_block_payment():
    d = SafetyGuard().check_user_message("帮我自动付款订酒店")
    assert d.blocked
    assert d.allow_agent is False


def test_block_credentials():
    d = SafetyGuard().check_user_message("我的身份证号是110101199001011234")
    assert d.blocked


def test_allow_normal():
    d = SafetyGuard().check_user_message("去杭州玩两天预算2000")
    assert not d.blocked
