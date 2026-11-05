from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")

DEFAULT_RETRY_DELAY_SEC = 0.8


async def once_with_retry(
    op: Callable[[], Awaitable[T]],
    *,
    delay_sec: float = DEFAULT_RETRY_DELAY_SEC,
) -> T:
    """Run async op; on exception wait briefly and try exactly one more time."""
    try:
        return await op()
    except Exception:
        await asyncio.sleep(max(0.0, delay_sec))
        return await op()
