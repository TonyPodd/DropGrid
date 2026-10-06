from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from dropgrid.api.dependencies import VK, Limit, Offset, Session, Tokens
from dropgrid.api.schemas import (
    AccountCreate,
    AccountPatch,
    AccountRead,
    AccountValidationRead,
    CampaignCreate,
    CampaignPatch,
    CampaignRead,
    CampaignStats,
    CampaignSummary,
    CommunityRead,
    CommunityResolveInput,
    DashboardRead,
    GridCommunityPage,
    GridCreate,
    GridDetail,
    GridImport,
    GridImportRead,
    GridRead,
    GridSummary,
    GridText,
    PrepareRead,
    SubmissionPage,
    TrackInput,
    TrackRead,
)
from dropgrid.db.models import Account, Campaign, Community, Grid
from dropgrid.domain.enums import SubmissionStatus
from dropgrid.domain.grid_parser import ParseGridResult, parse_grid
from dropgrid.integrations.vk.errors import VKInputError
from dropgrid.integrations.vk.helpers import parse_vk_audio_reference
from dropgrid.services import campaigns, catalog, vk_accounts, workflow
from dropgrid.services.catalog import InvalidGridError

router = APIRouter(prefix="/api/v1")


@router.get("/accounts", response_model=list[AccountRead])
async def accounts(session: Session, limit: Limit = 100, offset: Offset = 0) -> Sequence[Account]:
    return await catalog.list_entities(session, Account, limit, offset)


@router.post("/accounts", response_model=AccountRead, status_code=201)
async def account_create(data: AccountCreate, session: Session) -> Account:
    return await catalog.create_account(session, data)


@router.get("/accounts/{entity_id}", response_model=AccountRead)
async def account_get(entity_id: UUID, session: Session) -> Account:
    return await catalog.get_entity(session, Account, entity_id)


@router.patch("/accounts/{entity_id}", response_model=AccountRead)
async def account_patch(entity_id: UUID, data: AccountPatch, session: Session) -> Account:
    return await catalog.patch_account(session, entity_id, data)


@router.get("/communities", response_model=list[CommunityRead])
async def communities(
    session: Session, limit: Limit = 100, offset: Offset = 0
) -> Sequence[Community]:
    return await catalog.list_entities(session, Community, limit, offset)


@router.get("/communities/{entity_id}", response_model=CommunityRead)
async def community_get(entity_id: UUID, session: Session) -> Community:
    return await catalog.get_entity(session, Community, entity_id)


@router.get("/grids", response_model=list[GridSummary])
async def grids(session: Session, limit: Limit = 100, offset: Offset = 0) -> list[GridSummary]:
    return await workflow.grid_list(session, limit, offset)


@router.post("/grids", response_model=GridRead, status_code=201)
async def grid_create(data: GridCreate, session: Session) -> Grid:
    return await catalog.create_grid(session, data.name)


@router.post("/grids/parse", response_model=ParseGridResult)
async def grid_parse(data: GridText) -> ParseGridResult:
    return parse_grid(data.text)


@router.post("/grids/import", response_model=GridImportRead, status_code=201)
async def grid_import(data: GridImport, session: Session) -> GridImportRead:
    grid, parsed = await catalog.import_grid(session, data)
    return GridImportRead(grid=GridRead.model_validate(grid), preview=parsed)


@router.get("/grids/{entity_id}", response_model=GridDetail)
async def grid_get(
    entity_id: UUID, session: Session, limit: Limit = 100, offset: Offset = 0
) -> GridDetail:
    return await workflow.grid_detail(session, entity_id, limit, offset)


@router.get("/campaigns", response_model=list[CampaignSummary])
async def campaign_list(
    session: Session, limit: Limit = 100, offset: Offset = 0
) -> list[CampaignSummary]:
    return await workflow.campaign_list(session, limit, offset)


@router.post("/campaigns", response_model=CampaignRead, status_code=201)
async def campaign_create(data: CampaignCreate, session: Session) -> Campaign:
    return await campaigns.create_campaign(session, data)


@router.get("/campaigns/{entity_id}", response_model=CampaignSummary)
async def campaign_get(entity_id: UUID, session: Session) -> CampaignSummary:
    await catalog.get_entity(session, Campaign, entity_id)
    return (await workflow.campaign_list(session, 1, 0, entity_id))[0]


@router.patch("/campaigns/{entity_id}", response_model=CampaignRead)
async def campaign_patch(entity_id: UUID, data: CampaignPatch, session: Session) -> Campaign:
    return await campaigns.patch_campaign(session, entity_id, data)


@router.post("/campaigns/{entity_id}/prepare", response_model=PrepareRead)
async def campaign_prepare(entity_id: UUID, session: Session) -> PrepareRead:
    return await campaigns.prepare_campaign(session, entity_id)


@router.post("/accounts/{entity_id}/validate", response_model=AccountValidationRead)
async def account_validate(
    entity_id: UUID, session: Session, client: VK, tokens: Tokens
) -> AccountValidationRead:
    account, error = await vk_accounts.validate_account(session, entity_id, client, tokens)
    return AccountValidationRead(
        account=AccountRead.model_validate(account),
        valid=error is None,
        error=error.as_dict() if error else None,
    )


@router.post("/communities/{entity_id}/resolve", response_model=CommunityRead)
async def community_resolve(
    entity_id: UUID, data: CommunityResolveInput, session: Session, client: VK, tokens: Tokens
) -> Community:
    return await vk_accounts.resolve_community(session, entity_id, data.account_id, client, tokens)


Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=200)]


@router.get("/dashboard", response_model=DashboardRead)
async def dashboard_get(session: Session) -> DashboardRead:
    return await workflow.dashboard(session)


@router.get("/grids/{entity_id}/communities", response_model=GridCommunityPage)
async def grid_members(
    entity_id: UUID, session: Session, page: Page = 1, page_size: PageSize = 25
) -> GridCommunityPage:
    return await workflow.grid_members(session, entity_id, page, page_size)


@router.get("/campaigns/{entity_id}/stats", response_model=CampaignStats)
async def campaign_stats(entity_id: UUID, session: Session) -> CampaignStats:
    return await workflow.campaign_stats(session, entity_id)


@router.get("/campaigns/{entity_id}/submissions", response_model=SubmissionPage)
async def submission_list(
    entity_id: UUID,
    session: Session,
    page: Page = 1,
    page_size: PageSize = 25,
    status: SubmissionStatus | None = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
) -> SubmissionPage:
    return await workflow.submission_list(session, entity_id, page, page_size, status, category)


@router.post("/tracks/parse", response_model=TrackRead)
async def track_parse(data: TrackInput) -> TrackRead:
    try:
        audio = parse_vk_audio_reference(data.track_url)
    except VKInputError:
        raise InvalidGridError(
            "Expected a VK audio link, for example https://vk.ru/audio1_2"
        ) from None
    return TrackRead(owner_id=audio.owner_id, audio_id=audio.media_id)
