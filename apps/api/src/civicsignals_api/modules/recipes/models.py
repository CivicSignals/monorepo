"""recipes SQLAlchemy models.

Tables are prefixed ``recipes_`` and are migrated only by this module
(doc 06 §3, §4). Importing ``Base`` keeps Alembic autogenerate aware of this
module even before it had concrete tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base


class DeadLetter(Base):
    """A field/document that no extraction step could resolve (doc 18 §2.3, §3.4).

    The ordered fallback chain (primary → fallback → LLM-assisted) reached its
    end without a value, so the runner records the miss here rather than dropping
    it silently. The raw document lives in S3 (doc 18 §3.6), so once the recipe
    is fixed the extraction can be replayed against the snapshot identified by
    ``content_hash``. E7 (drift detection) reads these to decide when a recipe is
    degrading enough to auto-pause.
    """

    __tablename__ = "recipes_dead_letter"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    # SHA-256 of the raw document body — the key to replay against the S3 snapshot.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    # The ordered selector list we tried, for the human fixing the recipe.
    tried_selectors: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
