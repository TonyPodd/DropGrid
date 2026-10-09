"""Read-only normalized activity view; never starts worker operations."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.api.dependencies import Session
from dropgrid.db.models import (
    Campaign,
    Community,
    MediaPreparationJob,
    PhotoOperationJob,
    ReferenceSyncJob,
    Submission,
    utcnow,
)

router = APIRouter(prefix="/api/v1")
STAGES = {
    "queued": "В очереди",
    "reading_wall": "Читаем стену",
    "downloading": "Скачиваем фото",
    "embedding": "Строим визуальные признаки",
    "finalizing": "Классифицируем и сохраняем",
    "archive_seek": "Ищем границы архива",
    "archive_scan": "Индексируем посты",
    "pixabay_search": "Ищем фото в Pixabay",
    "materializing": "Подготавливаем изображения",
    "own_archive": "Подбираем собственный архив",
    "pinterest_search": "Ищем публичные Pins",
    "pinterest_materializing": "Скачиваем и изучаем Pins",
    "category_materializing": "Готовим выбранные фото других групп",
    "category_shortlist": "Сравниваем фото других сообществ",
    "ranking": "Ранжируем по стилю",
    "running": "Выполняется",
    "ready": "Готово",
    "failed": "Не завершено",
    "sending": "Отправка",
    "submitted": "Ожидаем публикацию",
    "published": "Публикация найдена",
    "transient": "Ожидает следующей попытки",
    "pending": "В очереди",
}
LABELS = {
    "reference": "Изучение постов",
    "archive": "Индексация архива",
    "preview": "Сравнение фото",
    "media": "Подготовка медиа",
    "sending": "Отправка кампании",
    "monitoring": "Проверка публикации",
}


class ActivityRead(BaseModel):
    id: str
    kind: str
    label: str
    state: Literal["queued", "running", "success", "warning", "failed"]
    stage: str
    current: int = 0
    total: int | None = None
    percent: float | None = None
    started_at: datetime
    updated_at: datetime
    elapsed_seconds: int
    target_url: str
    message: str | None = None
    counters: dict[str, int] = Field(default_factory=dict)


async def list_activity(session: AsyncSession) -> list[ActivityRead]:
    result: list[ActivityRead] = []
    for model, kind in (
        (ReferenceSyncJob, "reference"),
        (MediaPreparationJob, "media"),
        (PhotoOperationJob, "photo"),
    ):
        rows = (
            await session.execute(
                select(model, Community.name, Community.domain)
                .join(Community, model.community_id == Community.id)
                .where(
                    model.state.not_in(("ready", "failed", "transient"))
                    | model.id.in_(select(model.id).order_by(model.updated_at.desc()).limit(100))
                )
                .order_by(model.updated_at.desc())
            )
        ).all()
        for row, name, domain in rows:
            actual = row.kind if isinstance(row, PhotoOperationJob) else kind
            stage = row.stage if isinstance(row, PhotoOperationJob) else row.state
            counters: dict[str, int] = (
                row.progress
                if isinstance(row, ReferenceSyncJob)
                else row.counters
                if isinstance(row, PhotoOperationJob)
                else {}
            )
            prefix = (
                "downloads"
                if stage == "downloading"
                else "embeddings"
                if stage == "embedding"
                else None
            )
            current = (
                row.current
                if isinstance(row, PhotoOperationJob)
                else counters.get(f"{prefix}_done", 0)
                if prefix
                else counters.get("posts_scanned", 0)
            )
            total = (
                row.total
                if isinstance(row, PhotoOperationJob)
                else counters.get(f"{prefix}_total")
                if prefix
                else None
            )
            state: Literal["queued", "running", "success", "warning", "failed"] = (
                "failed"
                if row.state == "failed" or row.error_code
                else "queued"
                if row.state == "queued"
                else "warning"
                if row.state == "transient"
                else "running"
                if row.state != "ready"
                else "warning"
                if row.result and row.result.get("warnings")
                else "success"
            )
            start = getattr(row, "started_at", None) or row.created_at
            finish = getattr(row, "finished_at", None)
            result.append(
                ActivityRead(
                    id=f"{actual}:{row.id}",
                    kind=actual,
                    label=f"{LABELS[actual]} · {name or domain}",
                    state=state,
                    stage=STAGES.get(stage, "Выполняется"),
                    current=current,
                    total=total,
                    percent=min(100, max(0, current * 100 / total)) if total else None,
                    counters=counters,
                    started_at=start,
                    updated_at=row.updated_at,
                    elapsed_seconds=max(0, int(((finish or utcnow()) - start).total_seconds())),
                    target_url=f"/communities/{row.community_id}",
                    message="Операция не завершена. Проверьте доступ и повторите вручную."
                    if state == "failed"
                    else "Завершено с предупреждениями. Откройте результат."
                    if state == "warning"
                    else None,
                )
            )
    submission_rows = (
        await session.execute(
            select(Submission, Campaign.name)
            .join(Campaign)
            .where(
                Submission.vk_send_started_at.is_not(None)
                | Submission.vk_last_checked_at.is_not(None)
            )
            .where(
                (Submission.status.in_(("pending", "sending")))
                | (Submission.vk_monitor_lease_until > utcnow())
                | Submission.id.in_(
                    select(Submission.id).order_by(Submission.updated_at.desc()).limit(100)
                )
            )
            .order_by(Submission.updated_at.desc())
        )
    ).all()
    for row, name in submission_rows:
        for kind, start, running, finish in (
            ("sending", row.vk_send_started_at, row.status.value == "sending", row.submitted_at),
            (
                "monitoring",
                row.vk_last_checked_at,
                bool(row.vk_monitor_lease_until and row.vk_monitor_lease_until > utcnow()),
                row.vk_last_checked_at,
            ),
        ):
            if start is None:
                continue
            state = (
                "running"
                if running
                else "queued"
                if kind == "sending" and row.status.value == "pending"
                else "failed"
                if row.status.value == "failed"
                else "success"
            )
            result.append(
                ActivityRead(
                    id=f"{kind}:{row.id}",
                    kind=kind,
                    label=f"{LABELS[kind]} · {name}",
                    state=state,
                    stage=STAGES.get(row.status.value, "Завершено"),
                    started_at=start,
                    updated_at=row.updated_at,
                    elapsed_seconds=max(
                        0,
                        int(
                            (
                                (utcnow() if running else finish or row.updated_at) - start
                            ).total_seconds()
                        ),
                    ),
                    target_url=f"/campaigns/{row.campaign_id}",
                )
            )
    ordered = sorted(
        result,
        key=lambda item: (
            item.state not in ("running", "queued"),
            -item.updated_at.timestamp(),
            item.id,
        ),
    )
    return [item for item in ordered if item.state in ("running", "queued")] + [
        item for item in ordered if item.state not in ("running", "queued")
    ][:100]


@router.get("/activity", response_model=list[ActivityRead])
async def get_activity(session: Session) -> list[ActivityRead]:
    return await list_activity(session)
