"""Small explicit retrieval vocabulary; no runtime translation or shorthand inference."""

import re

# Extend aliases here, separately from query assembly. Unknown tokens are ignored.
CONCEPTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("девушка", "девушки", "девушкой", "женщина", "женщины", "woman", "girl"), ("woman", "girl")),
    (("машина", "машины", "машиной", "машину", "автомобиль", "автомобилем", "car"), ("car",)),
    (("трактор", "тракторы", "tractor"), ("tractor",)),
    (("фура", "фуры", "грузовик", "грузовики", "truck"), ("truck",)),
    (("собака", "собаки", "собакой", "dog"), ("dog",)),
    (("кот", "коты", "кошка", "кошки", "cat"), ("cat",)),
    (("закат", "закаты", "sunset"), ("sunset",)),
    (("ночь", "ночной", "ночная", "night"), ("night",)),
    (("город", "городской", "city"), ("city",)),
    (("мото", "мотоцикл", "мотоциклы", "motorcycle"), ("motorcycle",)),
    (("дембель", "военные", "soldier"), ("soldier", "military")),
    (("еда", "food"), ("food",)),
    (("казан", "казане", "cauldron"), ("cauldron",)),
    (("бмв", "bmw"), ("bmw",)),
    (("е60", "e60"), ("e60",)),
    (("е38", "e38"), ("e38",)),
    (("w201",), ("mercedes w201",)),
    (("цитаты", "цитата", "quote"), ("quote",)),
    (("небо", "sky"), ("sky",)),
    (("цветы", "flowers"), ("flowers",)),
    (("природа", "nature"), ("nature",)),
)
MAX_CONCEPTS = 4
MAX_HINT_CHARS = 500


def concept_queries(text: str | None) -> tuple[str, ...]:
    tokens = re.findall(
        r"[a-zа-яё0-9]+", (text or "")[:MAX_HINT_CHARS].casefold().replace("ё", "е")
    )
    words = set(tokens)
    found = [values for aliases, values in CONCEPTS if words.intersection(aliases)][:MAX_CONCEPTS]
    if not found:
        return ()
    queries = [" ".join(values[0] for values in found)]
    alternate = " ".join(values[-1] for values in found)
    if alternate != queries[0]:
        queries.append(alternate)
    return tuple(queries)


def car_model(text: str | None) -> str | None:
    words = set(re.findall(r"[a-zа-яё0-9]+", (text or "").casefold()))
    if words & {"honda", "хонда"} and words & {"accord", "аккорд"}:
        return "Honda Accord"
    if words & {"bmw", "бмв"}:
        for model in ("60", "38"):
            if words & {"e" + model, "е" + model}:
                return "BMW E" + model
    if "w201" in words:
        return "Mercedes W201"
    return None
