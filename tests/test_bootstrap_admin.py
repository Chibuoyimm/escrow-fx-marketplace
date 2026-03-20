from __future__ import annotations

import argparse

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bootstrap_admin import build_parser, run_command
from app.domain.enums import UserRole
from app.domain.exceptions import NotFoundError
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.security import SecurityService
from app.services.auth import AuthService


def build_auth_service(session_factory: async_sessionmaker[AsyncSession]) -> AuthService:
    return AuthService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        security=SecurityService(),
    )


@pytest.mark.anyio
async def test_bootstrap_command_creates_a_new_admin_when_missing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "create-admin",
            "--email",
            "admin@example.com",
            "--password",
            "ChangeMe123!",
            "--country",
            "NG",
        ]
    )

    user = await run_command(args, build_auth_service(session_factory))

    assert user.email == "admin@example.com"
    assert user.role is UserRole.ADMIN


@pytest.mark.anyio
async def test_bootstrap_command_promotes_an_existing_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    auth_service = build_auth_service(session_factory)
    await auth_service.register_user(
        email="existing@example.com",
        password="ChangeMe123!",
        country="NG",
        phone="+2348000000000",
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "promote-admin",
            "--email",
            "existing@example.com",
        ]
    )

    user = await run_command(args, auth_service)

    assert user.email == "existing@example.com"
    assert user.role is UserRole.ADMIN


@pytest.mark.anyio
async def test_create_admin_promotes_existing_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    auth_service = build_auth_service(session_factory)
    await auth_service.register_user(
        email="existing-admin@example.com",
        password="ChangeMe123!",
        country="NG",
        phone="+2348000000000",
    )

    parser = build_parser()
    args = parser.parse_args(
        [
            "create-admin",
            "--email",
            "existing-admin@example.com",
            "--password",
            "Changed123!",
            "--country",
            "NG",
        ]
    )

    user = await run_command(args, auth_service)

    assert user.email == "existing-admin@example.com"
    assert user.role is UserRole.ADMIN


@pytest.mark.anyio
async def test_promote_admin_missing_user_raises_not_found(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parser = build_parser()
    args = parser.parse_args(
        [
            "promote-admin",
            "--email",
            "missing@example.com",
        ]
    )

    with pytest.raises(NotFoundError):
        await run_command(args, build_auth_service(session_factory))


@pytest.mark.anyio
async def test_run_command_rejects_unsupported_command(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    args = argparse.Namespace(
        command="unknown-command",
        email="admin@example.com",
        password="ChangeMe123!",
        country="NG",
        phone=None,
    )

    with pytest.raises(ValueError, match="Unsupported bootstrap command"):
        await run_command(args, build_auth_service(session_factory))
