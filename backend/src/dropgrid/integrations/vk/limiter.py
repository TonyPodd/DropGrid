import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

Sleeper = Callable[[float], Awaitable[None]]


class VKRateLimiter(Protocol):
    async def acquire(self, account_id: UUID) -> None: ...


class LocalRateLimiter:
    """Serialize starts per account; process-local, no platform-limit probing."""

    def __init__(
        self,
        interval: float = 1,
        *,
        sleeper: Sleeper = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval <= 0:
            raise ValueError("Rate interval must be positive")
        self.interval = interval
        self.sleeper = sleeper
        self.clock = clock
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._last: dict[UUID, float] = {}

    async def acquire(self, account_id: UUID) -> None:
        lock = self._locks.setdefault(account_id, asyncio.Lock())
        async with lock:
            if account_id in self._last:
                delay = self.interval - (self.clock() - self._last[account_id])
                if delay > 0:
                    await self.sleeper(delay)
            self._last[account_id] = self.clock()
