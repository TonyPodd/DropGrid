from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dropgrid.db.session import Database
from dropgrid.integrations.vk.client import VKClient
from dropgrid.integrations.vk.credentials import TokenProvider


def database(request: Request) -> Database:
    db: Database = request.app.state.database
    return db


async def session(db: Annotated[Database, Depends(database)]) -> AsyncIterator[AsyncSession]:
    async with db.sessions() as value, value.begin():
        yield value


Session = Annotated[AsyncSession, Depends(session, scope="function")]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def vk_client(request: Request) -> VKClient:
    client: VKClient = request.app.state.vk_client
    return client


def token_provider(request: Request) -> TokenProvider:
    provider: TokenProvider = request.app.state.token_provider
    return provider


VK = Annotated[VKClient, Depends(vk_client)]
Tokens = Annotated[TokenProvider, Depends(token_provider)]
