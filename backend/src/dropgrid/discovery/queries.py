"""Bounded, deterministic cluster queries, never one request per grid row."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from dropgrid.photos.concepts import car_model
from dropgrid.photos.domain import normalize_category
from dropgrid.photos.regions import RegionContext, grid_region


@dataclass(frozen=True)
class Seed:
    id: UUID
    name: str
    category: str | None
    content_hint: str | None = None
    region_key: str | None = None

    @property
    def region(self) -> RegionContext | None:
        return grid_region(self.category, self.region_key)


def topic_for(seed: Seed) -> str:
    text = " ".join(filter(None, (seed.content_hint, seed.name, seed.category)))
    model = car_model(text)
    if model:
        return model
    category = normalize_category(seed.category)
    if seed.region:
        return "авто"
    aliases = {
        "трактор": "тракторы",
        "котики": "кошки",
        "уличные коты": "кошки",
        "бмв": "BMW",
        "музыка": "музыка",
    }
    return aliases.get(category, category) or "сообщество"


def queries_for(topic: str, region: RegionContext | None) -> tuple[str, ...]:
    if region and region.region_known:
        city = region.search_places[0]
        return (
            f"{topic} {city}",
            f"автоклуб {city}" if topic == "авто" else f"{topic} клуб {city}",
            f"{region.region_codes[0]} регион {topic}",
        )
    aliases = {
        "тракторы": ("тракторы", "сельхоз техника", "Кировец"),
        "кошки": ("кошки", "котики", "кошки клуб"),
    }
    return aliases.get(topic, (topic, f"{topic} club", f"{topic} Россия"))


def build_queries(seeds: list[Seed], limit: int = 12) -> list[dict[str, Any]]:
    clusters: dict[tuple[str, str | None], list[Seed]] = {}
    for seed in sorted(seeds, key=lambda s: str(s.id)):
        region = seed.region
        key = (topic_for(seed), region.region_key if region else None)
        clusters.setdefault(key, []).append(seed)
    result: dict[str, dict[str, Any]] = {}
    # Round-robin across clusters rather than spending every slot on the first category.
    for level in range(3):
        for (topic, _), group in sorted(clusters.items(), key=lambda item: str(item[0])):
            seed = group[0]
            region = seed.region
            query = normalize_category(queries_for(topic, region)[level])[:100]
            if query in result:
                old = result[query]
                old["seed_ids"] = sorted(set(old["seed_ids"]) | {str(s.id) for s in group})
                continue
            if len(result) >= min(max(limit, 1), 24):
                continue
            result[query] = {
                "query": query,
                "topic": topic,
                "category": seed.category,
                "region_key": region.region_key if region else None,
                "seed_ids": [str(s.id) for s in group],
            }
    return list(result.values())
