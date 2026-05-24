"""CLI entry point for reconciling pending KYC checks."""

from __future__ import annotations

import argparse
import asyncio

from app.infrastructure.config import settings
from app.services.kyc import KycService


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Reconcile pending KYC verifications.")
    parser.add_argument(
        "--limit",
        type=int,
        default=settings.kyc_reconciliation_batch_size,
        help="Maximum pending verifications to reconcile.",
    )
    return parser.parse_args()


async def run(limit: int) -> None:
    """Run one KYC reconciliation pass."""
    completed = await KycService().reconcile_pending(limit=limit)
    print(f"KYC reconciliation complete: {completed} verifications completed.")


def main() -> None:
    """Run the KYC reconciliation command."""
    args = parse_args()
    asyncio.run(run(args.limit))


if __name__ == "__main__":
    main()
