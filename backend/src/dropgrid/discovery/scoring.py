"""Deterministic V1 evidence, not a learned prediction of publication acceptance."""

import math
import re
from datetime import datetime
from typing import Any

from dropgrid.photos.concepts import car_model
from dropgrid.photos.domain import normalize_category
from dropgrid.photos.visual import VisualEmbedding, cosine_similarity


def topic_match(topic: str, text: str) -> float:
    topic, text = normalize_category(topic), normalize_category(text)
    model = car_model(topic)
    if model:
        return 1.0 if car_model(text) == model else 0.0
    aliases = {
        "авто": {
            "авто",
            "машины",
            "автомобили",
            "автоклуб",
            "car",
            "cars",
            "бпан",
            "дрифт",
            "auto",
        },
        "тракторы": {"трактор", "тракторы", "кировец", "сельхоз", "tractor"},
        "кошки": {"кошки", "котики", "кот", "коты", "cat", "cats"},
        "bmw": {"bmw", "бмв"},
    }
    words = set(re.findall(r"[\w]+", text))
    if topic == "тракторы":
        agriculture = any(
            w.startswith(("сельхоз", "сельскохозяй", "мтз", "юмз", "комбайн", "кировец"))
            for w in words
        )
        entertainment = (
            "хк" in words
            or any(w.startswith(("хокке", "мультф", "мультик")) for w in words)
            or "синий трактор" in text
            or "бар" in words
            or " bar" in text
        )
        if entertainment and not agriculture:
            return 0.0
    if topic in aliases:
        return 1.0 if words & aliases[topic] else 0.0
    tokens = set(re.findall(r"[\w]+", topic)) - {"клуб", "club", "россия"}
    # Multi-token car-model queries require the model identity, not just "BMW".
    return len(tokens & words) / max(1, len(tokens))


def activity(posts: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    timestamps = [
        p["date"]
        for p in posts
        if type(p.get("date")) is int
        and 0 < p["date"] <= now.timestamp()
        and p.get("post_type") == "post"
    ]
    last = max(timestamps) if timestamps else None
    age = (now.timestamp() - last) / 86400 if last else math.inf
    state, score = ("active", 1.0) if age <= 30 else ("weak", 0.5) if age <= 90 else ("dead", 0.0)
    return {
        "state": state,
        "score": score,
        "last_post_at": int(last) if last else None,
        "posts_last_30d": sum(t >= now.timestamp() - 30 * 86400 for t in timestamps),
        "posts_last_90d": sum(t >= now.timestamp() - 90 * 86400 for t in timestamps),
        "sampled_posts": len(posts),
        "counts_are_sample_lower_bounds": True,
        "photo_posts_sampled": sum(
            any(a.get("type") == "photo" for a in p.get("attachments", []) if isinstance(a, dict))
            for p in posts
        ),
    }


def centroid(vectors: list[VisualEmbedding]) -> VisualEmbedding | None:
    if not vectors:
        return None
    first = vectors[0]
    compatible = [v for v in vectors if v.model == first.model and v.dimensions == first.dimensions]
    return VisualEmbedding(
        first.model,
        first.dimensions,
        tuple(
            sum(v.vector[i] for v in compatible) / len(compatible) for i in range(first.dimensions)
        ),
    )


def visual_similarity(
    candidate: VisualEmbedding, references: dict[str, list[VisualEmbedding]]
) -> list[tuple[str, float]]:
    values = []
    for id, vectors in references.items():
        core = centroid(vectors)
        if core and core.model == candidate.model and core.dimensions == candidate.dimensions:
            values.append((id, cosine_similarity(candidate, core)))
    return sorted(values, key=lambda pair: (-pair[1], pair[0]))[:5]


def final_score(
    topic: float, visual: float | None, region: float | None, activity: float, queries: int
) -> tuple[float, float]:
    graph = min(1.0, max(0, queries - 1) / 3)
    weights = [(topic, 0.40), (activity, 0.20), (graph, 0.10)]
    if visual is not None:
        weights.append((max(0.0, visual), 0.30))
    if region is not None:
        weights.append((region, 0.30))
    value = sum(v * w for v, w in weights) / sum(w for _, w in weights)
    return round(value, 4), graph
