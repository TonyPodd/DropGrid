"""Restart-safe search metadata cache, coalescing leases and provider rate gate.

No transaction remains open while waiting or making provider HTTP requests.
"""

import asyncio
import logging
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dropgrid.db.models import PhotoProviderState, PhotoSearchCache, utcnow
from dropgrid.photos.domain import PhotoCandidate, PhotoError, PhotoProvider, PhotoSearch

logger = logging.getLogger(__name__)


class SearchCache:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        provider: PhotoProvider,
        hours: int = 24,
        namespace: str | None = None,
    ) -> None:
        if hours < 24:
            raise ValueError("Search cache must retain responses for at least 24 hours")
        self.sessions, self.provider, self.hours = sessions, provider, hours
        self.namespace = namespace or provider.name

    async def _rate_slot(self) -> None:
        now = utcnow()
        async with self.sessions() as session, session.begin():
            await session.execute(
                insert(PhotoProviderState)
                .values(provider=self.provider.name, next_request_at=now, interval_seconds=1)
                .on_conflict_do_nothing()
            )
            state = await session.scalar(
                select(PhotoProviderState)
                .where(PhotoProviderState.provider == self.provider.name)
                .with_for_update()
            )
            assert state is not None
            if state.blocked_until and state.blocked_until > now:
                raise PhotoError(
                    "provider_rate_limited", (state.blocked_until - now).total_seconds()
                )
            delay = max(0, (state.next_request_at - now).total_seconds())
            if delay > 10:
                raise PhotoError("provider_rate_limited", delay)
            state.next_request_at = now + timedelta(seconds=delay + state.interval_seconds)
        await asyncio.sleep(delay)

    async def search(self, search: PhotoSearch) -> tuple[tuple[PhotoCandidate, ...], bool]:
        key = search.cache_key(self.namespace)
        token = uuid4()
        deadline = time.monotonic() + 65
        while True:
            now = utcnow()
            async with self.sessions() as session, session.begin():
                # Expiration is opportunistic and bounded by retained live leases.
                await session.execute(
                    delete(PhotoSearchCache).where(
                        PhotoSearchCache.expires_at < now - timedelta(days=7),
                        (PhotoSearchCache.lease_until.is_(None))
                        | (PhotoSearchCache.lease_until < now),
                    )
                )
                await session.execute(
                    insert(PhotoSearchCache).values(cache_key=key).on_conflict_do_nothing()
                )
                row = await session.scalar(
                    select(PhotoSearchCache)
                    .where(PhotoSearchCache.cache_key == key)
                    .with_for_update()
                )
                assert row is not None
                if row.expires_at and row.expires_at > now and row.candidates is not None:
                    logger.info("photo.query.cache_hit", extra={"provider": self.provider.name})
                    return tuple(
                        PhotoCandidate.model_validate(item) for item in row.candidates
                    ), True
                if not row.lease_until or row.lease_until < now:
                    row.lease_token, row.lease_until = token, now + timedelta(seconds=60)
                    break
            if time.monotonic() > deadline:
                raise PhotoError("provider_busy")
            await asyncio.sleep(0.1)
        try:
            await self._rate_slot()
            logger.info("photo.provider.search", extra={"provider": self.provider.name})
            result = await self.provider.search(search)
            async with self.sessions() as session, session.begin():
                await session.execute(
                    update(PhotoSearchCache)
                    .where(PhotoSearchCache.cache_key == key, PhotoSearchCache.lease_token == token)
                    .values(
                        candidates=[item.model_dump(mode="json") for item in result.candidates],
                        expires_at=utcnow() + timedelta(hours=self.hours),
                        lease_token=None,
                        lease_until=None,
                    )
                )
                state = await session.get(
                    PhotoProviderState, self.provider.name, with_for_update=True
                )
                assert state is not None
                if result.rate_limit:
                    state.interval_seconds = max(1, 60 / result.rate_limit)
                if result.remaining == 0:
                    state.blocked_until = utcnow() + timedelta(seconds=min(result.reset, 3600))
            return result.candidates, False
        except PhotoError as exc:
            if exc.code == "provider_rate_limited":
                logger.info("photo.provider.rate_limited", extra={"provider": self.provider.name})
                async with self.sessions() as session, session.begin():
                    await session.execute(
                        update(PhotoProviderState)
                        .where(PhotoProviderState.provider == self.provider.name)
                        .values(blocked_until=utcnow() + timedelta(seconds=exc.retry_after or 60))
                    )
            raise
        finally:
            async with self.sessions() as session, session.begin():
                await session.execute(
                    update(PhotoSearchCache)
                    .where(PhotoSearchCache.cache_key == key, PhotoSearchCache.lease_token == token)
                    .values(
                        lease_token=None,
                        lease_until=None,
                        expires_at=func.coalesce(PhotoSearchCache.expires_at, utcnow()),
                    )
                )
