"""Public service interface for the accounts module.

Other modules call accounts only through the functions defined here — never by
importing accounts's models or routes directly (doc 06 §3). This is the seam the
``auth`` module uses to create/look up users, and that B5 (workspaces) / B7
(RBAC) will extend.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    """Return the (non-deleted) user with this id, or ``None``."""
    result = await session.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Return the (non-deleted) user with this email (case-insensitive), or ``None``."""
    result = await session.execute(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str | None = None,
    name: str | None = None,
    email_verified: bool = False,
) -> User:
    """Insert a new user. The caller is responsible for hashing the password
    (via ``auth.services.hash_password``) and for handling uniqueness conflicts.
    """
    user = User(
        email=email,
        password_hash=password_hash,
        name=name,
        email_verified_at=datetime.now(UTC) if email_verified else None,
    )
    session.add(user)
    await session.flush()
    return user


async def mark_email_verified(session: AsyncSession, user: User) -> User:
    """Set the user's ``email_verified_at`` to now (idempotent)."""
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        await session.flush()
    return user


async def touch_last_seen(session: AsyncSession, user: User) -> None:
    """Update ``last_seen_at`` to now (called on login / authenticated access)."""
    user.last_seen_at = datetime.now(UTC)
    await session.flush()
