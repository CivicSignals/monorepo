"""notifications SQLAlchemy models.

Tables are prefixed ``notifications_`` and are migrated only by this module
(doc 06 §3, §4). Importing ``Base`` keeps Alembic autogenerate aware of the
module even before there are concrete tables.

H3 adds :class:`DigestSubscription`: a per-(saved-search, user) digest schedule.
The notifications module owns delivery, so it owns the schedule table too — the
``searches`` module owns the saved-search definition, the digest config hangs off
it here. The FK to ``searches_saved_search`` is ``ON DELETE CASCADE`` so deleting
a saved search cleans up its digest subscriptions automatically (the seam the H1
``delete_saved_search`` TODO pointed at).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base

from .digest import DigestFrequency


class DigestSubscription(Base):
    """A per-(saved-search, user) digest schedule (H3; doc 14 §8).

    One row per recipient per saved search. ``frequency`` is ``off``/``daily``/
    ``weekly``; ``send_hour`` (0..23) and ``weekday`` (0=Mon..6=Sun, weekly only)
    say *when*, evaluated in the recipient's ``timezone``. ``last_sent_period`` /
    ``last_sent_at`` provide the distributed-safe dedupe (see ``digest.py``): the
    dispatch sweep only enqueues a subscription whose current local period differs
    from ``last_sent_period``, so a double-fired hourly beat never double-sends.

    # TODO H5: an unsubscribe / preferences surface flips ``frequency`` to ``off``
    #   (and a one-click unsubscribe token lands here) rather than deleting the row,
    #   so the recipient's last choice is remembered.
    """

    __tablename__ = "notifications_digest_subscription"
    __table_args__ = (
        # One digest config per (saved search, recipient).
        UniqueConstraint(
            "saved_search_id",
            "user_id",
            name="uq_notifications_digest_subscription_search_user",
        ),
        # The dispatch sweep scans active (non-off) subscriptions; a partial index
        # keeps that scan cheap as the table grows (most rows are off/idle).
        Index(
            "ix_notifications_digest_subscription_active",
            "frequency",
            postgresql_where=text("frequency <> 'off'"),
        ),
        Index("ix_notifications_digest_subscription_workspace_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The saved search this digest re-runs (H1). CASCADE so deleting the search
    # removes its digest subscriptions (the H1 delete_saved_search seam).
    saved_search_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("searches_saved_search.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Denormalized workspace for cheap tenant-scoped reads + the dispatch payload.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The recipient. RESTRICT mirrors the saved-search owner FK (audit safety).
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="RESTRICT"),
        nullable=False,
    )

    frequency: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'off'"))
    # 0..23 local hour to send at (validated in the schema layer).
    send_hour: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"))
    # 0=Mon..6=Sun; only meaningful for weekly. Default Monday.
    weekday: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Recipient timezone (IANA name). Defaults to UTC; an unknown name degrades to
    # UTC at send time (see digest.resolve_tz).
    # TODO: surface a per-user timezone preference (accounts_user has no tz column
    #   yet) and seed this from it; until then the user sets it per subscription.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))

    # Distributed-safe dedupe: the local period (daily day / ISO week) the last
    # digest belonged to, plus its wall-clock time for observability.
    last_sent_period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def frequency_enum(self) -> DigestFrequency:
        """The ``frequency`` column as the :class:`DigestFrequency` enum."""
        return DigestFrequency(self.frequency)
