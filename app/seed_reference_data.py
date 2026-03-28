"""Seed local reference data for currencies and corridors."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.domain.entities import Corridor, CorridorRail, Currency
from app.domain.enums import CorridorStatus, CurrencyStatus, FlowType, RailStatus
from app.domain.exceptions import NotFoundError
from app.infrastructure.database.session import AsyncSessionFactory
from app.infrastructure.database.unit_of_work import AbstractUnitOfWork, SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class CurrencySeed:
    """Currency seed definition."""

    code: str
    minor_unit: int
    min_amount: Decimal
    max_amount: Decimal


@dataclass(frozen=True, slots=True)
class CorridorRailSeed:
    """Corridor rail seed definition."""

    flow_type: FlowType
    priority_order: int
    provider: str
    method: str


@dataclass(frozen=True, slots=True)
class CorridorSeed:
    """Corridor seed definition."""

    from_currency_code: str
    to_currency_code: str
    funding_sla_minutes: int
    fee_model_name: str | None
    rails: tuple[CorridorRailSeed, ...]


@dataclass(frozen=True, slots=True)
class SeedResult:
    """Reference-data seed summary."""

    created_currencies: int
    created_corridors: int
    created_rails: int


CURRENCY_SEEDS: tuple[CurrencySeed, ...] = (
    CurrencySeed(
        code="GBP", minor_unit=2, min_amount=Decimal("1.00"), max_amount=Decimal("500000.00")
    ),
    CurrencySeed(
        code="NGN", minor_unit=2, min_amount=Decimal("1000.00"), max_amount=Decimal("500000000.00")
    ),
    CurrencySeed(
        code="USD", minor_unit=2, min_amount=Decimal("1.00"), max_amount=Decimal("500000.00")
    ),
)

CORRIDOR_SEEDS: tuple[CorridorSeed, ...] = (
    CorridorSeed(
        from_currency_code="USD",
        to_currency_code="NGN",
        funding_sla_minutes=30,
        fee_model_name="default",
        rails=(
            CorridorRailSeed(
                flow_type=FlowType.FUNDING,
                priority_order=1,
                provider="stripe",
                method="card",
            ),
            CorridorRailSeed(
                flow_type=FlowType.PAYOUT,
                priority_order=1,
                provider="paystack",
                method="bank_transfer",
            ),
        ),
    ),
    CorridorSeed(
        from_currency_code="NGN",
        to_currency_code="USD",
        funding_sla_minutes=45,
        fee_model_name="default",
        rails=(
            CorridorRailSeed(
                flow_type=FlowType.FUNDING,
                priority_order=1,
                provider="paystack",
                method="bank_transfer",
            ),
            CorridorRailSeed(
                flow_type=FlowType.PAYOUT,
                priority_order=1,
                provider="wise",
                method="wire",
            ),
        ),
    ),
)


def build_uow() -> AbstractUnitOfWork:
    """Build the default unit of work for seeding."""
    return SqlAlchemyUnitOfWork(AsyncSessionFactory)


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


async def seed_reference_data(
    uow_factory: Callable[[], AbstractUnitOfWork] | None = None,
) -> SeedResult:
    """Seed local currencies, corridors, and rails in an idempotent way."""
    factory = uow_factory or build_uow
    created_currencies = 0
    created_corridors = 0
    created_rails = 0

    async with factory() as uow:
        for currency_seed in CURRENCY_SEEDS:
            try:
                await uow.currencies.get_by_code(currency_seed.code)
            except NotFoundError:
                await uow.currencies.add(
                    Currency(
                        id=uuid4(),
                        code=currency_seed.code,
                        minor_unit=currency_seed.minor_unit,
                        status=CurrencyStatus.ACTIVE,
                        min_amount=currency_seed.min_amount,
                        max_amount=currency_seed.max_amount,
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                )
                created_currencies += 1

        for corridor_seed in CORRIDOR_SEEDS:
            from_currency = await uow.currencies.get_by_code(corridor_seed.from_currency_code)
            to_currency = await uow.currencies.get_by_code(corridor_seed.to_currency_code)

            try:
                corridor = await uow.corridors.get_by_currency_pair(
                    from_currency.id, to_currency.id
                )
            except NotFoundError:
                corridor = await uow.corridors.add(
                    Corridor(
                        id=uuid4(),
                        from_currency_id=from_currency.id,
                        to_currency_id=to_currency.id,
                        status=CorridorStatus.ACTIVE,
                        funding_sla_minutes=corridor_seed.funding_sla_minutes,
                        fee_model_name=corridor_seed.fee_model_name,
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                )
                created_corridors += 1

            existing_rails = await uow.corridor_rails.list_for_corridor(corridor.id)
            existing_keys = {
                (rail.flow_type, rail.priority_order, rail.provider, rail.method)
                for rail in existing_rails
            }

            for rail_seed in corridor_seed.rails:
                rail_key = (
                    rail_seed.flow_type,
                    rail_seed.priority_order,
                    rail_seed.provider,
                    rail_seed.method,
                )
                if rail_key in existing_keys:
                    continue

                await uow.corridor_rails.add(
                    CorridorRail(
                        id=uuid4(),
                        corridor_id=corridor.id,
                        flow_type=rail_seed.flow_type,
                        priority_order=rail_seed.priority_order,
                        provider=rail_seed.provider,
                        method=rail_seed.method,
                        status=RailStatus.ACTIVE,
                        created_at=utc_now(),
                        updated_at=utc_now(),
                    )
                )
                created_rails += 1

        await uow.commit()

    return SeedResult(
        created_currencies=created_currencies,
        created_corridors=created_corridors,
        created_rails=created_rails,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the seed command parser."""
    return argparse.ArgumentParser(description="Seed reference data for local development.")


async def main() -> None:
    """Run the reference-data seed command."""
    build_parser().parse_args()
    result = await seed_reference_data()
    print(
        "Reference data seed complete: "
        f"{result.created_currencies} currencies, "
        f"{result.created_corridors} corridors, "
        f"{result.created_rails} rails created."
    )


if __name__ == "__main__":
    asyncio.run(main())
