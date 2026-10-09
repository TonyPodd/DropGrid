from dropgrid.services.catalog import ConflictError

CODES = frozenset(
    {
        "reference_sync_in_progress",
        "archive_sync_in_progress",
        "profile_locked",
        "preview_in_progress",
    }
)


class PhotoConflict(ConflictError):
    def __init__(self, code: str) -> None:
        assert code in CODES
        self.code = code
        super().__init__("Photo operation is temporarily locked")
