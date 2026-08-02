from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import IdempotencyRecordStatus
from app.domain.exceptions import IdempotencyConflictError, IdempotencyInProgressError
from app.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.security import SecurityService
from app.models.idempotency_record import IdempotencyRecordModel
from tests.conftest import build_user

pytestmark = pytest.mark.anyio


async def test_idempotency_repository_replays_conflicts_and_cleans_expired_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    security = SecurityService()
    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        user = await uow.users.add(
            build_user(
                email="idempotency-repository@example.com",
                password_hash=security.hash_password("ChangeMe123!"),
            )
        )
        now = datetime.now(UTC)
        record = await uow.idempotency_records.claim(
            principal_user_id=user.id,
            operation_scope="exchange-request.create",
            key_hash="a" * 64,
            request_fingerprint="b" * 64,
            now=now,
        )
        await uow.idempotency_records.complete(
            record_id=record.id,
            response_status_code=201,
            response_body={"id": "request-1"},
            now=now,
        )
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        replay = await uow.idempotency_records.claim(
            principal_user_id=user.id,
            operation_scope="exchange-request.create",
            key_hash="a" * 64,
            request_fingerprint="b" * 64,
            now=now,
        )
        assert replay.status is IdempotencyRecordStatus.COMPLETED
        assert replay.response_status_code == 201
        assert replay.response_body == {"id": "request-1"}

    with pytest.raises(IdempotencyConflictError):
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await uow.idempotency_records.claim(
                principal_user_id=user.id,
                operation_scope="exchange-request.create",
                key_hash="a" * 64,
                request_fingerprint="c" * 64,
                now=now,
            )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        processing = await uow.idempotency_records.claim(
            principal_user_id=user.id,
            operation_scope="exchange-request.relist:request-1",
            key_hash="d" * 64,
            request_fingerprint="e" * 64,
            now=now,
        )
        await uow.commit()
    assert processing.status is IdempotencyRecordStatus.PROCESSING

    with pytest.raises(IdempotencyInProgressError):
        async with SqlAlchemyUnitOfWork(session_factory) as uow:
            await uow.idempotency_records.claim(
                principal_user_id=user.id,
                operation_scope="exchange-request.relist:request-1",
                key_hash="d" * 64,
                request_fingerprint="e" * 64,
                now=now,
            )

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.session is not None
        model = await uow.session.get(IdempotencyRecordModel, processing.id)
        assert model is not None
        model.expires_at = now - timedelta(seconds=1)
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        reclaimed = await uow.idempotency_records.claim(
            principal_user_id=user.id,
            operation_scope="exchange-request.relist:request-1",
            key_hash="d" * 64,
            request_fingerprint="e" * 64,
            now=now + timedelta(seconds=2),
        )
        assert reclaimed.id != processing.id
        assert reclaimed.status is IdempotencyRecordStatus.PROCESSING
        await uow.idempotency_records.complete(
            record_id=reclaimed.id,
            response_status_code=201,
            response_body={"id": "request-2"},
            now=now + timedelta(seconds=2),
        )
        await uow.commit()

    async with SqlAlchemyUnitOfWork(session_factory) as uow:
        assert uow.session is not None
        model = await uow.session.get(IdempotencyRecordModel, reclaimed.id)
        assert model is not None
        model.expires_at = now - timedelta(seconds=1)
        deleted = await uow.idempotency_records.delete_expired(now=now, limit=10)
        await uow.commit()
    assert deleted == 1
