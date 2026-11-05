import pytest

from app.tools.http_retry import once_with_retry


@pytest.mark.asyncio
async def test_once_with_retry_succeeds_on_second_try():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("busy")
        return "ok"

    out = await once_with_retry(flaky, delay_sec=0.01)
    assert out == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_once_with_retry_raises_after_second_failure():
    async def always_fail():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError, match="down"):
        await once_with_retry(always_fail, delay_sec=0.01)
