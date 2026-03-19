"""Corridor ORM models."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Enum, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.entities import Corridor, CorridorRail
from app.domain.enums import CorridorStatus, FlowType, RailStatus
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CorridorModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Configured corridor row."""

    __tablename__ = "corridors"
    __table_args__ = (
        UniqueConstraint("from_currency_id", "to_currency_id", name="uq_corridors_currency_pair"),
    )

    from_currency_id: Mapped[UUID] = mapped_column(
        ForeignKey("currencies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    to_currency_id: Mapped[UUID] = mapped_column(
        ForeignKey("currencies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[CorridorStatus] = mapped_column(
        Enum(CorridorStatus, native_enum=False, validate_strings=True)
    )
    funding_sla_minutes: Mapped[int] = mapped_column(Integer)
    fee_model_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    rails: Mapped[list[CorridorRailModel]] = relationship(
        back_populates="corridor",
        cascade="all, delete-orphan",
    )

    def to_domain(self) -> Corridor:
        """Convert the ORM row to a domain entity."""
        return Corridor(
            id=self.id,
            from_currency_id=self.from_currency_id,
            to_currency_id=self.to_currency_id,
            status=self.status,
            funding_sla_minutes=self.funding_sla_minutes,
            fee_model_name=self.fee_model_name,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class CorridorRailModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Configured corridor rail row."""

    __tablename__ = "corridor_rails"
    __table_args__ = (
        UniqueConstraint(
            "corridor_id",
            "flow_type",
            "priority_order",
            name="uq_corridor_rails_priority",
        ),
    )

    corridor_id: Mapped[UUID] = mapped_column(
        ForeignKey("corridors.id", ondelete="CASCADE"),
        nullable=False,
    )
    flow_type: Mapped[FlowType] = mapped_column(
        Enum(FlowType, native_enum=False, validate_strings=True)
    )
    priority_order: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(64))
    status: Mapped[RailStatus] = mapped_column(
        Enum(RailStatus, native_enum=False, validate_strings=True)
    )

    corridor: Mapped[CorridorModel] = relationship(back_populates="rails")

    def to_domain(self) -> CorridorRail:
        """Convert the ORM row to a domain entity."""
        return CorridorRail(
            id=self.id,
            corridor_id=self.corridor_id,
            flow_type=self.flow_type,
            priority_order=self.priority_order,
            provider=self.provider,
            method=self.method,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
