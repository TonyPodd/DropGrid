"""Durable VK suggestion sender. A persisted write boundary is never replayed."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, literal, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from dropgrid.config import Settings
from dropgrid.db.models import (
    Account,
    Campaign,
    CampaignAccount,
    Community,
    MediaAsset,
    PhotoSelectionSession,
    Submission,
    utcnow,
)
from dropgrid.domain.enums import AccountStatus, CampaignStatus, SubmissionStatus
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import (
    VKAPIError,
    VKError,
    VKInputError,
    VKProtocolError,
    VKRateLimitError,
    VKTransportError,
    VKWriteDisabledError,
)
from dropgrid.integrations.vk.helpers import build_suggested_post_request
from dropgrid.integrations.vk.media import checked_media
from dropgrid.integrations.vk.models import VKAttachment, WallPostReceipt
from dropgrid.integrations.vk.photos import WallPhotoUploader
from dropgrid.photos.images import MediaStorage
from dropgrid.services.account_pools import (
    CATEGORY_UNASSIGNED,
    compatible,
    distribute,
    pool,
    required_gender,
    save_pool,
)
from dropgrid.services.campaigns import locked_campaign
from dropgrid.services.catalog import ConflictError, get_entity
from dropgrid.services.category_genders import UNPLACED, Placement, placements
from dropgrid.services.publication import attachments, record_suggested_submission

# Session-level PostgreSQL lock bounds ALL sender processes to one pipeline globally.
# It is held on a dedicated AUTOCOMMIT connection: never a transaction over HTTP.
SENDER_LOCK = 0x44524753454E44
LEASE = timedelta(minutes=15)
MAX_UPLOAD_ATTEMPTS = 3
MAX_READBACK_ATTEMPTS = 6
PRE_WALL = {"queued", "claimed", "photo_uploaded"}
READBACK = {"receipt_received", "readback_pending"}


def usable_account(account: Account) -> bool:
    return bool(
        account.status == AccountStatus.active
        and account.vk_user_id
        and account.encrypted_access_token
    )


def exclusion(
    community: Community, account: Account, placement: Placement | None = None
) -> str | None:
    if (
        not community.is_active
        or community.resolution_status != "resolved"
        or not community.vk_group_id
    ):
        return "community_unavailable"
    if not compatible(required_gender(community, placement), account):
        return "account_gender_mismatch"
    return None


async def preflight(
    session: AsyncSession,
    campaign_id: UUID,
    account_id: UUID | None,
    max_submissions: int | None,
    storage: MediaStorage,
    settings: Settings,
    *,
    account_ids: list[UUID] | None = None,
) -> tuple[dict[str, object], list[tuple[Submission, str | None]]]:
    campaign = await get_entity(session, Campaign, campaign_id)
    accounts = await pool(
        session, campaign_id, settings, account_ids or ([account_id] if account_id else None)
    )
    usable = sorted(
        ((a, quota, order) for a, quota, order in accounts if usable_account(a)),
        key=lambda item: (item[2], str(item[0].id)),
    )
    places = await placements(session, campaign.grid_id)
    rows = (
        await session.execute(
            select(Submission, Community, MediaAsset)
            .join(Community, Submission.community_id == Community.id)
            .outerjoin(MediaAsset, Submission.media_asset_id == MediaAsset.id)
            .where(Submission.campaign_id == campaign_id)
            .order_by(Community.domain, Submission.id)
        )
    ).all()
    selected: list[tuple[Submission, str | None]] = []
    intended_rows: list[tuple[Submission, Community]] = []
    missing = invalid = assigned = unavailable = incompatible = eligible = 0
    checked: dict[UUID, bool] = {}
    for row, community, asset in rows:
        reason = None
        if (
            not community.is_active
            or community.resolution_status != "resolved"
            or not community.vk_group_id
        ):
            reason = "community_unavailable"
            unavailable += 1
        elif (
            account_id
            and accounts
            and exclusion(community, accounts[0][0], places.get(community.id, UNPLACED))
        ):
            reason = "account_gender_mismatch"
            incompatible += 1
        else:
            eligible += 1
            if max_submissions is not None and len(intended_rows) >= max_submissions:
                reason = "pilot_scope_excluded"
            else:
                intended_rows.append((row, community))
                if asset is None:
                    missing += 1
                else:
                    assigned += 1
                    if asset.id not in checked:
                        try:
                            if not asset.enabled:
                                raise VKInputError("media.asset", "MediaAsset disabled")
                            await asyncio.to_thread(
                                checked_media, asset, storage, settings.vk_max_photo_bytes
                            )
                            checked[asset.id] = True
                        except VKInputError:
                            checked[asset.id] = False
                    invalid += int(not checked[asset.id])
        selected.append((row, reason))
    allocation, failures = distribute(intended_rows, usable, places)
    capacity_missing = sum(reason == "account_capacity_exhausted" for reason in failures.values())
    unplaced = {
        places.get(community.id, UNPLACED).category
        for row, community in intended_rows
        if failures.get(row.id) == CATEGORY_UNASSIGNED
    }
    incompatible += sum(reason == "account_gender_mismatch" for reason in failures.values())
    reviews = (
        await session.scalars(
            select(PhotoSelectionSession)
            .where(PhotoSelectionSession.campaign_id == campaign_id)
            .order_by(PhotoSelectionSession.created_at.desc(), PhotoSelectionSession.id.desc())
        )
    ).all()
    latest: dict[UUID, PhotoSelectionSession] = {}
    for review in reviews:
        latest.setdefault(review.submission_id, review)
    review_pending = (
        sum(row.id not in latest or latest[row.id].confirmed_at is None for row, _ in intended_rows)
        if campaign.photo_review_mode == "REVIEW_BEFORE_SEND"
        else 0
    )
    ready = (
        not campaign.is_dry_run
        and campaign.status == CampaignStatus.ready
        and campaign.preparation_state in {"legacy", "ready"}
        and bool(usable)
        and campaign.track_owner_id is not None
        and bool(campaign.track_audio_id)
        and bool(intended_rows)
        and not missing
        and not invalid
        and not failures
        and not review_pending
        and all(
            row.status == SubmissionStatus.pending
            and row.vk_send_phase in {None, "queued"}
            and row.vk_send_receipt_post_id is None
            for row, _ in selected
        )
    )
    return {
        "total": len(rows),
        "resolved_sendable": eligible,
        "unavailable": unavailable,
        "gender_incompatible": incompatible,
        "intended": len(intended_rows),
        "media_assigned": assigned,
        "media_missing": missing,
        "media_invalid": invalid,
        "account_usable": bool(usable),
        "accounts_ready": len(usable),
        "total_send_capacity": sum(quota for _, quota, _ in usable),
        "capacity_unassigned": capacity_missing,
        "category_unassigned": sum(reason == CATEGORY_UNASSIGNED for reason in failures.values()),
        "unassigned_categories": sorted(unplaced, key=lambda c: (c is None, c or "")),
        "review_pending": review_pending,
        "assigned_per_account": [
            {
                "account_id": str(a.id),
                "name": a.name,
                "assigned": sum(v == a.id for v in allocation.values()),
                "quota": quota,
            }
            for a, quota, _ in usable
        ],
        "ready": bool(ready),
        "_assignments": allocation,
    }, selected


async def start_campaign(
    session: AsyncSession,
    campaign_id: UUID,
    account_id: UUID | None,
    max_submissions: int | None,
    storage: MediaStorage,
    settings: Settings,
    *,
    account_ids: list[UUID] | None = None,
) -> Campaign:
    campaign = await locked_campaign(session, campaign_id)
    if campaign.is_dry_run:
        raise ConflictError("Dry-run campaigns cannot be sent")
    report, rows = await preflight(
        session,
        campaign_id,
        account_id,
        max_submissions,
        storage,
        settings,
        account_ids=account_ids,
    )
    if not report["ready"]:
        raise ConflictError(
            "Campaign preflight failed: check account, lifecycle, audio, local media and "
            "grid category distribution"
        )
    allocations = cast(dict[UUID, UUID], report["_assignments"])
    # Accounts in their send sequence; allocation insertion order is the send order.
    selected_ids = list(dict.fromkeys(allocations.values()))
    send_order = {row_id: position for position, row_id in enumerate(allocations)}
    await save_pool(
        session,
        campaign_id,
        account_ids or ([account_id] if account_id else selected_ids),
        settings,
    )
    for membership in (
        await session.scalars(
            select(CampaignAccount).where(CampaignAccount.campaign_id == campaign_id)
        )
    ).all():
        membership.assigned_count = sum(
            aid == membership.account_id for aid in allocations.values()
        )
    campaign.account_id = account_id or (selected_ids[0] if selected_ids else None)
    campaign.status = CampaignStatus.running
    campaign.started_at = utcnow()
    for row, reason in rows:
        row.account_id = allocations.get(row.id)
        row.send_order = send_order.get(row.id)
        if reason:
            row.status = SubmissionStatus.skipped
            row.error_code, row.error_message = reason, "Submission excluded from sending scope"
        else:
            row.vk_send_guid = row.id
            row.vk_send_phase = "queued"
    await session.flush()
    return campaign


async def cancel_campaign(session: AsyncSession, campaign_id: UUID) -> Campaign:
    campaign = await locked_campaign(session, campaign_id)
    if campaign.status not in {
        CampaignStatus.ready,
        CampaignStatus.running,
        CampaignStatus.monitoring,
    }:
        raise ConflictError("Campaign cannot be cancelled in its current status")
    campaign.status = CampaignStatus.cancelled
    campaign.completed_at = utcnow()
    return campaign


async def refresh_campaigns(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions() as session, session.begin():
        campaigns = (
            await session.scalars(
                select(Campaign)
                .where(Campaign.status.in_([CampaignStatus.running, CampaignStatus.monitoring]))
                .with_for_update(skip_locked=True)
            )
        ).all()
        for campaign in campaigns:
            statuses = (
                await session.scalars(
                    select(Submission.status).where(Submission.campaign_id == campaign.id)
                )
            ).all()
            if any(s in {SubmissionStatus.pending, SubmissionStatus.sending} for s in statuses):
                campaign.status = CampaignStatus.running
            elif SubmissionStatus.submitted in statuses:
                campaign.status = CampaignStatus.monitoring
            else:
                successful = any(
                    s in {SubmissionStatus.published, SubmissionStatus.not_found} for s in statuses
                )
                campaign.status = CampaignStatus.completed if successful else CampaignStatus.failed
                campaign.completed_at = utcnow()


class CampaignSender:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        client: VKClient,
        tokens: TokenProvider,
        storage: MediaStorage,
        uploader: WallPhotoUploader,
    ) -> None:
        self.sessions, self.client, self.tokens = sessions, client, tokens
        self.storage, self.uploader = storage, uploader

    @asynccontextmanager
    async def _serialized(self) -> AsyncIterator[bool]:
        # A session advisory lock also prevents an expired DB lease from overlapping a
        # still-live uploader. Crash/disconnection releases it automatically.
        engine = cast(AsyncEngine, self.sessions.kw["bind"])
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": SENDER_LOCK}
            )
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    try:
                        await connection.execute(
                            text("SELECT pg_advisory_unlock(:key)"), {"key": SENDER_LOCK}
                        )
                    except BaseException:
                        # Never return a connection carrying a session lock to the pool.
                        await connection.invalidate()
                        raise

    async def _claim(self, submission_id: UUID | None) -> Submission | None:
        now = utcnow()
        earlier = aliased(Submission)
        async with self.sessions() as session, session.begin():
            query = (
                select(Submission)
                .join(Campaign)
                .outerjoin(Account, Submission.account_id == Account.id)
                .where(
                    Campaign.is_dry_run.is_(False),
                    Submission.vk_send_readback_attempts < MAX_READBACK_ATTEMPTS,
                    or_(
                        Submission.vk_send_phase != "wall_post_started",
                        Submission.error_code.is_(None),
                    ),
                    or_(
                        Submission.vk_send_phase.in_(READBACK | {"wall_post_started"}),
                        and_(
                            literal(self.client.settings.vk_write_enabled),
                            or_(Account.vk_next_send_at.is_(None), Account.vk_next_send_at <= now),
                        ),
                    ),
                    Submission.status.in_([SubmissionStatus.pending, SubmissionStatus.sending]),
                    Submission.vk_send_phase.is_not(None),
                    or_(
                        Submission.vk_send_lease_until.is_(None),
                        Submission.vk_send_lease_until <= now,
                    ),
                    or_(Submission.vk_send_next_at.is_(None), Submission.vk_send_next_at <= now),
                    or_(
                        Campaign.status == CampaignStatus.running,
                        Submission.vk_send_phase.in_(READBACK),
                    ),
                    # One account at a time: the next account of a campaign starts only
                    # when earlier ones have no upload work left. Recovery is never held.
                    or_(
                        Submission.vk_send_phase.not_in(PRE_WALL),
                        ~exists().where(
                            earlier.campaign_id == Submission.campaign_id,
                            earlier.send_order < Submission.send_order,
                            earlier.account_id.is_distinct_from(Submission.account_id),
                            earlier.status.in_(
                                [SubmissionStatus.pending, SubmissionStatus.sending]
                            ),
                            earlier.vk_send_phase.in_(PRE_WALL),
                        ),
                    ),
                )
                .order_by(
                    Submission.send_order.asc().nulls_last(), Submission.created_at, Submission.id
                )
                .with_for_update(of=Submission, skip_locked=True)
            )
            if submission_id:
                query = query.where(Submission.id == submission_id)
            # Bounded scan: a paused account must not starve receipt recovery.
            for row in (await session.scalars(query.limit(20))).all():
                if row.vk_send_phase == "wall_post_started":
                    row.error_code = "wall_post_outcome_unknown"
                    row.error_message = (
                        "Write outcome unknown; manual read-only reconciliation required"
                    )
                    row.vk_send_next_at = None
                    continue
                if row.vk_send_phase in PRE_WALL:
                    if not self.client.settings.vk_write_enabled:
                        continue
                    account = await session.get(Account, row.account_id)
                    if account and account.vk_next_send_at and account.vk_next_send_at > now:
                        continue
                    if row.attempt_count >= MAX_UPLOAD_ATTEMPTS:
                        row.status = SubmissionStatus.failed
                        row.error_code = "photo_upload_attempts_exhausted"
                        row.error_message = "Photo upload attempt limit reached"
                        continue
                    row.attempt_count += 1
                    row.vk_send_phase = "claimed"
                    if account:
                        account.vk_next_send_at = now + timedelta(
                            seconds=self.client.settings.vk_send_interval_seconds
                        )
                elif row.vk_send_phase not in READBACK:
                    continue
                row.status = SubmissionStatus.sending
                row.vk_send_guid = row.vk_send_guid or row.id
                row.vk_send_lease_token, row.vk_send_lease_until = uuid4(), now + LEASE
                row.vk_send_started_at = row.vk_send_started_at or now
                return row
        return None

    async def _save(self, claim: Submission, **changes: object) -> bool:
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(Submission)
                .where(
                    Submission.id == claim.id,
                    Submission.vk_send_lease_token == claim.vk_send_lease_token,
                    Submission.vk_send_lease_until > utcnow(),
                )
                .with_for_update()
            )
            if row is None:
                return False
            for key, value in changes.items():
                setattr(row, key, value)
            return True

    async def _wall_boundary(self, claim: Submission) -> bool:
        async with self.sessions() as session, session.begin():
            # Same lock as cancel/start: cancellation linearizes before this boundary.
            campaign = await locked_campaign(session, claim.campaign_id)
            row = await session.scalar(
                select(Submission)
                .where(
                    Submission.id == claim.id,
                    Submission.vk_send_lease_token == claim.vk_send_lease_token,
                    Submission.vk_send_lease_until > utcnow(),
                    Submission.vk_send_phase == "photo_uploaded",
                )
                .with_for_update()
            )
            if row is None or campaign.status != CampaignStatus.running:
                return False
            account = await session.get(Account, row.account_id)
            community = await session.get(Community, row.community_id)
            if (
                account is None
                or not usable_account(account)
                or community is None
                or exclusion(community, account)
            ):
                return False
            self._write_gate(community.vk_group_id)
            row.vk_send_phase = "wall_post_started"
            return True

    def _write_gate(self, group_id: int | None) -> None:
        self.client.require_write("wall.post")
        allowlist = self.client.settings.vk_test_allowed_community_ids
        if group_id is None or (allowlist and group_id not in allowlist):
            raise VKWriteDisabledError("wall.post", "Target is outside configured sending scope")

    async def _readback(
        self, claim: Submission, campaign: Campaign, community: Community, account: Account
    ) -> None:
        assert account.id and community.vk_group_id and claim.vk_send_receipt_post_id is not None
        token = await self.tokens.get_token(account.id)
        posts = await self.client.get_wall_post_by_id(
            -community.vk_group_id,
            claim.vk_send_receipt_post_id,
            access_token=token,
            account_id=account.id,
        )
        matching = [p for p in posts.items if p.id == claim.vk_send_receipt_post_id]
        if len(matching) != 1:
            raise VKProtocolError("wall.getById", "Suggestion not yet visible")
        post = matching[0]
        photos, audio = attachments(post, "photo"), attachments(post, "audio")
        if (
            post.owner_id != -community.vk_group_id
            or post.from_id != account.vk_user_id
            or post.post_type != "suggest"
            or len(photos) != 1
            or photos[0][0] != -community.vk_group_id
            or (audio and audio != [(campaign.track_owner_id, campaign.track_audio_id)])
        ):
            raise VKProtocolError("wall.getById", "Suggestion identity could not be verified")
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(Submission)
                .where(
                    Submission.id == claim.id,
                    Submission.vk_send_lease_token == claim.vk_send_lease_token,
                    Submission.vk_send_lease_until > utcnow(),
                )
                .with_for_update()
            )
            if row is None:
                return
            await record_suggested_submission(
                session,
                row,
                account_id=account.id,
                receipt=WallPostReceipt(post_id=claim.vk_send_receipt_post_id),
                suggestion=post,
            )
            row.vk_send_phase = "verified"
            row.vk_send_lease_token = row.vk_send_lease_until = None
            row.error_code = row.error_message = None

    async def _process(self, claim: Submission) -> None:
        async with self.sessions() as session:
            campaign = await get_entity(session, Campaign, claim.campaign_id)
            community = await get_entity(session, Community, claim.community_id)
            if claim.account_id is None:
                await self._save(
                    claim, status=SubmissionStatus.failed, error_code="account_missing"
                )
                return
            account = await get_entity(session, Account, claim.account_id)
            asset = (
                await session.get(MediaAsset, claim.media_asset_id)
                if claim.media_asset_id
                else None
            )
        phase = claim.vk_send_phase
        try:
            if phase in READBACK:
                await self._readback(claim, campaign, community, account)
                return
            if campaign.status != CampaignStatus.running:
                await self._save(
                    claim, status=SubmissionStatus.skipped, error_code="campaign_cancelled"
                )
                return
            if not usable_account(account):
                raise VKInputError("credentials", "Account unavailable")
            reason = exclusion(community, account)
            if reason:
                await self._save(
                    claim,
                    status=SubmissionStatus.skipped,
                    error_code=reason,
                    error_message="Community excluded from sending",
                )
                return
            if (
                asset is None
                or not asset.enabled
                or campaign.track_owner_id is None
                or not campaign.track_audio_id
            ):
                raise VKInputError("media.asset", "Prepared media and audio required")
            self._write_gate(community.vk_group_id)
            data = await asyncio.to_thread(
                checked_media, asset, self.storage, self.client.settings.vk_max_photo_bytes
            )
            token = await self.tokens.get_token(account.id)
            assert community.vk_group_id is not None
            photo = await self.uploader.upload(
                data,
                community_id=community.vk_group_id,
                account_id=account.id,
                access_token=token,
                mime_type=asset.mime_type,
            )
            if not await self._save(
                claim,
                vk_send_phase="photo_uploaded",
                vk_photo_upload_owner_id=photo.owner_id,
                vk_photo_upload_id=photo.media_id,
            ):
                return
            claim.vk_photo_upload_owner_id, claim.vk_photo_upload_id = (
                photo.owner_id,
                photo.media_id,
            )
            payload = build_suggested_post_request(
                community.vk_group_id,
                caption=campaign.caption,
                photo=photo,
                audio=VKAttachment("audio", campaign.track_owner_id, campaign.track_audio_id),
            )
            payload["guid"] = str(claim.vk_send_guid)
            if not await self._wall_boundary(claim):
                await self._save(
                    claim,
                    status=SubmissionStatus.skipped,
                    error_code="send_precondition_changed",
                    error_message="Sending cancelled or preconditions changed",
                )
                return
            phase = "wall_post_started"
            receipt = await self.client.create_wall_post(
                payload, access_token=token, account_id=account.id
            )
            if not await self._save(
                claim, vk_send_receipt_post_id=receipt.post_id, vk_send_phase="receipt_received"
            ):
                return
            phase = "receipt_received"
            claim.vk_send_receipt_post_id = receipt.post_id
            await self._readback(claim, campaign, community, account)
        except VKError as error:
            if phase in READBACK:
                attempts = claim.vk_send_readback_attempts + 1
                await self._save(
                    claim,
                    vk_send_phase="readback_pending",
                    vk_send_readback_attempts=attempts,
                    error_code=(
                        "suggestion_readback_pending"
                        if attempts < MAX_READBACK_ATTEMPTS
                        else "suggestion_readback_exhausted"
                    ),
                    error_message="Receipt saved; read-only verification pending",
                    vk_send_next_at=utcnow()
                    + timedelta(seconds=min(3600, 30 * 2 ** min(attempts, 7)))
                    if attempts < MAX_READBACK_ATTEMPTS
                    else None,
                    vk_send_lease_token=None,
                    vk_send_lease_until=None,
                )
            elif phase == "wall_post_started":
                known_rejection = isinstance(error, VKAPIError)
                await self._save(
                    claim,
                    status=SubmissionStatus.failed if known_rejection else SubmissionStatus.sending,
                    error_code=f"vk_rejected_{error.code}"
                    if known_rejection
                    else "wall_post_outcome_unknown",
                    error_message="VK rejected the suggestion"
                    if known_rejection
                    else "Write outcome unknown; do not resend",
                    vk_send_next_at=None,
                    vk_send_lease_token=None,
                    vk_send_lease_until=None,
                )
            else:
                retry = (
                    isinstance(error, (VKTransportError, VKRateLimitError))
                    and claim.attempt_count < MAX_UPLOAD_ATTEMPTS
                )
                code = (
                    "photo_upload_retry"
                    if retry
                    else (
                        f"vk_error_{error.code}"
                        if error.code is not None
                        else "send_preflight_failed"
                    )
                )
                await self._save(
                    claim,
                    status=SubmissionStatus.pending if retry else SubmissionStatus.failed,
                    vk_send_phase="queued",
                    error_code=code,
                    error_message="Sending precondition or photo upload failed",
                    vk_send_next_at=utcnow() + timedelta(seconds=60 * claim.attempt_count),
                    vk_send_lease_token=None,
                    vk_send_lease_until=None,
                )

    async def tick(self, submission_id: UUID | None = None) -> bool:
        try:
            async with self._serialized() as acquired:
                if not acquired:
                    return False
                claim = await self._claim(submission_id)
                if claim is None:
                    return False
                await self._process(claim)
                return True
        finally:
            await refresh_campaigns(self.sessions)
