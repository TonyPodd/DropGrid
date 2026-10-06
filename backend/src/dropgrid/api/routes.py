from collections.abc import Sequence
from dataclasses import asdict
from uuid import UUID

from fastapi import APIRouter

from dropgrid.api.dependencies import VK, Limit, Offset, Session, Tokens
from dropgrid.api.schemas import (
    AccountCreate,
    AccountPatch,
    AccountRead,
    AccountValidationRead,
    CampaignCreate,
    CampaignPatch,
    CampaignRead,
    CommunityRead,
    CommunityResolveInput,
    GridCreate,
    GridDetail,
    GridImport,
    GridRead,
    GridText,
    PrepareRead,
)
from dropgrid.db.models import Account, Campaign, Community, Grid
from dropgrid.domain.grid_parser import ParseGridResult, parse_grid
from dropgrid.services import campaigns, catalog, vk_accounts

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


@router.get("/grids", response_model=list[GridRead])
async def grids(session: Session, limit: Limit = 100, offset: Offset = 0) -> Sequence[Grid]:
    return await catalog.list_entities(session, Grid, limit, offset)


@router.post("/grids", response_model=GridRead, status_code=201)
async def grid_create(data: GridCreate, session: Session) -> Grid:
    return await catalog.create_grid(session, data.name)


@router.post("/grids/parse")
async def grid_parse(data: GridText) -> ParseGridResult:
    return parse_grid(data.text)


@router.post("/grids/import", status_code=201)
async def grid_import(data: GridImport, session: Session) -> dict[str, object]:
    grid, parsed = await catalog.import_grid(session, data)
    return {"grid": GridRead.model_validate(grid), "preview": asdict(parsed)}


@router.get("/grids/{entity_id}", response_model=GridDetail)
async def grid_get(entity_id: UUID, session: Session) -> GridDetail:
    grid = await catalog.get_entity(session, Grid, entity_id)
    communities = await catalog.grid_communities(session, entity_id)
    return GridDetail(
        **GridRead.model_validate(grid).model_dump(),
        communities=[CommunityRead.model_validate(community) for community in communities],
    )


@router.get("/campaigns", response_model=list[CampaignRead])
async def campaign_list(
    session: Session, limit: Limit = 100, offset: Offset = 0
) -> Sequence[Campaign]:
    return await catalog.list_entities(session, Campaign, limit, offset)


@router.post("/campaigns", response_model=CampaignRead, status_code=201)
async def campaign_create(data: CampaignCreate, session: Session) -> Campaign:
    return await campaigns.create_campaign(session, data)


@router.get("/campaigns/{entity_id}", response_model=CampaignRead)
async def campaign_get(entity_id: UUID, session: Session) -> Campaign:
    return await catalog.get_entity(session, Campaign, entity_id)


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
