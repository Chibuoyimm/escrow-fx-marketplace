"""Expire due marketplace records."""

from __future__ import annotations

import argparse
import asyncio

from app.services.marketplace_expiry import MarketplaceExpiryService


def build_parser() -> argparse.ArgumentParser:
    """Build the expiry command parser."""
    return argparse.ArgumentParser(description="Expire due marketplace records.")


async def run_command(service: MarketplaceExpiryService | None = None) -> None:
    """Run the marketplace expiry command."""
    expiry_service = service or MarketplaceExpiryService()
    result = await expiry_service.expire_due_items()
    print(
        "Marketplace expiry complete: "
        f"{result.expired_requests} requests expired, "
        f"{result.expired_offers} offers expired, "
        f"{result.reopened_requests} requests reopened, "
        f"{result.cancelled_trades} trades cancelled."
    )


async def main() -> None:
    """Run the marketplace expiry command."""
    build_parser().parse_args()
    await run_command()


if __name__ == "__main__":
    asyncio.run(main())
