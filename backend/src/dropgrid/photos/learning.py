"""Local interpretable pairwise ranker trained on confirmed, displayed choices.

One model ranks photos: the champion. Every training produces a challenger that is
compared with the champion and with the deterministic baseline on the same, newest
decisions neither was trained on. The challenger replaces the champion only if it
is at least as accurate as the champion and more accurate than the baseline. A
champion that falls behind the baseline is retired and the baseline ranks again.
"""

import math
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.config import Settings
from dropgrid.db.models import (
    PhotoRankingModel,
    PhotoReviewer,
    PhotoSelectionCandidate,
    PhotoSelectionSession,
    utcnow,
)
from dropgrid.photos.concepts import concept_queries

SCHEMA_VERSION = 1
PROVIDERS = ("pinterest", "pixabay", "library", "vk_archive", "vk_category_archive")
CONTEXTS = ("woman", "car", "cat", "dog", "tractor", "truck", "nature", "military")
DIMENSIONS = 6 + len(PROVIDERS) + 2 * len(CONTEXTS)


def number(value: object, default: float = 0.0) -> float:
    if type(value) not in (int, float):
        return default
    result = float(cast(int | float, value))
    return result if math.isfinite(result) else default


def features(row: PhotoSelectionCandidate, context: PhotoSelectionSession) -> list[float]:
    data = row.features
    visual = number(data.get("normalized_visual_score"))
    metadata = number(data.get("metadata_score"))
    words = set(
        " ".join(
            concept_queries(
                " ".join(
                    filter(
                        None,
                        (
                            context.content_hint,
                            str(data.get("context_desired_content") or ""),
                            context.category,
                        ),
                    )
                )
            )
        ).split()
    )
    return (
        [
            visual,
            metadata,
            number(data.get("quality_score")),
            number(data.get("best_similarity")),
            min(number(data.get("reference_count")), 50) / 50,
            number(data.get("age_reuse_score")),
        ]
        + [float(row.provider == p) for p in PROVIDERS]
        + [value if topic in words else 0 for topic in CONTEXTS for value in (visual, metadata)]
    )


# "ready" marks models that passed the former manual-enable check.
TRAINED_STATES = ("ready", "evaluated", "champion", "retired")
MIN_VALIDATION = 20


def valid_model(model: PhotoRankingModel | None) -> bool:
    return bool(
        model
        and model.state in TRAINED_STATES
        and model.feature_schema_version == SCHEMA_VERSION
        and model.coefficients
        and len(model.coefficients) == DIMENSIONS
        and all(type(v) in (int, float) and math.isfinite(v) for v in model.coefficients)
    )


def predict(
    rows: list[PhotoSelectionCandidate],
    context: PhotoSelectionSession,
    model: PhotoRankingModel | None,
) -> int | None:
    if not rows or not valid_model(model):
        return None
    assert model and model.coefficients
    weights = model.coefficients
    return min(
        rows,
        key=lambda row: (
            -sum(w * x for w, x in zip(weights, features(row, context), strict=True)),
            row.rank,
        ),
    ).rank


def temporal_split(
    decisions: list[PhotoSelectionSession],
) -> tuple[list[PhotoSelectionSession], list[PhotoSelectionSession]]:
    ordered = sorted(
        decisions, key=lambda d: (d.confirmed_at or datetime.min.replace(tzinfo=UTC), str(d.id))
    )
    cut = max(1, int(len(ordered) * 0.8))
    return ordered[:cut], ordered[cut:]


async def active_model(session: AsyncSession) -> PhotoRankingModel | None:
    """The champion that ranks photos; None means the deterministic baseline ranks."""
    model = await session.scalar(
        select(PhotoRankingModel)
        .where(PhotoRankingModel.state == "champion")
        .order_by(PhotoRankingModel.trained_at.desc(), PhotoRankingModel.id.desc())
        .limit(1)
    )
    return model if valid_model(model) else None


