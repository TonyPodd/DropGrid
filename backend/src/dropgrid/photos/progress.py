"""Operation-local progress; synchronous API calls need no durable job."""

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

Progress = Callable[[str, int, int | None, dict[str, int]], Awaitable[None]]
observer: ContextVar[Progress | None] = ContextVar("photo_progress", default=None)


async def emit(stage: str, current: int = 0, total: int | None = None, **counters: int) -> None:
    callback = observer.get()
    if callback:
        await callback(stage, current, total, counters)
