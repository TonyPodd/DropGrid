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
    for row in unique:
        similarities = sorted(
            (
                cosine_similarity(vectors[row.id], vectors[other.id])
                for other in unique
                if other.id != row.id
            ),
            reverse=True,
        )
        scores[row.id] = (
            (sum(similarities[:DENSITY_K]) / len(similarities[:DENSITY_K]), similarities[0])
            if similarities
            else (None, None)
        )
    ordered = sorted(unique, key=lambda r: (-(scores[r.id][0] or 0), str(r.id)))
    keep = (
        len(ordered)
        if len(ordered) < MIN_UNIQUE
        else max(MIN_CORE, math.ceil(len(ordered) * CORE_FRACTION))
    )
    core = {row.id for row in ordered[:keep]}
    assessments = [
        DensityAssessment(
            row.id,
            "core" if row.id in core else "auxiliary",
            scores.get(row.id, (None, None))[0],
            scores.get(row.id, (None, None))[1],
            row.id in duplicates,
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