async def latest_trained(session: AsyncSession) -> PhotoRankingModel | None:
    model: PhotoRankingModel | None = await session.scalar(
        select(PhotoRankingModel)
        .where(PhotoRankingModel.state.in_(TRAINED_STATES))
        .order_by(PhotoRankingModel.trained_at.desc(), PhotoRankingModel.id.desc())
        .limit(1)
    )
    return model


async def _incumbent(session: AsyncSession) -> PhotoRankingModel | None:
    champion = await active_model(session)
    if champion:
        return champion
    # Before the first champion, a model that passed the former check may compete.
    legacy = await session.scalar(
        select(PhotoRankingModel)
        .where(PhotoRankingModel.state == "ready")
        .order_by(PhotoRankingModel.trained_at.desc(), PhotoRankingModel.id.desc())
        .limit(1)
    )
    return legacy if valid_model(legacy) else None


def decide(enough: bool, baseline: int, challenger: int, champion: int | None) -> str:
    """Outcome of one training, from top-1 hits on the same validation decisions."""
    if not enough:
        return "insufficient_validation"
    if challenger > baseline and (champion is None or challenger >= champion):
        return "promoted"
    if champion is None:
        return "not_better"
    return "kept" if champion >= baseline else "fallback"


async def queue_training(
    session: AsyncSession, settings: Settings, explicit: bool = False
) -> PhotoRankingModel | None:
    await session.execute(text("SELECT pg_advisory_xact_lock(4452471001)"))
    count = (
        await session.scalar(
            select(func.count())
            .select_from(PhotoSelectionSession)
            .where(PhotoSelectionSession.confirmed_at.is_not(None))
        )
        or 0
    )
    if count < settings.photo_learned_ranker_min_choices:
        return None
    active = await session.scalar(
        select(PhotoRankingModel).where(PhotoRankingModel.state.in_(("queued", "training")))
    )
    if active:
        return active
    previous = await session.scalar(
        select(PhotoRankingModel)
        .where(PhotoRankingModel.state.in_(TRAINED_STATES))
        .order_by(PhotoRankingModel.created_at.desc())
        .limit(1)
    )
    if (
        not explicit
        and previous
        and count - previous.training_choices < settings.photo_learned_ranker_retrain_choices
    ):
        return None
    row = PhotoRankingModel(training_choices=count)
    session.add(row)
    await session.flush()
    return row


