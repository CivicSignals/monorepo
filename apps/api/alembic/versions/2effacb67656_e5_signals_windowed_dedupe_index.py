"""E5 signals windowed dedupe index

Replaces E4's unique ``signals_dedupe_idx`` on
``(entity_id, signal_type, content_hash)`` with a **non-unique** windowed lookup
index ``signals_dedupe_window_idx`` on
``(entity_id, signal_type, content_hash, occurred_at)`` (doc 19 §7.1-§7.2; E5).

The exact-match dedupe is now windowed (doc 19 §7.2): the same canonical key
recurring *outside* its type-specific window (90d RFP, 365d contracts/budgets,
730d leadership, 60d board agenda) is a genuinely new signal, so a global unique
constraint on the key is wrong — it would forbid e.g. the same annual RFP a year
later. The "one-signal-per-window" guarantee moves into
``signals.dedupe.find_duplicate``'s SELECT-then-merge; this index just makes that
windowed lookup cheap (narrow by the key, range-scan the trailing ``occurred_at``).

Revision ID: 2effacb67656
Revises: 0a3f28b23f5d
Create Date: 2026-05-22 10:45:28.623242
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2effacb67656"
down_revision: str | None = "0a3f28b23f5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop the E4 global-unique dedupe index; the windowed lookup (doc 19 §7.2)
    # replaces the "uniqueness" semantics with a SELECT-then-merge in the service.
    op.drop_index("signals_dedupe_idx", table_name="signals_signal")
    # Non-unique windowed lookup index (doc 19 §7.1): narrow by the canonical key,
    # range-scan the trailing event date for the type-specific window.
    op.create_index(
        "signals_dedupe_window_idx",
        "signals_signal",
        ["entity_id", "signal_type", "content_hash", "occurred_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("signals_dedupe_window_idx", table_name="signals_signal")
    # Restore the E4 unique dedupe index (NULLS NOT DISTINCT so resolution-pending
    # signals with a NULL entity_id still dedupe — doc 19 §4.3, PG15+).
    op.create_index(
        "signals_dedupe_idx",
        "signals_signal",
        ["entity_id", "signal_type", "content_hash"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
