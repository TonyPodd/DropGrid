from dropgrid.services.catalog import ConflictError

CODES = frozenset(
    {
        "reference_sync_in_progress",
        "archive_sync_in_progress",
        "profile_locked",
        "preview_in_progress",
        # A photo marked «Не нравится» is approved only after explicitly choosing it again.
        "photo_disliked",
        "photo_reuse_limit",
        "photo_recently_used",
        "photo_unavailable",
        # A girl's photo belongs to one account (VK token) only.
        "persona_photo_taken",
        "persona_photo_duplicate",
        "persona_photo_in_library",
        "persona_photo_limit",
        # Suggesting posts needs one account per quota (100) communities of the grid.
        "accounts_needed",
        # A campaign that is preparing, sending or monitoring is stopped before deletion.
        "campaign_in_progress",
    }
)


class PhotoConflict(ConflictError):
    def __init__(self, code: str, message: str = "Photo operation is temporarily locked") -> None:
        assert code in CODES
        self.code = code
        super().__init__(message)