async def train_one(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> bool:
    # Transaction lock bounds CPU training; this never makes external requests.
    async with sessions() as session, session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(4452471002)"))
        model = await session.scalar(
            select(PhotoRankingModel)
            .where(PhotoRankingModel.state == "queued")
            .order_by(PhotoRankingModel.created_at)
            .with_for_update()
            .limit(1)
        )
        if not model:
            return False
        decisions = list(
            (
                await session.scalars(
                    select(PhotoSelectionSession)
                    .where(PhotoSelectionSession.confirmed_at.is_not(None))
                    .order_by(
                        PhotoSelectionSession.confirmed_at.desc(), PhotoSelectionSession.id.desc()
                    )
                    .limit(5000)
                )
            ).all()
        )
        if len(decisions) < settings.photo_learned_ranker_min_choices:
            model.state = "insufficient_data"
            return True
        train, validation = temporal_split(decisions)
        candidates = (
            await session.scalars(
                select(PhotoSelectionCandidate).where(
                    PhotoSelectionCandidate.selection_session_id.in_([d.id for d in decisions])
                )
            )
        ).all()
        groups: dict[UUID, list[PhotoSelectionCandidate]] = {}
        for row in candidates:
            if row.displayed:
                groups.setdefault(row.selection_session_id, []).append(row)
        weights = [0.0] * DIMENSIONS
        comparisons = []
        weak_comparisons = []
        for decision in train:
            shown = groups.get(decision.id, [])
            chosen = next((c for c in shown if c.rank == decision.chosen_rank), None)
            if chosen is None:
                continue
            positive = features(chosen, decision)
            for other in shown:
                if other.rank != chosen.rank:
                    comparisons.append(
                        [a - b for a, b in zip(positive, features(other, decision), strict=True)]
                    )
            # Ratings supplement confirmed review evidence; they cannot contradict its winner.
            for liked in shown:
                if liked.rank == chosen.rank or liked.operator_rating != "like":
                    continue
                for disliked in shown:
                    if disliked.rank != chosen.rank and disliked.operator_rating == "dislike":
                        weak_comparisons.append(
                            [
                                a - b
                                for a, b in zip(
                                    features(liked, decision),
                                    features(disliked, decision),
                                    strict=True,
                                )
                            ]
                        )
        for _ in range(60):
            for delta, importance in [(d, 1.0) for d in comparisons] + [
                (d, 0.15) for d in weak_comparisons
            ]:
                dot = max(-30, min(30, sum(w * x for w, x in zip(weights, delta, strict=True))))
                error = 1 / (1 + math.exp(dot))
                weights = [
                    w + 0.03 * (importance * error * x - 0.001 * w)
                    for w, x in zip(weights, delta, strict=True)
                ]
        model.coefficients, model.feature_schema_version = weights, SCHEMA_VERSION
        model.state = "evaluated"  # a challenger until the comparison below promotes it
        incumbent = await _incumbent(session)

        def hits(ranker: PhotoRankingModel) -> int:
            return sum(
                predict(groups.get(d.id, []), d, ranker) == d.chosen_rank for d in validation
            )

        baseline = sum(d.chosen_rank == d.baseline_rank for d in validation)
        learned = hits(model)
        held = hits(incumbent) if incumbent else None
        outcome = decide(
            len(validation) >= MIN_VALIDATION and bool(comparisons), baseline, learned, held
        )
        denominator = max(1, len(validation))
        evaluation = {
            "validation": len(validation),
            "baseline_top1": baseline / denominator,
            "evaluated_at": utcnow().isoformat(),
        }
        model.metrics = {
            "decisions": len(decisions),
            "train": len(train),
            "validation": len(validation),
            "pairwise_examples": len(comparisons),
            "rating_examples": len(weak_comparisons),
            "baseline_top1": baseline / denominator,
            "learned_top1": learned / denominator,
            "champion_version": str(incumbent.id) if incumbent else None,
            "champion_top1": held / denominator if held is not None else None,
            "outcome": outcome,
            "promotion_ready": outcome == "promoted",
        }
        if outcome in {"promoted", "fallback"}:
            await session.execute(
                update(PhotoRankingModel)
                .where(PhotoRankingModel.state.in_(("champion", "ready")))
                .values(state="retired")
            )
        if outcome == "promoted":
            model.state = "champion"
            model.metrics["current"] = {**evaluation, "top1": learned / denominator}
        elif outcome == "kept" and incumbent and held is not None:
            incumbent.state = "champion"
            incumbent.metrics = {
                **(incumbent.metrics or {}),
                "current": {**evaluation, "top1": held / denominator},
            }
        reviewers = {
            r.id: r.display_name for r in (await session.scalars(select(PhotoReviewer))).all()
        }
        by_reviewer: dict[str, dict[str, int | float]] = {}
        for decision in decisions:
            name = (
                reviewers.get(decision.reviewer_id, "Unattributed")
                if decision.reviewer_id
                else "Unattributed"
            )
            bucket = by_reviewer.setdefault(
                name, {"decisions": 0, "validation": 0, "baseline_matches": 0, "learned_matches": 0}
            )
            bucket["decisions"] += 1
            if decision in validation:
                bucket["validation"] += 1
                bucket["baseline_matches"] += int(decision.chosen_rank == decision.baseline_rank)
                bucket["learned_matches"] += int(
                    predict(groups.get(decision.id, []), decision, model) == decision.chosen_rank
                )
        model.metrics["reviewers"] = by_reviewer
        model.training_choices = (
            await session.scalar(
                select(func.count())
                .select_from(PhotoSelectionSession)
                .where(PhotoSelectionSession.confirmed_at.is_not(None))
            )
            or 0
        )
        model.trained_at = utcnow()
        return True


async def bootstrap(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """Without a champion, evaluate once at worker start instead of waiting for new choices."""
    async with sessions() as session, session.begin():
        if not await active_model(session):
            await queue_training(session, settings, explicit=True)
