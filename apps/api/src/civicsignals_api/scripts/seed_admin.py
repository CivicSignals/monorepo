"""Seed an initial admin user and workspace for self-hosted deployments (TODO O2).

Invoked by the `init` process type in docker-entrypoint.sh, which runs
Alembic migrations first then calls this script.

The admin user (B1) and workspace (B5) models are implemented, so this script
verifies the DB connection, ensures the pgvector extension, creates the admin
user, and gives them a default organization + workspace + owner membership
(recorded in ``accounts_user.last_active_workspace_id`` so first-run requests
scope to it).

Environment variables read:
  CIVICSIGNALS_ADMIN_EMAIL    — admin account email  (required for user creation)
  CIVICSIGNALS_ADMIN_PASSWORD — admin account password (required for user creation)
  CIVICSIGNALS_WORKSPACE_NAME — default workspace name (optional; "My Workspace")
  DATABASE_DIRECT_URL         — direct Postgres URL (bypasses PgBouncer)

SPDX-License-Identifier: AGPL-3.0-only
"""

from __future__ import annotations

import os
import sys


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        print(f"seed_admin: WARNING: {name} is not set — skipping admin user creation.", flush=True)
    return val


def main() -> None:
    """Entry point called by the `init` process type."""
    import asyncio

    asyncio.run(_async_main())


async def _async_main() -> None:
    # -------------------------------------------------------------------------
    # Step 1: Verify DB connectivity and enable pgvector.
    # We use the *direct* URL (bypasses PgBouncer) because this is a long-running
    # setup operation — the same pattern as Alembic (doc 06 §4).
    # -------------------------------------------------------------------------
    direct_url = os.environ.get("DATABASE_DIRECT_URL", "")
    if not direct_url:
        print("seed_admin: DATABASE_DIRECT_URL not set; cannot connect to Postgres.", flush=True)
        sys.exit(1)

    try:
        import sqlalchemy
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(direct_url, echo=False)
        async with engine.connect() as conn:
            await conn.execute(sqlalchemy.text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.commit()
            result = await conn.execute(sqlalchemy.text("SELECT version()"))
            row = result.fetchone()
            pg_version = row[0] if row else "unknown"
        await engine.dispose()
        print(f"seed_admin: Postgres connection OK — {pg_version}", flush=True)
        print("seed_admin: pgvector extension enabled.", flush=True)
    except Exception as exc:
        print(f"seed_admin: ERROR connecting to Postgres: {exc}", flush=True)
        sys.exit(1)

    # -------------------------------------------------------------------------
    # Step 2: Create the initial admin user (B1 — DONE) and their default
    # workspace + owner membership (B5 — DONE).
    # -------------------------------------------------------------------------
    admin_email = _require_env("CIVICSIGNALS_ADMIN_EMAIL")
    admin_password = _require_env("CIVICSIGNALS_ADMIN_PASSWORD")

    if not admin_email or not admin_password:
        print(
            "seed_admin: Skipping admin user creation (env vars not set). "
            "Set CIVICSIGNALS_ADMIN_EMAIL and CIVICSIGNALS_ADMIN_PASSWORD and re-run "
            "`docker compose run --rm init`.",
            flush=True,
        )
        return

    # Create the admin via the accounts/auth services (never their internals,
    # doc 06 §3). The operator's own account is created already-verified.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from civicsignals_api.modules.accounts import services as accounts_services
    from civicsignals_api.modules.auth import services as auth_services

    workspace_name = os.environ.get("CIVICSIGNALS_WORKSPACE_NAME", "My Workspace")

    engine = create_async_engine(direct_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        admin = await accounts_services.get_user_by_email(session, admin_email)
        if admin is not None:
            print(f"seed_admin: admin user '{admin_email}' already exists — skipping.", flush=True)
        else:
            admin = await accounts_services.create_user(
                session,
                email=admin_email,
                password_hash=auth_services.hash_password(admin_password),
                name="Administrator",
                email_verified=True,
            )
            await session.commit()
            print(f"seed_admin: created admin user '{admin_email}'.", flush=True)

        # B5 — DONE: give the admin an organization + workspace + owner membership
        # via accounts.services (never their internals, doc 06 §3). Idempotent:
        # only create one if the admin is not already a member of any workspace,
        # then set it as their last_active_workspace_id so header-less scoped
        # requests resolve to it (doc 08 §1.4).
        existing_workspaces = await accounts_services.list_workspaces_for_user(session, admin.id)
        if existing_workspaces.items:
            # Ensure last_active_workspace_id is populated even on the skip path:
            # a pre-B5 admin (or one with manually-created memberships) may have
            # NULL here, which would 400 header-less requests. Backfill it.
            if admin.last_active_workspace_id is None:
                await accounts_services.set_last_active_workspace(
                    session, user=admin, workspace_id=existing_workspaces.items[0].id
                )
                await session.commit()
            print("seed_admin: admin already has a workspace — skipping creation.", flush=True)
        else:
            workspace = await accounts_services.create_workspace(
                session, owner=admin, name=workspace_name
            )
            await accounts_services.set_last_active_workspace(
                session, user=admin, workspace_id=workspace.id
            )
            await session.commit()
            print(
                f"seed_admin: created workspace '{workspace.name}' (slug '{workspace.slug}').",
                flush=True,
            )
    await engine.dispose()

    print("seed_admin: init complete.", flush=True)


if __name__ == "__main__":
    main()
