"""Read-only VK reconciliation. Short DB leases fence every result commit."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import (
    Account,
    Campaign,
    Community,
    MediaAsset,
    Submission,
    VKNotificationCursor,
    utcnow,
)
from dropgrid.domain.enums import AccountStatus, SubmissionStatus
from dropgrid.integrations.vk.client import VKClient, parse_response
from dropgrid.integrations.vk.credentials import TokenProvider
from dropgrid.integrations.vk.errors import VKError
from dropgrid.integrations.vk.models import VKNotification, WallPostDetails, WallPostReceipt
from dropgrid.services.catalog import ConflictError, NotFoundError

LEASE = timedelta(minutes=15)
INTERVAL = timedelta(minutes=5)
OVERLAP = timedelta(minutes=5)
MAX_PAGES = 3


def safe_evidence(value: dict[str, object]) -> dict[str, object]:
    """The API never exposes arbitrary JSON from historical/manually edited rows."""
    booleans = {
        "notification_match",
        "notification_photo_match",
        "notification_community_match",
        "notification_text_present",
        "notification_marker_present",
        "notification_audio_match",
        "audio_match",
        "text_present",
        "marker_present",
        "notifications_complete",
        "pending_after_deadline",
        "wall_window_complete",
    }
    labels = {
        "source": {"wall_publish", "fallback"},
        "suggestion_state": {"unknown", "pending", "published_same_object", "missing_or_degraded"},
        "result": {
            "missing_receipt",
            "ambiguous_receipt",
            "published_id_conflict",
            "notification_photo_mismatch",
            "verified",
            "unverified_notification",
            "read_error",
            "unexpected_suggestion_response",
            "incomplete_candidate",
            "ambiguous",
            "unverified_candidate",
            "no_match",
            "incomplete_window",
        },
        "error_kind": {
            "VKAuthenticationError",
            "VKCredentialUnavailableError",
            "VKPermissionError",
            "VKCommunityUnavailableError",
            "VKRateLimitError",
            "VKTransportError",
            "VKProtocolError",
            "VKAPIError",
            "VKCaptchaRequiredError",
            "VKInputError",
        },
    }
    return {
        k: v
        for k, v in value.items()
        if (
            (k in booleans and (type(v) is bool or v is None))
            or (k in {"candidate_count", "error_code"} and (type(v) is int or v is None))
            or (k in labels and isinstance(v, str) and v in labels[k])
        )
    }


def attachments(post: WallPostDetails, kind: str) -> list[tuple[int, int]]:
    result = []
    for attachment in post.attachments:
        value = attachment.get(kind)
        if attachment.get("type") == kind and isinstance(value, dict):
            owner, identity = value.get("owner_id"), value.get("id")
            if type(owner) is int and type(identity) is int and owner and identity > 0:
                result.append((owner, identity))
    return result


def vk_date(value: int | None) -> datetime | None:
    if type(value) is not int or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except (ValueError, OverflowError, OSError):
        return None


def deadline(target: "Target") -> datetime:
    try:
        return target.suggested_at + timedelta(hours=target.horizon)
    except OverflowError:
        return datetime.max.replace(tzinfo=UTC)


async def record_suggested_submission(
    session: AsyncSession,
    submission: Submission,
    *,
    account_id: UUID,
    receipt: WallPostReceipt,
    suggestion: WallPostDetails,
) -> Submission:
    """Caller commits this transaction. Never sends or uploads; counts a receipt once."""
    row = await session.scalar(
        select(Submission)
        .where(Submission.id == submission.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFoundError("Submission not found")
    community = await session.get(Community, row.community_id)
    account = await session.get(Account, account_id, with_for_update=True)
    group = community.vk_group_id if community else None
    photos = attachments(suggestion, "photo")
    canonical = [p for p in photos if group and p[0] == -group]
    suggested_at = vk_date(suggestion.date)
    if (
        not group
        or not account
        or account.status != AccountStatus.active
        or not account.vk_user_id
        or receipt.post_id <= 0
        or suggestion.id != receipt.post_id
        or suggestion.owner_id != -group
        or suggestion.from_id != account.vk_user_id
        or suggestion.post_type != "suggest"
        or len(canonical) != 1
        or not suggested_at
    ):
        raise ConflictError("Suggestion receipt/read-back is not verified")
    if row.vk_suggested_post_id is not None:
        if (
            row.vk_suggested_post_id != receipt.post_id
            or row.account_id != account_id
            or (row.vk_canonical_photo_owner_id, row.vk_canonical_photo_id) != canonical[0]
        ):
            raise ConflictError("Conflicting suggestion receipt")
        return row
    if row.status not in (SubmissionStatus.pending, SubmissionStatus.sending):
        raise ConflictError("Submission cannot record a new receipt")
    existing = await session.scalar(
        select(Submission.id)
        .join(Community, Community.id == Submission.community_id)
        .where(
            Submission.id != row.id,
            Submission.account_id == account_id,
            Submission.vk_suggested_post_id == receipt.post_id,
            Community.vk_group_id == group,
        )
        .limit(1)
    )
    if existing:
        raise ConflictError("Suggestion receipt already belongs to another Submission")
    row.account_id = account_id
    row.vk_suggested_post_id = receipt.post_id
    row.vk_suggested_at = suggested_at
    row.vk_canonical_photo_owner_id, row.vk_canonical_photo_id = canonical[0]
    audio = attachments(suggestion, "audio")
    if len(audio) == 1:
        row.vk_audio_owner_id, row.vk_audio_id = audio[0]
    row.status = SubmissionStatus.submitted
    row.submitted_at = suggested_at
    row.vk_next_check_at = utcnow()
    if row.media_asset_id:
        await session.execute(
            update(MediaAsset)
            .where(MediaAsset.id == row.media_asset_id)
            .values(usage_count=MediaAsset.usage_count + 1, last_used_at=suggested_at)
        )
    await session.flush()
    return row


@dataclass(frozen=True)
class Target:
    id: UUID
    account_id: UUID
    group: int
    suggested_id: int
    suggested_at: datetime
    photo: tuple[int, int]
    audio: tuple[int | None, int | None]
    horizon: int
    lease: UUID


@dataclass(frozen=True)
class Mapping:
    group: int
    suggested_id: int
    published_id: int
    photo: tuple[int, int]
    audio: tuple[int, int] | None
    text_present: bool
    marker_present: bool


def marker_present(text: object) -> bool:
    return isinstance(text, str) and bool(
        re.search(r"\[DropGrid-test:[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\]", text)
    )


def notification_mapping(event: VKNotification) -> list[Mapping]:
    if event.type != "wall_publish" or not isinstance(event.feedback, dict):
        return []
    feedback = event.feedback
    owner, to, published = feedback.get("owner_id"), feedback.get("to_id"), feedback.get("id")
    if owner is not None and (type(owner) is not int or owner >= 0):
        return []
    if to is not None and (type(to) is not int or to >= 0):
        return []
    if owner is not None and to is not None and owner != to:
        return []
    community = to if to is not None else owner
    if type(community) is not int or community >= 0 or type(published) is not int or published <= 0:
        return []
    raw = feedback.get("attachments")
    if not isinstance(raw, list):
        return []
    audio = None
    for attachment in raw:
        if isinstance(attachment, dict) and attachment.get("type") == "audio":
            a = attachment.get("audio")
            if isinstance(a, dict) and type(a.get("owner_id")) is int and type(a.get("id")) is int:
                audio = (a["owner_id"], a["id"])
    mappings = []
    for attachment in raw:
        if not isinstance(attachment, dict) or attachment.get("type") != "photo":
            continue
        photo = attachment.get("photo")
        if not isinstance(photo, dict):
            continue
        owner, identity, old_id = photo.get("owner_id"), photo.get("id"), photo.get("post_id")
        if (
            type(owner) is int
            and owner == community
            and type(identity) is int
            and identity > 0
            and type(old_id) is int
            and old_id > 0
        ):
            mappings.append(
                Mapping(
                    -community,
                    old_id,
                    published,
                    (owner, identity),
                    audio,
                    bool(feedback.get("text")),
                    marker_present(feedback.get("text")),
                )
            )
    return list(dict.fromkeys(mappings))


def verified_publication(
    post: WallPostDetails, target: Target, post_id: int, now: datetime
) -> bool:
    date = vk_date(post.date)
    return (
        post.id == post_id
        and post.owner_id == -target.group
        and post.post_type == "post"
        and target.photo in attachments(post, "photo")
        and date is not None
        and target.suggested_at <= date <= now + timedelta(minutes=1)
    )


class PublicationMonitor:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], client: VKClient, tokens: TokenProvider
    ) -> None:
        self.sessions, self.client, self.tokens = sessions, client, tokens

    async def _claim(self, submission_id: UUID, now: datetime) -> Target | None:
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(Submission)
                .where(
                    Submission.id == submission_id,
                    Submission.status == SubmissionStatus.submitted,
                    or_(
                        Submission.vk_monitor_lease_until.is_(None),
                        Submission.vk_monitor_lease_until <= now,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            community = await session.get(Community, row.community_id)
            campaign = await session.get(Campaign, row.campaign_id)
            if (
                not community
                or not community.vk_group_id
                or not campaign
                or not row.account_id
                or not row.vk_suggested_post_id
                or not row.vk_suggested_at
                or row.vk_canonical_photo_owner_id != -community.vk_group_id
                or not row.vk_canonical_photo_id
            ):
                row.vk_last_checked_at, row.vk_next_check_at = now, now + INTERVAL
                row.vk_monitor_evidence = {"result": "missing_receipt"}
                return None
            lease = uuid4()
            duplicates = (
                await session.scalars(
                    select(Submission.id)
                    .join(Community, Community.id == Submission.community_id)
                    .where(
                        Submission.account_id == row.account_id,
                        Submission.vk_suggested_post_id == row.vk_suggested_post_id,
                        Community.vk_group_id == community.vk_group_id,
                        Submission.status.in_(
                            [SubmissionStatus.submitted, SubmissionStatus.published]
                        ),
                    )
                )
            ).all()
            if len(duplicates) != 1:
                row.vk_monitor_evidence = {"result": "ambiguous_receipt"}
                row.vk_last_checked_at, row.vk_next_check_at = now, now + INTERVAL
                return None
            row.vk_monitor_lease_token, row.vk_monitor_lease_until = lease, now + LEASE
            return Target(
                row.id,
                row.account_id,
                community.vk_group_id,
                row.vk_suggested_post_id,
                row.vk_suggested_at,
                (row.vk_canonical_photo_owner_id, row.vk_canonical_photo_id),
                (row.vk_audio_owner_id, row.vk_audio_id),
                campaign.publication_check_hours,
                lease,
            )

    async def _finish(
        self,
        target: Target,
        evidence: dict[str, object],
        *,
        post: WallPostDetails | None = None,
        not_found: bool = False,
    ) -> bool:
        now = utcnow()
        async with self.sessions() as session, session.begin():
            row = await session.scalar(
                select(Submission)
                .where(
                    Submission.id == target.id,
                    Submission.vk_monitor_lease_token == target.lease,
                    Submission.vk_monitor_lease_until > now,
                )
                .with_for_update()
            )
            if (
                row is None
                or row.status != SubmissionStatus.submitted
                or row.account_id != target.account_id
                or row.vk_suggested_post_id != target.suggested_id
                or (row.vk_canonical_photo_owner_id, row.vk_canonical_photo_id) != target.photo
            ):
                return False
            # Serialize receipt registration against final publication commits.
            await session.get(Account, target.account_id, with_for_update=True)
            community = await session.get(Community, row.community_id)
            duplicates = (
                await session.scalars(
                    select(Submission.id)
                    .join(Community, Community.id == Submission.community_id)
                    .where(
                        Submission.account_id == target.account_id,
                        Submission.vk_suggested_post_id == target.suggested_id,
                        Community.vk_group_id == target.group,
                        Submission.status.in_(
                            [SubmissionStatus.submitted, SubmissionStatus.published]
                        ),
                    )
                )
            ).all()
            if not community or community.vk_group_id != target.group or len(duplicates) != 1:
                row.vk_monitor_evidence = {"result": "ambiguous_receipt"}
                row.vk_monitor_lease_token = row.vk_monitor_lease_until = None
                row.vk_next_check_at = now + INTERVAL
                return False
            if post is not None:
                if not verified_publication(post, target, post.id, now):
                    return False
                row.status = SubmissionStatus.published
                row.vk_published_post_id = post.id
                row.published_at = vk_date(post.date)
                row.published_post_url = f"https://vk.com/wall-{target.group}_{post.id}"
                row.vk_publication_detected_at = now
            elif not_found:
                row.status = SubmissionStatus.not_found
            row.vk_last_checked_at = now
            row.vk_next_check_at = (
                now + INTERVAL if row.status == SubmissionStatus.submitted else None
            )
            row.vk_monitor_evidence = evidence
            row.vk_monitor_lease_token = row.vk_monitor_lease_until = None
        return True

    async def _note(self, ids: list[UUID], result: str) -> None:
        async with self.sessions() as session, session.begin():
            await session.execute(
                update(Submission)
                .where(
                    Submission.id.in_(ids),
                    or_(
                        Submission.vk_monitor_lease_until.is_(None),
                        Submission.vk_monitor_lease_until <= utcnow(),
                    ),
                )
                .values(vk_monitor_evidence={"source": "wall_publish", "result": result})
            )

    async def _confirm(self, target: Target, post_id: int) -> WallPostDetails | None:
        token = await self.tokens.get_token(target.account_id)
        posts = await self.client.get_wall_post_by_id(
            -target.group, post_id, access_token=token, account_id=target.account_id
        )
        if len(posts.items) == 1 and verified_publication(
            posts.items[0], target, post_id, utcnow()
        ):
            return posts.items[0]
        return None

    async def process_notification(self, account_id: UUID, event: VKNotification) -> bool:
        """False means unresolved relevant evidence; cursor must not hide a failed event."""
        mappings = notification_mapping(event)
        for mapping in mappings:
            async with self.sessions() as session:
                rows = (
                    await session.scalars(
                        select(Submission)
                        .join(Community, Community.id == Submission.community_id)
                        .where(
                            Submission.account_id == account_id,
                            Submission.status.in_(
                                [SubmissionStatus.submitted, SubmissionStatus.published]
                            ),
                            Community.vk_group_id == mapping.group,
                            Submission.vk_suggested_post_id == mapping.suggested_id,
                        )
                    )
                ).all()
                # Require a unique receipt, before content matching.
                if len(rows) > 1:
                    await self._note([r.id for r in rows], "ambiguous_receipt")
                    return False
                if not rows:
                    continue
                row = rows[0]
                if row.status == SubmissionStatus.published:
                    if row.vk_published_post_id != mapping.published_id:
                        await self._note([row.id], "published_id_conflict")
                        return False
                    continue
                if (row.vk_canonical_photo_owner_id, row.vk_canonical_photo_id) != mapping.photo:
                    await self._note([row.id], "notification_photo_mismatch")
                    return False
                submission_id = row.id
            target = await self._claim(submission_id, utcnow())
            if target is None:
                return False
            evidence: dict[str, object] = {
                "source": "wall_publish",
                "notification_match": True,
                "notification_photo_match": True,
                "notification_community_match": True,
                "notification_text_present": mapping.text_present,
                "notification_marker_present": mapping.marker_present,
                "notification_audio_match": mapping.audio == target.audio
                if mapping.audio
                else None,
            }
            try:
                post = await self._confirm(target, mapping.published_id)
                evidence["result"] = "verified" if post else "unverified_notification"
                if post:
                    evidence["audio_match"] = target.audio in attachments(post, "audio")
                    evidence["text_present"] = bool(post.text)
                    evidence["marker_present"] = marker_present(post.text)
                committed = await self._finish(target, evidence, post=post)
                if not post or not committed:
                    return False
            except VKError as error:
                await self._finish(
                    target,
                    {
                        **evidence,
                        "result": "read_error",
                        "error_kind": type(error).__name__,
                        "error_code": error.code,
                    },
                )
                return False
        return True

    async def poll_account(self, account_id: UUID, *, force: bool = False) -> bool:
        now, lease = utcnow(), uuid4()
        async with self.sessions() as session, session.begin():
            account = await session.get(Account, account_id)
            if (
                not account
                or account.status != AccountStatus.active
                or not account.token_configured
            ):
                return False
            await session.execute(
                insert(VKNotificationCursor).values(account_id=account_id).on_conflict_do_nothing()
            )
            cursor = await session.scalar(
                select(VKNotificationCursor)
                .where(
                    VKNotificationCursor.account_id == account_id,
                    or_(
                        VKNotificationCursor.lease_until.is_(None),
                        VKNotificationCursor.lease_until <= now,
                    ),
                )
                .with_for_update(skip_locked=True)
            )
            if not cursor or (not force and cursor.next_poll_at and cursor.next_poll_at > now):
                return False
            earliest = await session.scalar(
                select(Submission.vk_suggested_at)
                .where(
                    Submission.account_id == account_id,
                    Submission.status == SubmissionStatus.submitted,
                    Submission.vk_suggested_at.is_not(None),
                )
                .order_by(Submission.vk_suggested_at)
                .limit(1)
            )
            start = (
                cursor.last_polled_at - OVERLAP
                if cursor.last_polled_at
                else min(earliest or now, now - timedelta(days=1))
            )
            cursor.lease_token, cursor.lease_until = lease, now + LEASE
        complete = False
        try:
            token = await self.tokens.get_token(account_id)
            next_from = None
            seen: set[str] = set()
            for _ in range(MAX_PAGES):
                page = await self.client.get_notifications(
                    access_token=token,
                    account_id=account_id,
                    count=100,
                    start_time=max(0, int(start.timestamp())),
                    end_time=int(now.timestamp()),
                    start_from=next_from,
                    filters=["wall"],
                )
                events_ok = True
                for event in page.items:
                    if not await self.process_notification(account_id, event):
                        events_ok = False
                if not events_ok:
                    break
                next_from = page.next_from
                if not next_from:
                    complete = True
                    break
                if next_from in seen:
                    break
                seen.add(next_from)
        except VKError:
            complete = False
        finally:
            async with self.sessions() as session, session.begin():
                cursor = await session.scalar(
                    select(VKNotificationCursor)
                    .where(
                        VKNotificationCursor.account_id == account_id,
                        VKNotificationCursor.lease_token == lease,
                        VKNotificationCursor.lease_until > utcnow(),
                    )
                    .with_for_update()
                )
                if cursor:
                    if complete:
                        cursor.last_polled_at = now
                    cursor.next_poll_at = utcnow() + INTERVAL
                    cursor.lease_token = cursor.lease_until = None
                else:
                    complete = False
        return complete

    async def reconcile(self, submission_id: UUID, *, notifications_complete: bool) -> None:
        target = await self._claim(submission_id, utcnow())
        if target is None:
            return
        evidence: dict[str, object] = {
            "source": "fallback",
            "notification_match": False,
            "notifications_complete": notifications_complete,
            "suggestion_state": "unknown",
        }
        try:
            token = await self.tokens.get_token(target.account_id)
            direct = await self.client.get_wall_post_by_id(
                -target.group, target.suggested_id, access_token=token, account_id=target.account_id
            )
            if len(direct.items) > 1 or (
                direct.items
                and (
                    direct.items[0].id != target.suggested_id
                    or direct.items[0].owner_id != -target.group
                )
            ):
                await self._finish(target, {**evidence, "result": "unexpected_suggestion_response"})
                return
            if len(direct.items) == 1:
                old = direct.items[0]
                if (
                    old.id == target.suggested_id
                    and old.owner_id == -target.group
                    and old.post_type == "suggest"
                    and (vk_date(old.date) is not None or bool(old.text) or bool(old.attachments))
                ):
                    evidence["suggestion_state"] = "pending"
                    evidence["pending_after_deadline"] = utcnow() >= deadline(target)
                    await self._finish(target, evidence)
                    return
                if verified_publication(old, target, target.suggested_id, utcnow()):
                    evidence["suggestion_state"] = "published_same_object"
                    evidence["audio_match"] = target.audio in attachments(old, "audio")
                    evidence["marker_present"] = marker_present(old.text)
                    await self._finish(target, evidence, post=old)
                    return
            evidence["suggestion_state"] = "missing_or_degraded"
            candidates: dict[int, WallPostDetails] = {}
            complete = False
            offset = 0
            seen_ids: set[int] = set()
            previous_date: datetime | None = None
            now = utcnow()
            for _ in range(MAX_PAGES):
                page = await self.client.get_wall_posts(
                    f"club{target.group}",
                    access_token=token,
                    account_id=target.account_id,
                    count=100,
                    offset=offset,
                )
                posts = [
                    parse_response(TypeAdapter(WallPostDetails), p, "wall.get") for p in page.items
                ]
                for post in posts:
                    if (
                        target.photo in attachments(post, "photo")
                        and post.post_type == "post"
                        and (post.owner_id is None or post.date is None)
                    ):
                        await self._finish(target, {**evidence, "result": "incomplete_candidate"})
                        return
                    if verified_publication(post, target, post.id, now):
                        candidates[post.id] = post
                offset += len(posts)
                seen_ids.update(p.id for p in posts)
                dates = [vk_date(p.date) for p in posts if p.is_pinned != 1]
                valid_dates = [d for d in dates if d is not None]
                if (
                    len(valid_dates) != len(dates)
                    or valid_dates != sorted(valid_dates, reverse=True)
                    or (
                        previous_date is not None and valid_dates and previous_date < valid_dates[0]
                    )
                ):
                    evidence["result"] = "incomplete_window"
                    await self._finish(target, evidence)
                    return
                reached_start = (
                    len(dates) == len(valid_dates)
                    and bool(valid_dates)
                    and valid_dates == sorted(valid_dates, reverse=True)
                    and valid_dates[-1] < target.suggested_at
                )
                if len(seen_ids) >= page.count or reached_start:
                    complete = True
                    break
                if valid_dates:
                    previous_date = valid_dates[-1]
                if not posts:
                    break
            evidence["wall_window_complete"] = complete
            evidence["candidate_count"] = len(candidates)
            if len(candidates) > 1:
                evidence["result"] = "ambiguous"
            elif len(candidates) == 1 and complete:
                confirmed = await self._confirm(target, next(iter(candidates)))
                evidence["result"] = "verified" if confirmed else "unverified_candidate"
                if confirmed:
                    evidence["audio_match"] = target.audio in attachments(confirmed, "audio")
                    evidence["marker_present"] = marker_present(confirmed.text)
                await self._finish(target, evidence, post=confirmed)
                return
            else:
                evidence["result"] = "no_match" if complete else "incomplete_window"
            expired = utcnow() >= deadline(target)
            await self._finish(
                target,
                evidence,
                not_found=(expired and complete and not candidates and notifications_complete),
            )
        except VKError as error:
            await self._finish(
                target,
                {
                    **evidence,
                    "result": "read_error",
                    "error_kind": type(error).__name__,
                    "error_code": error.code,
                },
            )

    async def check_submission(self, submission_id: UUID) -> dict[str, object]:
        async with self.sessions() as session:
            row = await session.get(Submission, submission_id)
            if row is None:
                raise NotFoundError("Submission not found")
            previous, account_id = row.status, row.account_id
        if previous == SubmissionStatus.submitted and account_id:
            complete = await self.poll_account(account_id, force=True)
            await self.reconcile(submission_id, notifications_complete=complete)
        async with self.sessions() as session:
            row = await session.get(Submission, submission_id)
            assert row is not None
            evidence = safe_evidence(row.vk_monitor_evidence or {})
            return {
                "submission_id": row.id,
                "previous_status": previous,
                "current_status": row.status,
                "suggestion_state": evidence.get("suggestion_state", "unknown"),
                "notification_match": evidence.get("notification_match", False),
                "published_post_id": row.vk_published_post_id,
                "published_post_url": row.published_post_url,
                "last_checked_at": row.vk_last_checked_at,
                "evidence": evidence,
            }

    async def tick(self) -> None:
        now = utcnow()
        async with self.sessions() as session:
            accounts = (
                await session.scalars(
                    select(Account.id)
                    .outerjoin(VKNotificationCursor)
                    .where(
                        Account.status == AccountStatus.active,
                        Account.encrypted_access_token.is_not(None),
                        or_(
                            VKNotificationCursor.next_poll_at.is_(None),
                            VKNotificationCursor.next_poll_at <= now,
                        ),
                    )
                    .order_by(VKNotificationCursor.next_poll_at.asc().nulls_first(), Account.id)
                    .limit(5)
                )
            ).all()
        for account_id in accounts:
            complete = await self.poll_account(account_id)
            async with self.sessions() as session:
                due = (
                    await session.scalars(
                        select(Submission.id)
                        .where(
                            Submission.account_id == account_id,
                            Submission.status == SubmissionStatus.submitted,
                            or_(
                                Submission.vk_next_check_at.is_(None),
                                Submission.vk_next_check_at <= utcnow(),
                            ),
                        )
                        .order_by(Submission.vk_next_check_at.asc().nulls_first(), Submission.id)
                        .limit(10)
                    )
                ).all()
            for submission_id in due:
                await self.reconcile(submission_id, notifications_complete=complete)
