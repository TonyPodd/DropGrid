"""Deterministic relative density, excluding self and collapsed duplicate series."""

import math
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import CommunityReferencePhoto, utcnow
from dropgrid.photos.domain import Deduplicator, PhotoError
from dropgrid.photos.visual import (
    VisualEmbedder,
    VisualEmbedding,
    cosine_similarity,
    deserialize_embedding,
)

MIN_UNIQUE = 8
MIN_CORE = 6
CORE_FRACTION = 0.75
DENSITY_K = 5


@dataclass(frozen=True)
class DensityAssessment:
    id: UUID
    role: str
    density: float | None
    nearest: float | None
    duplicate: bool = False
    cluster_size: int | None = None
    reason: str = "density"


def assess_references(
    rows: list[CommunityReferencePhoto], embedder: VisualEmbedder | None
) -> tuple[list[DensityAssessment], dict[UUID, VisualEmbedding]]:
    vectors: dict[UUID, VisualEmbedding] = {}
    usable = []
    for row in sorted(rows, key=lambda r: str(r.id)):
        if (
            not embedder
            or not row.enabled
            or not row.is_style_reference
            or not row.embedding
            or row.embedding_model != embedder.model
            or row.embedding_dimensions != embedder.dimensions
        ):
            continue
        try:
            vectors[row.id] = deserialize_embedding(
                row.embedding, embedder.model, embedder.dimensions
            )
            usable.append(row)
        except PhotoError:
            continue
    unique: list[CommunityReferencePhoto] = []
    duplicates: set[UUID] = set()
    for row in usable:
        if any(
            (row.sha256 and row.sha256 == old.sha256)
            or Deduplicator().near(row.perceptual_hash, old.perceptual_hash)
            or (row.vk_photo_owner_id, row.vk_photo_id) == (old.vk_photo_owner_id, old.vk_photo_id)
            for old in unique
        ):
            duplicates.add(row.id)
        else:
            unique.append(row)
    scores = {}
    neighbors: dict[UUID, set[UUID]] = {}
    for row in unique:
        ordered_neighbors = sorted(
            (
                (cosine_similarity(vectors[row.id], vectors[other.id]), other.id)
                for other in unique
                if other.id != row.id
            ),
            key=lambda pair: (-pair[0], str(pair[1])),
        )
        similarities = [pair[0] for pair in ordered_neighbors]
        scores[row.id] = (
            (sum(similarities[:DENSITY_K]) / len(similarities[:DENSITY_K]), similarities[0])
            if similarities
            else (None, None)
        )
        neighbors[row.id] = {pair[1] for pair in ordered_neighbors[:3]}
    # Mutual nearest-neighbor connectivity is relative; no fixed cosine cutoff.
    components: list[set[UUID]] = []
    unseen = set(neighbors)
    while unseen:
        seed = min(unseen, key=str)
        component, pending = set(), [seed]
        while pending:
            identity = pending.pop()
            if identity in component:
                continue
            component.add(identity)
            pending.extend(
                other
                for other in neighbors[identity]
                if identity in neighbors[other] and other not in component
            )
        unseen -= component
        components.append(component)
    sizes = {identity: len(group) for group in components for identity in group}
    minimum_cluster = max(3, math.ceil(len(unique) * 0.05))
    sizeable = [group for group in components if len(group) >= minimum_cluster]
    reasons: dict[UUID, str] = {}
    core: set[UUID] = set()
    if len(unique) < MIN_UNIQUE:
        core = {row.id for row in unique}
        reasons = {row.id: "small_reference_fallback" for row in unique}
    elif sizeable:
        for group in sizeable:
            ordered = sorted(
                group, key=lambda identity: (-(scores[identity][0] or 0), str(identity))
            )
            core.update(ordered[: max(3, math.ceil(len(group) * CORE_FRACTION))])
        # Preserve the safe minimum using only sizeable coherent clusters.
        eligible = sorted(
            set().union(*sizeable),
            key=lambda identity: (-(scores[identity][0] or 0), str(identity)),
        )
        core.update(eligible[:MIN_CORE])
        reasons = {
            row.id: "small_cluster"
            if sizes[row.id] < minimum_cluster
            else "coherent_cluster"
            if row.id in core
            else "low_density"
            for row in unique
        }
    else:
        fallback_rows = sorted(unique, key=lambda row: (-(scores[row.id][0] or 0), str(row.id)))
        core = {
            row.id for row in fallback_rows[: max(MIN_CORE, math.ceil(len(unique) * CORE_FRACTION))]
        }
        reasons = {row.id: "density_fallback" for row in unique}
    assessments = [
        DensityAssessment(
            row.id,
            "core" if row.id in core else "auxiliary",
            scores.get(row.id, (None, None))[0],
            scores.get(row.id, (None, None))[1],
            row.id in duplicates,
            sizes.get(row.id),
            "duplicate" if row.id in duplicates else reasons.get(row.id, "incompatible_embedding"),
        )
        for row in rows
    ]
    return assessments, vectors


async def refresh_density(
    sessions: async_sessionmaker[AsyncSession], community_id: UUID, embedder: VisualEmbedder | None
) -> None:
    async with sessions() as session, session.begin():
        rows = list(
            (
                await session.scalars(
                    select(CommunityReferencePhoto)
                    .where(
                        CommunityReferencePhoto.community_id == community_id,
                        CommunityReferencePhoto.is_style_reference.is_(True),
                        CommunityReferencePhoto.posted_at >= utcnow() - timedelta(days=180),
                    )
                    .order_by(CommunityReferencePhoto.posted_at.desc(), CommunityReferencePhoto.id)
                    .limit(300)
                )
            ).all()
        )
        assessments, _ = assess_references(rows, embedder)
        by_id = {a.id: a for a in assessments}
        for row in rows:
            value = by_id[row.id]
            row.reference_role = value.role
            row.reference_density = value.density
            row.reference_nearest_similarity = value.nearest
            row.reference_duplicate = value.duplicate
            row.reference_cluster_size = value.cluster_size
            row.reference_role_reason = value.reason
