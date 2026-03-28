from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.models.corridor import CorridorModel, CorridorRailModel
from app.models.currency import CurrencyModel
from app.seed_reference_data import seed_reference_data

pytestmark = pytest.mark.anyio


async def test_seed_reference_data_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    def factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    first = await seed_reference_data(factory)
    second = await seed_reference_data(factory)

    currencies = (await session.execute(select(CurrencyModel))).scalars().all()
    corridors = (await session.execute(select(CorridorModel))).scalars().all()
    rails = (await session.execute(select(CorridorRailModel))).scalars().all()

    assert first.created_currencies == 3
    assert first.created_corridors == 2
    assert first.created_rails == 4
    assert second.created_currencies == 0
    assert second.created_corridors == 0
    assert second.created_rails == 0
    assert len(currencies) == 3
    assert len(corridors) == 2
    assert len(rails) == 4
