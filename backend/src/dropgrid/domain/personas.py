"""Grid categories that need photos of one specific girl instead of library photos."""

PERSONA_PROVIDER = "persona"
PERSONA_KINDS = ("selfie", "other")
# Upper bound per girl account: enough variety without an unbounded upload store.
MAX_PHOTOS_PER_ACCOUNT = 30
_MARKERS = ("знакомств", "селфи", "selfie", "dating")


def is_persona_category(name: str | None) -> bool:
    """Dating and selfie publics are sent from a girl's account with her own photos."""
    folded = (name or "").casefold()
    return any(marker in folded for marker in _MARKERS)
