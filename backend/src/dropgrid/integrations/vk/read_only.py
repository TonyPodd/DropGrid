"""Task-local preparation boundary. Never carries credentials."""

from contextvars import ContextVar

from dropgrid.integrations.vk.errors import VKWriteDisabledError

preparation_read_only: ContextVar[bool] = ContextVar("preparation_read_only", default=False)


def assert_write_allowed(method: str) -> None:
    if preparation_read_only.get():
        raise VKWriteDisabledError(method, "Preparation is strictly read-only")
