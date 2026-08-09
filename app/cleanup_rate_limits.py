"""Delete expired persistent API rate-limit buckets."""

from app.infrastructure.config import settings
from app.services._shared import build_uow, utc_now


async def cleanup_expired_rate_limit_buckets() -> int:
    """Delete one bounded batch of expired rate-limit counters."""
    async with build_uow() as uow:
        deleted = await uow.rate_limits.delete_expired(
            now=utc_now(),
            limit=settings.rate_limit_cleanup_batch_size,
        )
        await uow.commit()
        return deleted


async def _main() -> None:
    deleted = await cleanup_expired_rate_limit_buckets()
    print(f"Deleted {deleted} expired rate-limit bucket(s).")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
