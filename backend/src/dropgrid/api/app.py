import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from asyncpg import PostgresError
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from dropgrid.api.access import ProxyAccess
from dropgrid.api.activity import router as activity_router
from dropgrid.api.dependencies import database
from dropgrid.api.routes import router
from dropgrid.config import Settings
from dropgrid.db.session import Database
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.errors import (
    VKAuthenticationError,
    VKCredentialUnavailableError,
    VKError,
    VKPermissionError,
    VKRateLimitError,
)
from dropgrid.integrations.vk.token_storage import AccountTokenCipher, DBTokenProvider
from dropgrid.logging import configure_logging
from dropgrid.photos.archive import VKArchivePhotoProvider
from dropgrid.photos.conflicts import PhotoConflict
from dropgrid.photos.engine import PhotoEngine
from dropgrid.photos.preparation import MediaPreparation
from dropgrid.photos.preparation_routes import router as preparation_router
from dropgrid.photos.reference_routes import router as reference_router
from dropgrid.photos.references import CommunityReferenceCollector
from dropgrid.photos.routes import router as photo_router
from dropgrid.services.catalog import ConflictError, InvalidGridError, NotFoundError

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging()
        app.state.database = Database(config)
        app.state.vk_client = VKClient(config)
        app.state.token_cipher = AccountTokenCipher(config.app_secret_key)
        app.state.token_provider = DBTokenProvider(
            app.state.database.sessions, app.state.token_cipher
        )
        app.state.photo_engine = PhotoEngine(config, app.state.database)
        photo_engine = app.state.photo_engine
        app.state.reference_collector = CommunityReferenceCollector(
            app.state.database.sessions,
            app.state.vk_client,
            app.state.token_provider,
            photo_engine.reference_downloader,
            photo_engine.reference_storage,
            photo_engine.embedder,
        )
        app.state.media_preparation = MediaPreparation(app.state.reference_collector)
        photo_engine.planner.archive = VKArchivePhotoProvider(app.state.reference_collector)
        try:
            yield
        finally:
            await app.state.photo_engine.aclose()
            await app.state.vk_client.aclose()
            await app.state.database.close()

    app = FastAPI(title="DropGrid", version="0.1.0", lifespan=lifespan)
    app.add_middleware(ProxyAccess, settings=config)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.frontend_origin],
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    from dropgrid.photos.workflow_routes import router as photo_workflow_router

    app.include_router(photo_workflow_router)
    from dropgrid.photos.validation_routes import router as validation_router

    app.include_router(validation_router)
    app.include_router(router)
    app.include_router(activity_router)
    app.include_router(photo_router)
    app.include_router(reference_router)
    app.include_router(preparation_router)

    @app.get("/health")
    async def health(db: Annotated[Database, Depends(database)]) -> JSONResponse:
        try:
            await db.ping()
        except (SQLAlchemyError, PostgresError, OSError, TimeoutError):
            logger.warning("Database health check failed")
            return JSONResponse(
                status_code=503, content={"status": "degraded", "database": "unavailable"}
            )
        return JSONResponse(content={"status": "ok", "database": "ok"})

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Pydantic errors normally echo request input (including rejected credentials).
        errors = [{key: error[key] for key in ("loc", "msg", "type")} for error in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError) -> JSONResponse:
        if isinstance(exc, PhotoConflict):
            return JSONResponse(status_code=409, content={"detail": {"code": exc.code}})
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InvalidGridError)
    async def invalid_grid(request: Request, exc: InvalidGridError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(IntegrityError)
    async def integrity(request: Request, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": "Database constraint conflict"})

    @app.exception_handler(PostgresError)
    @app.exception_handler(OSError)
    @app.exception_handler(TimeoutError)
    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request: Request, exc: Exception) -> JSONResponse:
        logger.warning("Database operation failed")
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @app.exception_handler(VKError)
    async def vk_failure(request: Request, exc: VKError) -> JSONResponse:
        status = 502
        if isinstance(exc, VKAuthenticationError):
            status = 401
        elif isinstance(exc, VKRateLimitError):
            status = 429
        elif isinstance(exc, VKPermissionError):
            status = 403
        elif isinstance(exc, VKCredentialUnavailableError):
            status = 503
        return JSONResponse(
            status_code=status,
            content={
                "detail": {
                    **exc.as_dict(),
                    "kind": type(exc).__name__,
                }
            },
        )

    return app
