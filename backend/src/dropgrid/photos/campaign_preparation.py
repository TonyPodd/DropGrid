"""Restartable campaign workflow. Read-only VK context warmup, shared photo planner."""

import asyncio
from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.models import (
    Campaign,
    CampaignAccount,
    CampaignPreparationJob,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import CampaignStatus
from dropgrid.integrations.vk.read_only import preparation_read_only
from dropgrid.photos.engine import PhotoEngine
from dropgrid.photos.preparation import MediaPreparation
from dropgrid.photos.progress import observer
from dropgrid.photos.schemas import MediaPlanInput
from dropgrid.services.account_pools import pool, save_pool
from dropgrid.services.campaigns import locked_campaign, prepare_campaign
from dropgrid.services.catalog import ConflictError
from dropgrid.services.sending import preflight, usable_account

PHOTO_BATCH_SIZE = 3


async def enqueue(
    session: AsyncSession,
    campaign_id: UUID,
    engine: PhotoEngine,
    account_ids: list[UUID] | None = None,
) -> CampaignPreparationJob:
    campaign = await locked_campaign(session, campaign_id)
    if campaign.status not in {CampaignStatus.draft, CampaignStatus.ready}:
        raise ConflictError("Preparation is unavailable after sending starts")
    old = await session.scalar(
        select(CampaignPreparationJob)
        .where(CampaignPreparationJob.campaign_id == campaign_id)
        .with_for_update()
    )
    if old and old.state in {"queued", "running"}:
        return old
    accounts = await pool(session, campaign_id, engine.planner.settings, account_ids)
    usable = [a for a, _, _ in accounts if usable_account(a)]
    if account_ids is not None and len(usable) != len(set(account_ids)):
        raise ConflictError("Selected pool contains unusable accounts")
    if not usable:
        raise ConflictError("Connect at least one usable VK account")
    await save_pool(session, campaign_id, [a.id for a in usable], engine.planner.settings)
    report = await prepare_campaign(session, campaign_id)
    campaign.preparation_state = "preparing"
    if old:
        job = old
        job.state, job.stage, job.completed, job.error_code, job.result = (
            "queued",
            "communities",
            0,
            None,
            None,
        )
        job.lease_token = job.lease_until = None
    else:
        job = CampaignPreparationJob(campaign_id=campaign_id)
        session.add(job)
    job.account_id, job.total = usable[0].id, report.total
    await session.flush()
    return job


class CampaignPreparation:
    def __init__(self, engine: PhotoEngine, preparation: MediaPreparation) -> None:
        self.engine, self.preparation, self.sessions = engine, preparation, preparation.sessions

    async def tick(self) -> int:
        token = uuid4()
        async with self.sessions() as session, session.begin():
            job = await session.scalar(
                select(CampaignPreparationJob)
                .where(
                    CampaignPreparationJob.state.in_(("queued", "running")),
                    CampaignPreparationJob.lease_until.is_(None)
                    | (CampaignPreparationJob.lease_until < utcnow()),
                )
                .order_by(CampaignPreparationJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return 0
            campaign = await session.get(Campaign, job.campaign_id)
            assert campaign
            if campaign.status == CampaignStatus.cancelled:
                job.state = "cancelled"
                return 1
            job.state, job.lease_token, job.lease_until = (
                "running",
                token,
                utcnow() + timedelta(minutes=30),
            )
            jid, campaign_id, account_id, completed, stage = (
                job.id,
                job.campaign_id,
                job.account_id,
                job.completed,
                job.stage,
            )
            rows = list(
                (
                    await session.scalars(
                        select(Submission)
                        .where(Submission.campaign_id == campaign_id)
                        .order_by(Submission.community_id, Submission.id)
                    )
                ).all()
            )
            grid_id = campaign.grid_id
            created_at = job.created_at
            progress_result = dict(job.result or {})
        error = None
        result = None

        async def heartbeat(
            _stage: str, _current: int, _total: int | None, _counters: dict[str, int]
        ) -> None:
            async with self.sessions() as db, db.begin():
                current = await db.get(CampaignPreparationJob, jid, with_for_update=True)
                parent = await db.get(Campaign, campaign_id)
                if (
                    not current
                    or current.lease_token != token
                    or not parent
                    or parent.status == CampaignStatus.cancelled
                ):
                    raise ConflictError("Preparation cancelled")
                current.lease_until = utcnow() + timedelta(minutes=30)
                current.result = {
                    "progress_stage": _stage,
                    "current": _current,
                    "total": _total,
                    "counters": _counters,
                }

        progress_token = observer.set(heartbeat)
        safety_token = preparation_read_only.set(True)
        try:
            if stage == "communities" and completed < len(rows):
                width = min(2, self.engine.planner.settings.media_preparation_concurrency)
                batch = rows[completed : completed + width]
                contexts = await asyncio.gather(
                    *(
                        self.preparation.prepare_community_media_context(
                            grid_id, row.community_id, account_id
                        )
                        for row in batch
                    ),
                    return_exceptions=True,
                )
                from collections import Counter

                warmup_warnings = Counter(
                    cast(dict[str, int], progress_result.get("warmup_warnings", {}))
                )
                for row, context in zip(batch, contexts, strict=True):
                    if isinstance(context, BaseException):
                        warmup_warnings.update(["community_warmup_failed"])
                        async with self.sessions() as db, db.begin():
                            fresh = await db.get(Submission, row.id)
                            assert fresh
                            fresh.photo_attention = ["preparation_error"]
                        continue
                    if context.resolution_status != "resolved":
                        async with self.sessions() as db, db.begin():
                            fresh = await db.get(Submission, row.id)
                            assert fresh
                            fresh.photo_attention = ["community_unavailable"]
                    warmup_warnings.update(context.warnings)
                    progress_result["styles_ready"] = int(
                        cast(int, progress_result.get("styles_ready", 0))
                    ) + int(context.references_ready)
                progress_result["warmup_warnings"] = dict(warmup_warnings)
                completed += len(batch)
                progress_result["communities_prepared"] = completed
                if completed >= len(rows):
                    progress_result["warmup_seconds"] = (utcnow() - created_at).total_seconds()
                    stage, completed = "photos", 0
            else:
                batch = rows[completed : completed + PHOTO_BATCH_SIZE]
                result = (
                    await self.engine.planner.plan(
                        campaign_id, MediaPlanInput(), submission_ids=[r.id for r in batch]
                    )
                ).model_dump(mode="json")
                progress_result["near_duplicate_exclusions"] = int(
                    cast(int, progress_result.get("near_duplicate_exclusions", 0))
                ) + int(result.get("near_duplicate_exclusions", 0))
                completed += len(batch)
                if completed >= len(rows):
                    progress_result["photo_seconds"] = (
                        utcnow() - created_at
                    ).total_seconds() - float(cast(float, progress_result.get("warmup_seconds", 0)))
                    stage = "complete"
        except Exception:
            # One failed community/batch must not discard a full-grid preparation.
            failed_rows = rows[
                completed : completed + (1 if stage == "communities" else PHOTO_BATCH_SIZE)
            ]
            async with self.sessions() as db, db.begin():
                for failed in failed_rows:
                    fresh_row = await db.get(Submission, failed.id)
                    if fresh_row:
                        fresh_row.photo_attention = sorted(
                            set(fresh_row.photo_attention + ["preparation_error"])
                        )
            completed += len(failed_rows)
            progress_result["operation_failures"] = int(
                cast(int, progress_result.get("operation_failures", 0))
            ) + len(failed_rows)
            if completed >= len(rows):
                stage, completed = (
                    ("photos", 0) if stage == "communities" else ("complete", completed)
                )
        finally:
            observer.reset(progress_token)
            preparation_read_only.reset(safety_token)
        async with self.sessions() as session, session.begin():
            current = await session.get(CampaignPreparationJob, jid, with_for_update=True)
            campaign = await session.get(Campaign, campaign_id, with_for_update=True)
            if not current or current.lease_token != token or not campaign:
                return 1
            current.completed, current.stage, current.error_code = completed, stage, error
            current.result = {**(current.result or {}), **(result or {}), **progress_result}
            current.lease_token = current.lease_until = None
            if campaign.status == CampaignStatus.cancelled:
                current.state = "cancelled"
            elif error:
                current.state, campaign.preparation_state = "failed", "failed"
            elif stage == "complete":
                current.state = "ready"
                campaign.preparation_state = (
                    "awaiting_review"
                    if campaign.photo_review_mode == "REVIEW_BEFORE_SEND"
                    else "ready"
                )
                report, _ = await preflight(
                    session,
                    campaign_id,
                    None,
                    None,
                    self.engine.storage,
                    self.engine.planner.settings,
                )
                allocations = cast(dict[UUID, UUID], report["_assignments"])
                for row in rows:
                    fresh = await session.get(Submission, row.id)
                    assert fresh
                    fresh.account_id = allocations.get(row.id)
                memberships = (
                    await session.scalars(
                        select(CampaignAccount).where(CampaignAccount.campaign_id == campaign_id)
                    )
                ).all()
                for membership in memberships:
                    membership.assigned_count = sum(
                        aid == membership.account_id for aid in allocations.values()
                    )
            else:
                current.state = "queued"
        return 1
