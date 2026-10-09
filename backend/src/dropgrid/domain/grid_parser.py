import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_REFERENCE = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.]{0,63}")
_INDEX = re.compile(r"^\d+(?:[.)]?\s+|(?=(?:https?://)?vk\.(?:com|ru)/))", re.I)


def normalize_vk_community_reference(value: str) -> str:
    """Normalize a VK domain, never resolving identities through the network."""
    reference = value.strip()
    if reference.startswith("@"):
        reference = reference[1:]
    if "://" in reference or "/" in reference or reference.lower().startswith(("vk.com", "vk.ru")):
        parsed = urlsplit(reference if "://" in reference else "https://" + reference)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.netloc.lower() not in {"vk.com", "vk.ru", "www.vk.com", "www.vk.ru"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Expected a VK community URL without query or fragment")
        reference = parsed.path.removeprefix("/").removesuffix("/")
    if not _REFERENCE.fullmatch(reference):
        raise ValueError("Invalid VK community reference")
    reference = reference.lower()
    if (
        reference.startswith(("club", "public"))
        and reference.removeprefix("club" if reference.startswith("club") else "public").isdigit()
    ):
        prefix = "club" if reference.startswith("club") else "public"
        group_id = int(reference.removeprefix(prefix))
        if not 0 < group_id <= 2**63 - 1:
            raise ValueError("VK group ID must be a positive 64-bit integer")
        # club/public aliases identify the same numeric group.
        return f"club{group_id}"
    return reference


@dataclass(frozen=True)
class GridItem:
    category: str | None
    community: str
    comment: str | None = None


@dataclass(frozen=True)
class GridParseError:
    line: int
    value: str
    message: str


@dataclass
class ParseGridResult:
    items: list[GridItem] = field(default_factory=list)
    errors: list[GridParseError] = field(default_factory=list)


def parse_grid(text: str) -> ParseGridResult:
    """Unindexed uppercase words/Cyrillic text are category headings.

    An ambiguous lowercase ASCII word is a community. Prefix a category with '#'
    to disambiguate it. Duplicate references retain their first category.
    """
    result = ParseGridResult()
    category: str | None = None
    seen: set[str] = set()
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        indexed = bool(_INDEX.match(line))
        candidate = _INDEX.sub("", line)
        first = candidate.split(maxsplit=1)[0]
        is_vk = bool(re.match(r"(?:https?://)?(?:www\.)?vk\.(?:com|ru)/|@", first, re.I))
        is_heading = (
            not indexed and not is_vk and (line.isupper() or bool(re.search(r"[А-Яа-яЁё]", first)))
        )
        if line.startswith("#") or is_heading:
            category = line.removeprefix("#").strip() or None
            continue
        parts = candidate.split(maxsplit=1)
        reference = parts[0]
        comment = parts[1].strip() if len(parts) > 1 else None
        if comment and not (
            "/" in reference
            or reference.startswith("@")
            or comment.startswith(("—", "–", "-"))
            or bool(re.search(r"\s{2,}", candidate))
        ):
            result.errors.append(GridParseError(line_number, raw, "Expected a separated comment"))
            continue
        if comment and comment.startswith(("—", "–", "-")):
            comment = comment[1:].strip() or None
        try:
            community = normalize_vk_community_reference(reference)
            if comment and len(comment) > 3000:
                raise ValueError("Comment must contain at most 3000 characters")
        except ValueError as exc:
            result.errors.append(GridParseError(line_number, raw, str(exc)))
            continue
        if community in seen:
            result.errors.append(GridParseError(line_number, raw, "Duplicate community"))
            continue
        seen.add(community)
        result.items.append(GridItem(category, community, comment))
    return result
