"""Per-operation safe stage durations. No identities, URLs or payloads recorded."""

from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from typing import Any, ParamSpec, TypeVar

_current: ContextVar[dict[str, float] | None] = ContextVar("photo_timings", default=None)
P = ParamSpec("P")
T = TypeVar("T")


@contextmanager
def stage(name: str) -> Iterator[None]:
    started = perf_counter()
    try:
        yield
    finally:
        values = _current.get()
        if values is not None:
            values[name] = values.get(name, 0) + (perf_counter() - started) * 1000


@contextmanager
def collect_timings() -> Iterator[dict[str, float]]:
    values = dict.fromkeys(
        (
            "query_retrieval",
            "downloads",
            "normalization",
            "candidate_embedding",
            "reference_loading",
            "archive_preparation",
            "ranking",
        ),
        0.0,
    )
    token = _current.set(values)
    try:
        yield values
    finally:
        _current.reset(token)


def timed(name: str) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Coroutine[Any, Any, T]]]:
    def decorate(fn: Callable[P, Awaitable[T]]) -> Callable[P, Coroutine[Any, Any, T]]:
        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with stage(name):
                return await fn(*args, **kwargs)

        return wrapper

    return decorate
