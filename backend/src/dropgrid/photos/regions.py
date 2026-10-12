"""Explicit grid-scoped vehicle-region taxonomy; image geography is never guessed.

Definitions: МВД приказ 766, приложение 1; three-digit aliases per приказ 130.
Only audited base codes and explicitly supported aliases are recognized.
"""

import re
import unicodedata
from dataclasses import asdict, dataclass, replace


@dataclass(frozen=True)
class RegionContext:
    recognized_region: bool
    region_known: bool
    region_codes: tuple[str, ...]
    region_key: str | None = None
    display_name: str | None = None
    vehicle_codes: tuple[str, ...] = ()
    search_places: tuple[str, ...] = ()
    confidence: str = "unknown"
    source: str = "grid_category"

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


# Code(s), canonical key, display name, Russian/English useful search places.
REGION_ROWS = (
    (("03", "103"), "buryatia", "Республика Бурятия", ("Улан-Удэ", "Бурятия", "Ulan Ude")),
    (("11", "111"), "komi", "Республика Коми", ("Сыктывкар", "Коми", "Syktyvkar")),
    (("12", "112"), "mari_el", "Республика Марий Эл", ("Йошкар-Ола", "Марий Эл", "Yoshkar Ola")),
    (("17", "117"), "tuva", "Республика Тыва", ("Кызыл", "Тыва", "Kyzyl")),
    (
        ("23", "93", "123", "193", "323"),
        "krasnodar_krai",
        "Краснодарский край",
        ("Краснодар", "Краснодарский край", "Krasnodar"),
    ),
    (
        ("24", "124"),
        "krasnoyarsk_krai",
        "Красноярский край",
        ("Красноярск", "Красноярский край", "Krasnoyarsk"),
    ),
    (
        ("28", "128"),
        "amur_oblast",
        "Амурская область",
        ("Благовещенск", "Амурская область", "Blagoveshchensk"),
    ),
    (
        ("30", "130"),
        "astrakhan_oblast",
        "Астраханская область",
        ("Астрахань", "Астраханская область", "Astrakhan"),
    ),
    (
        ("31", "131"),
        "belgorod_oblast",
        "Белгородская область",
        ("Белгород", "Белгородская область", "Belgorod"),
    ),
    (
        ("34", "134"),
        "volgograd_oblast",
        "Волгоградская область",
        ("Волгоград", "Волгоградская область", "Volgograd"),
    ),
    (
        ("35", "135"),
        "vologda_oblast",
        "Вологодская область",
        ("Вологда", "Вологодская область", "Vologda"),
    ),
    (
        ("37", "137"),
        "ivanovo_oblast",
        "Ивановская область",
        ("Иваново", "Ивановская область", "Ivanovo"),
    ),
    (
        ("42", "142"),
        "kemerovo_oblast",
        "Кемеровская область — Кузбасс",
        ("Кемерово", "Кузбасс", "Kemerovo"),
    ),
    (
        ("44", "144"),
        "kostroma_oblast",
        "Костромская область",
        ("Кострома", "Костромская область", "Kostroma"),
    ),
    (("46", "146"), "kursk_oblast", "Курская область", ("Курск", "Курская область", "Kursk")),
    (
        ("48", "148"),
        "lipetsk_oblast",
        "Липецкая область",
        ("Липецк", "Липецкая область", "Lipetsk"),
    ),
    (
        ("54", "154"),
        "novosibirsk_oblast",
        "Новосибирская область",
        ("Новосибирск", "Новосибирская область", "Novosibirsk"),
    ),
    (
        ("56", "156"),
        "orenburg_oblast",
        "Оренбургская область",
        ("Оренбург", "Оренбургская область", "Orenburg"),
    ),
    (("58", "158"), "penza_oblast", "Пензенская область", ("Пенза", "Пензенская область", "Penza")),
    (
        ("61", "161"),
        "rostov_oblast",
        "Ростовская область",
        ("Ростов-на-Дону", "Ростовская область", "Rostov"),
    ),
    (
        ("62", "162"),
        "ryazan_oblast",
        "Рязанская область",
        ("Рязань", "Рязанская область", "Ryazan"),
    ),
    (
        ("64", "164"),
        "saratov_oblast",
        "Саратовская область",
        ("Саратов", "Саратовская область", "Saratov"),
    ),
    (
        ("68", "168"),
        "tambov_oblast",
        "Тамбовская область",
        ("Тамбов", "Тамбовская область", "Tambov"),
    ),
    (
        ("73", "173"),
        "ulyanovsk_oblast",
        "Ульяновская область",
        ("Ульяновск", "Ульяновская область", "Ulyanovsk"),
    ),
)
BY_CODE = {code: row for row in REGION_ROWS for code in row[0]}
BY_KEY = {row[1]: row for row in REGION_ROWS}
_CODES = r"\d{1,3}(?:\s*(?:/|,|\s)\s*\d{1,3})*"
_PATTERN = re.compile(rf"^(?:({_CODES})\s*(?:регион|region)|(?:регион|region)\s*({_CODES}))$", re.I)


def region_for_key(key: str | None, *, source: str = "grid_region") -> RegionContext | None:
    row = BY_KEY.get(key or "")
    if not row:
        return None
    codes, canonical, name, places = row
    return RegionContext(True, True, (codes[0],), canonical, name, codes, places, "high", source)


def parse_region(category: str | None) -> RegionContext | None:
    value = " ".join(unicodedata.normalize("NFKC", category or "").strip().split())
    match = _PATTERN.fullmatch(value)
    if not match:
        return None
    codes = tuple(
        dict.fromkeys(c.zfill(2) for c in re.findall(r"\d+", match.group(1) or match.group(2)))
    )
    rows = [BY_CODE.get(code) for code in codes]
    keys = {row[1] for row in rows if row}
    if any(row is None for row in rows) or len(keys) != 1:
        return RegionContext(True, False, codes)
    row = rows[0]
    assert row
    aliases, key, name, places = row
    return RegionContext(True, True, codes, key, name, aliases, places, "high")


def grid_region(category: str | None, key: str | None = None) -> RegionContext | None:
    parsed, canonical = parse_region(category), region_for_key(key)
    if parsed and canonical and parsed.region_key == canonical.region_key:
        return replace(parsed, source="grid_region")
    return canonical or parsed


def region_evidence(text: str) -> RegionContext | None:
    """Explicit place/region labels; bare numbers and domain suffixes prove nothing."""
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    found = []
    for row in REGION_ROWS:
        if any(
            re.search(r"(?<!\w)" + re.escape(place.casefold().replace("ё", "е")) + r"(?!\w)", value)
            for place in row[3]
        ):
            found.append(row[1])
    for match in re.finditer(
        r"(?<!\w)(?:\d{1,3}\s*(?:регион|region)|(?:регион|region)\s*\d{1,3})(?!\w)", value
    ):
        region = parse_region(match.group())
        if region and region.region_known and region.region_key:
            found.append(region.region_key)
    keys = set(found)
    return (
        region_for_key(next(iter(keys)), source="explicit_public_metadata")
        if len(keys) == 1
        else None
    )
