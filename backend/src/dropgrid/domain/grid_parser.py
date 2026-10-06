import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

_REFERENCE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,63}")
_INDEX = re.compile(r"^\d+[.)]?\s+")


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
        is_heading = (
            not indexed
            and not any(c in line for c in "/.@:")
            and not any(c.isdigit() for c in line)
            and (line.isupper() or bool(re.search(r"[А-Яа-яЁё]", line)))
        )
        if line.startswith("#") or is_heading:
            category = line.removeprefix("#").strip() or None
            continue
        try:
            community = normalize_vk_community_reference(candidate)
        except ValueError as exc:
            result.errors.append(GridParseError(line_number, raw, str(exc)))
            continue
        if community in seen:
            result.errors.append(GridParseError(line_number, raw, "Duplicate community"))
            continue
        seen.add(community)
        result.items.append(GridItem(category, community))
    return result
