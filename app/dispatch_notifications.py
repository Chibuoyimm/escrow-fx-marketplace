"""Dispatch pending notification outbox events."""

from __future__ import annotations

import argparse

from app.infrastructure.jobs import run_cli, run_observed_job
from app.services.notification_dispatcher import NotificationDispatchService


def build_parser() -> argparse.ArgumentParser:
    """Build the notification dispatch command parser."""
    parser = argparse.ArgumentParser(description="Dispatch pending notification events.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum events to dispatch.")
    return parser


async def run_command(
    *,
    limit: int | None = None,
    service: NotificationDispatchService | None = None,
) -> None:
    """Run the notification dispatch command."""
    dispatch_service = service or NotificationDispatchService()
    result = await run_observed_job(
        "dispatch_notifications",
        lambda: dispatch_service.dispatch_due(limit=limit),
    )
    print(
        "Notification dispatch complete: "
        f"{result.claimed} events claimed, "
        f"{result.delivered} delivered, "
        f"{result.failed} failed, {result.stale} stale finalizers."
    )


async def main() -> None:
    """Run the notification dispatch command."""
    args = build_parser().parse_args()
    await run_command(limit=args.limit)


if __name__ == "__main__":
    run_cli(main())
