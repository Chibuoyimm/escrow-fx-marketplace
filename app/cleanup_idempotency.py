"""Delete expired mutation idempotency records."""

from app.infrastructure.config import settings
from app.services._shared import build_uow, utc_now


async def cleanup_expired_idempotency_records() -> int:
    """Delete one bounded batch of expired replay records."""
    async with build_uow() as uow:
        deleted = await uow.idempotency_records.delete_expired(
            now=utc_now(),
            limit=settings.idempotency_cleanup_batch_size,
        )
        await uow.commit()
        return deleted


async def _main() -> None:
    deleted = await cleanup_expired_idempotency_records()
    print(f"Deleted {deleted} expired idempotency record(s).")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
