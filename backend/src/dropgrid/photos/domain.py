"""Provider-neutral photo metadata, policy and deterministic selection."""

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dropgrid.photos.concepts import car_model, concept_queries

MAX_RETRIEVAL_QUERIES = 4
MAX_CANDIDATES_PER_QUERY = 24


def normalize_category(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е").split())


class PhotoError(Exception):
    """Only fixed categories leave an adapter; no external exception text."""

    def __init__(self, code: str, retry_after: float = 0, request_made: bool = False) -> None:
        self.code = code
        self.request_made = request_made
        self.retry_after = min(max(retry_after, 0), 3600)
        super().__init__(code)


class PhotoSearch(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    query: str = Field(min_length=1, max_length=100)
    lang: Literal["ru", "en"] = "ru"
    image_type: Literal["photo"] = "photo"
    orientation: Literal["all", "horizontal", "vertical"] = "all"
    category: str | None = None
    min_width: int = Field(default=850, ge=1)
    min_height: int = Field(default=850, ge=1)
    safesearch: Literal[True] = True
    order: Literal["popular", "latest"] = "popular"
    page: int = Field(default=1, ge=1, le=2)
    per_page: int = Field(default=75, ge=3, le=100)

    @field_validator("query")
    @classmethod
    def normalized_query(cls, value: str) -> str:
        return normalize_category(value)

    def cache_key(self, provider: str) -> str:
        data = {"provider": provider, **self.model_dump()}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class PhotoCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider: str
    provider_asset_id: str
    source_page_url: str
    candidate_download_url: str = Field(repr=False)
    publication_eligible: bool = True
    title: str = ""
    description: str = ""
    alt_text: str = ""
    creator_name: str = ""
    creator_url: str | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    media_type: str = "photo"
    tags: tuple[str, ...] = ()
    provider_position: int = Field(default=0, ge=0)
    image_size: int | None = Field(default=None, ge=0)
    views: int = Field(default=0, ge=0)
    downloads: int = Field(default=0, ge=0)
    likes: int = Field(default=0, ge=0)
    license_code: str
    license_name: str
    license_url: str
    requires_publication_attribution: bool = False
    attribution_text: str | None = None


@dataclass(frozen=True)
class PhotoSearchResult:
    candidates: tuple[PhotoCandidate, ...]
    rate_limit: int | None = None
    remaining: int | None = None
    reset: float = 60


class PhotoProvider(Protocol):
    name: str
    supports_sensitive_context: bool

    async def search(self, search: PhotoSearch) -> PhotoSearchResult: ...


class FakePhotoProvider:
    name = "fake"
    supports_sensitive_context = True

    def __init__(self, candidates: tuple[PhotoCandidate, ...] = ()) -> None:
        self.candidates = candidates
        self.calls: list[PhotoSearch] = []

    async def search(self, search: PhotoSearch) -> PhotoSearchResult:
        self.calls.append(search)
        return PhotoSearchResult(self.candidates)


@dataclass(frozen=True)
class PhotoQueryPlan:
    category: str
    variants: tuple[PhotoSearch, ...]
    sensitive: bool = False
    content_hint: str | None = None
    desired_content: str | None = None


class PhotoQueryBuilder:
    mappings = {
        "грузовики": ("грузовик дорога", "truck highway", "semi truck road"),
        "тракторы": ("трактор поле", "tractor field", "agricultural tractor"),
        "авто": ("автомобиль дорога", "car road", "automobile landscape"),
        "мото": ("мотоцикл дорога", "motorcycle road", "motorbike"),
        "природа": ("природа пейзаж", "nature landscape", "forest lake"),
        "животные": ("животные природа", "wildlife nature", "animals"),
        "собаки": ("собака природа", "dog outdoors", "dogs"),
        "кошки": ("кошка", "cat garden", "cats"),
        "любовь": ("сердце цветы", "heart flowers", "romantic sunset"),
        "знакомства": ("цветы закат", "flowers sunset", "romantic landscape"),
        "цитаты": ("природа небо", "nature sky", "calm landscape"),
        "музыка": ("музыка", "music concert", "musical instruments"),
        "военные": ("военные", "soldier military", "army"),
        "бмв": ("бмв автомобиль", "bmw car", "bmw sedan"),
        "мерседес": ("мерседес автомобиль", "mercedes car", "mercedes sedan"),
        "еда": ("еда", "food cooking", "meal"),
    }
    aliases = {
        "автомобили": "авто",
        "мотоциклы": "мото",
        "dating": "знакомства",
        "цитата": "цитаты",
        "цитаты про любовь": "цитаты",
        "котики": "кошки",
        "уличные коты": "кошки",
        "фура": "грузовики",
        "трактор": "тракторы",
    }

    def build(
        self,
        category: str | None,
        content_hint: str | None = None,
        desired_content: str | None = None,
    ) -> PhotoQueryPlan:
        model = car_model(" ".join(filter(None, (category, content_hint, desired_content))))
        if model:
            return PhotoQueryPlan(
                normalize_category(category),
                tuple(
                    PhotoSearch(query=model + suffix, lang="en", per_page=MAX_CANDIDATES_PER_QUERY)
                    for suffix in ("", " car", " aesthetic", " street")
                ),
                False,
                content_hint,
                desired_content,
            )
        key = normalize_category(category)
        canonical = self.aliases.get(key, key)
        if {"dating", "знакомства", "знакомство"} & set(key.split()):
            canonical = "знакомства"
        variants = self.mappings.get(canonical)
        if variants:
            searches = tuple(
                PhotoSearch(query=q, lang="ru" if i == 0 else "en") for i, q in enumerate(variants)
            )
        else:
            searches = (
                PhotoSearch(
                    query=key[:100] or "природа",
                    lang="ru" if re.search("[а-я]", key) or not key else "en",
                ),
            )
        hints, desired = concept_queries(content_hint), concept_queries(desired_content)
        if hints or desired:
            category_en = next((v.query for v in searches if v.lang == "en"), searches[0].query)
            queries = list(hints or desired)
            if hints and desired:
                queries.append(desired[0])
            queries.append(" ".join(dict.fromkeys((category_en + " " + queries[0]).split())))
            searches = (
                searches[0].model_copy(update={"per_page": MAX_CANDIDATES_PER_QUERY}),
            ) + tuple(
                PhotoSearch(query=q[:100], lang="en", per_page=MAX_CANDIDATES_PER_QUERY)
                for q in dict.fromkeys(queries)
                if q != searches[0].query
            )
            searches = searches[:MAX_RETRIEVAL_QUERIES]
        return PhotoQueryPlan(
            key, searches, canonical == "знакомства", content_hint, desired_content
        )


@dataclass(frozen=True)
class PhotoPolicy:
    min_short_side: int = 850
    max_aspect_ratio: float = 2.5
    max_input_bytes: int = 20 * 1024 * 1024
    max_output_bytes: int = 8 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_dimension: int = 2560
    jpeg_quality: int = 92
    hamming_threshold: int = 4
    max_redirects: int = 3
    timeout_seconds: float = 25
    plan_timeout_seconds: float = 300
    trusted_hosts: frozenset[str] = frozenset({"pixabay.com", "cdn.pixabay.com"})

    def trusted_host(self, host: str) -> bool:
        return host in self.trusted_hosts

    def dimensions_allowed(self, width: int, height: int) -> bool:
        return (
            min(width, height) >= self.min_short_side
            and max(width, height) / min(width, height) <= self.max_aspect_ratio
        )

    def candidate_allowed(self, candidate: PhotoCandidate, sensitive: bool = False) -> bool:
        # Conservative licensing policy: Pixabay's Terms explicitly mention dating
        # services among prohibited contexts. Metadata filters cannot certify an exception.
        if sensitive and candidate.license_code == "pixabay-content-license":
            return False
        people = {
            "portrait",
            "woman",
            "women",
            "man",
            "men",
            "girl",
            "boy",
            "model",
            "face",
            "selfie",
            "person",
            "people",
            "портрет",
            "женщина",
            "мужчина",
            "девушка",
            "девочка",
            "мальчик",
            "модель",
            "лицо",
            "селфи",
            "человек",
            "люди",
            "женщины",
            "мужчины",
        }
        words = set(re.findall(r"\w+", " ".join(candidate.tags).casefold()))
        return (
            candidate.media_type == "photo"
            and self.dimensions_allowed(candidate.width, candidate.height)
            and not (sensitive and words & people)
        )


class PhotoRanker:
    def score(
        self,
        candidate: PhotoCandidate,
        search: PhotoSearch,
        priority: int = 0,
        usage: int = 0,
        last_used_at: datetime | None = None,
        pending: int = 0,
        creator_selected: int = 0,
    ) -> float:
        query = set(re.findall(r"\w+", search.query))
        tags = set(re.findall(r"\w+", " ".join(candidate.tags).casefold()))
        relevance = 10 * len(query & tags) / max(len(query), 1)
        quality = min(min(candidate.width, candidate.height) / 1000, 4)
        ratio = candidate.width / candidate.height
        aspect = min(abs(ratio - preferred) for preferred in (1, 0.8, 1.5, 4 / 3, 16 / 9))
        popularity = (
            min(math.log1p(candidate.likes + candidate.downloads / 100 + candidate.views / 1000), 5)
            * 0.2
        )
        size_penalty = 1 if candidate.image_size and candidate.image_size > 20 * 1024 * 1024 else 0
        # Fixed recency penalty keeps equal inputs stable (no implicit wall-clock dependency).
        return (
            relevance
            + quality
            + popularity
            - aspect
            - priority * 2
            - candidate.provider_position * 0.03
            - usage * 0.5
            - (last_used_at.timestamp() / 1_000_000_000 if last_used_at else 0)
            - pending * 0.25
            - creator_selected * 2
            - size_penalty
        )


class Deduplicator:
    def __init__(self, threshold: int = 4) -> None:
        self.threshold = threshold

    def near(self, left: str | None, right: str | None) -> bool:
        if not left or not right:
            return False
        try:
            return (int(left, 16) ^ int(right, 16)).bit_count() <= self.threshold
        except ValueError:
            return False
