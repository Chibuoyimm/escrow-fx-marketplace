"""User ORM model."""

from __future__ import annotations

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.entities import User
from app.domain.enums import KycStatus, RiskLevel, UserRole, UserStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Platform user row."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country: Mapped[str] = mapped_column(String(2))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole, native_enum=False, validate_strings=True))
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False, validate_strings=True)
    )
    kyc_status: Mapped[KycStatus] = mapped_column(
        Enum(KycStatus, native_enum=False, validate_strings=True)
    )
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, validate_strings=True)
    )

    def to_domain(self) -> User:
        """Convert the ORM row to a domain entity."""
        return User(
            id=self.id,
            email=self.email,
            phone=self.phone,
            country=self.country,
            role=self.role,
            status=self.status,
            kyc_status=self.kyc_status,
            risk_level=self.risk_level,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

