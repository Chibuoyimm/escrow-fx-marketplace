"""CLI entry point for reconciling pending KYC checks."""

from __future__ import annotations

import argparse

from app.infrastructure.config import settings
from app.infrastructure.jobs import run_cli, run_observed_job
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


async def run(limit: int, service: KycService | None = None) -> None:
    """Run one KYC reconciliation pass."""
    kyc_service = service or KycService()
    completed = await run_observed_job(
        "reconcile_kyc",
        lambda: kyc_service.reconcile_pending(limit=limit),
    )
    print(f"KYC reconciliation complete: {completed} verifications completed.")


def main() -> None:
    """Run the KYC reconciliation command."""
    args = parse_args()
    run_cli(run(args.limit))


if __name__ == "__main__":
    main()
