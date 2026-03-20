"""Bootstrap command for first-admin provisioning."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.domain.entities import User
from app.domain.exceptions import AppError
from app.services.auth import AuthService, get_auth_service


def build_parser() -> argparse.ArgumentParser:
    """Build the bootstrap CLI parser."""
    parser = argparse.ArgumentParser(description="Bootstrap admin users.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-admin", help="Create or promote an admin user.")
    create_parser.add_argument("--email", required=True)
    create_parser.add_argument("--password", required=True)
    create_parser.add_argument("--country", required=True)
    create_parser.add_argument("--phone", default=None)

    promote_parser = subparsers.add_parser("promote-admin", help="Promote an existing user to admin.")
    promote_parser.add_argument("--email", required=True)
    promote_parser.add_argument("--password", default=None)

    return parser


async def run_command(args: argparse.Namespace, auth_service: AuthService) -> User:
    """Execute a bootstrap admin command."""
    if args.command == "create-admin":
        return await auth_service.create_admin(
            email=args.email,
            password=args.password,
            country=args.country,
            phone=args.phone,
        )
    if args.command == "promote-admin":
        return await auth_service.promote_admin(email=args.email, password=args.password)
    raise ValueError(f"Unsupported bootstrap command: {args.command}")


def main(argv: Sequence[str] | None = None, auth_service: AuthService | None = None) -> int:
    """Run the bootstrap admin CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    service = auth_service or get_auth_service()

    try:
        user = asyncio.run(run_command(args, service))
    except AppError as exc:
        print(exc.detail, file=sys.stderr)
        return 1

    print(f"Admin ready: {user.email} ({user.role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
