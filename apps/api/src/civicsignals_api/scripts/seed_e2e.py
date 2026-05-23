"""Seed the e2e routing scenarios by running the REAL extraction pipeline.

The foundation of the CivicSignals e2e test suite. Unlike :mod:`seed_demo` (which
writes throwaway ``dev_seed_*`` rows), this seeder exercises the *production* code
paths end-to-end against the real schema, with **no network and no Celery**:

For each scenario in ``tests/e2e_fixtures/scenarios.yaml``:

1. Upsert the scenario's canonical ``entities_entity`` row (so the produced signal
   links to it and the ICP geo/kind dimensions match).
2. Store the mocked ``source.html`` as a raw document via
   ``ingestion.services.store_raw_document`` against an **in-memory** content store
   (no S3/MinIO).
3. Run ``extraction.pipeline.run_extraction_pipeline`` with a deterministic
   fixture-backed gateway (``LLM_BACKEND=fake`` + the merged ``llm-responses.json``
   script) — fetch → parse → relevance gate → extract → score → dedupe → store →
   embed — producing real ``signals_signal`` rows (mirrors
   ``extraction/tests/test_pipeline_run.py``).

Then it creates the manifest's two users + workspaces + ICPs through the owning
modules' ``services.py`` (accounts / icp — never their internals, doc 06 §3), and
scores **every** produced signal for **every** workspace via
``signals.services.score_signal_for_all_workspaces`` → ``signals_workspace_score``
rows. The result encodes the routing invariant the e2e suite asserts: Alice's
workspace sees the TX RFP (not the CA news), Bob's sees the CA news (not the TX
RFP), and the weak-match signal scores into no workspace.

Idempotent: entities upsert on their external identifier, users/workspaces/ICPs
get-or-create on stable email/slug, and raw-document storage + signal promotion
dedupe on content hash, so a second run produces the same rows (and the same
summary) rather than duplicates. Run it directly:

    uv run python -m civicsignals_api.scripts.seed_e2e

or via the container ``seed-e2e`` process type (``docker-entrypoint.sh``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import structlog
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.config import Settings, get_settings
from civicsignals_api.llm_gateway import LLMGateway, build_fake_gateway
from civicsignals_api.logging import configure_logging
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.entities import services as entities_services
from civicsignals_api.modules.entities.services import IDENTIFIER_COLUMNS
from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.icp import services as icp_services
from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.storage import (
    StoredObject,
    content_hash,
    key_for_hash,
)
from civicsignals_api.modules.signals import services as signals_services

logger = structlog.get_logger(__name__)

# The fixtures live next to the api package's tests dir (apps/api/tests/e2e_fixtures).
_FIXTURES_DIR = Path(__file__).resolve().parents[3] / "tests" / "e2e_fixtures"
_MANIFEST = _FIXTURES_DIR / "scenarios.yaml"


# ---------------------------------------------------------------------------
# In-memory content-addressable storage (no S3/MinIO for the seed)
# ---------------------------------------------------------------------------


class InMemoryRawDocumentStorage:
    """A network-free stand-in for ``ingestion.storage.RawDocumentStorage``.

    Implements just the surface the seed touches: ``put_document_if_absent`` /
    ``put_document`` / ``get_document`` / ``exists``, keyed by the same
    ``sha256/<hash>`` content address so the produced signal's provenance + dedupe
    behave exactly as in production. Bytes live in a process-local dict.
    """

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put_document(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        precomputed_hash: str | None = None,
    ) -> StoredObject:
        hash_hex = precomputed_hash or content_hash(data)
        key = key_for_hash(hash_hex)
        self._objects[key] = data
        return StoredObject(
            key=key, content_hash=hash_hex, size=len(data), content_type=content_type
        )

    def put_document_if_absent(
        self,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
        precomputed_hash: str | None = None,
    ) -> StoredObject:
        hash_hex = precomputed_hash or content_hash(data)
        key = key_for_hash(hash_hex)
        if key not in self._objects:
            self._objects[key] = data
        return StoredObject(
            key=key, content_hash=hash_hex, size=len(data), content_type=content_type
        )

    def get_document(self, key: str) -> bytes:
        return self._objects[key]

    def exists(self, key: str) -> bool:
        return key in self._objects


# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScenarioSpec:
    id: str
    dir: str
    marker: str
    recipe_id: str
    connector: str
    source_url: str
    content_type: str
    entity: dict[str, Any]
    expected_signal_type: str

    @property
    def source_path(self) -> Path:
        return _FIXTURES_DIR / self.dir / "source.html"

    @property
    def responses_path(self) -> Path:
        return _FIXTURES_DIR / self.dir / "llm-responses.json"


@dataclass(slots=True)
class UserSpec:
    key: str
    email: str
    name: str
    password: str
    workspace_name: str
    workspace_slug: str
    icp: dict[str, Any]
    expects_scenarios: list[str]


@dataclass(slots=True)
class Manifest:
    scenarios: list[ScenarioSpec]
    users: list[UserSpec]


def load_manifest(path: Path = _MANIFEST) -> Manifest:
    """Parse ``scenarios.yaml`` into typed specs."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = [
        ScenarioSpec(
            id=s["id"],
            dir=s["dir"],
            marker=s["marker"],
            recipe_id=s["recipe_id"],
            connector=s["connector"],
            source_url=s["source_url"],
            content_type=s["content_type"],
            entity=dict(s["entity"]),
            expected_signal_type=s["expected_signal_type"],
        )
        for s in data["scenarios"]
    ]
    users = [
        UserSpec(
            key=u["key"],
            email=u["email"],
            name=u["name"],
            password=u["password"],
            workspace_name=u["workspace_name"],
            workspace_slug=u["workspace_slug"],
            icp=dict(u["icp"]),
            expects_scenarios=list(u.get("expects_scenarios", [])),
        )
        for u in data["users"]
    ]
    return Manifest(scenarios=scenarios, users=users)


def build_fixture_script(manifest: Manifest) -> dict[str, dict[str, object]]:
    """Merge every scenario's ``llm-responses.json`` into one marker-keyed script.

    The :class:`~civicsignals_api.llm_gateway.backends.fixture.FixtureBackend` keys on
    the scenario marker (present in the document text) + the call kind, so the merged
    mapping is ``{marker: {relevance|entity|signal: response}}``.
    """
    script: dict[str, dict[str, object]] = {}
    for scenario in manifest.scenarios:
        responses = json.loads(scenario.responses_path.read_text(encoding="utf-8"))
        script[scenario.marker] = responses
    return script


# ---------------------------------------------------------------------------
# Per-scenario pipeline run
# ---------------------------------------------------------------------------


def _entity_natural_key(entity: dict[str, Any]) -> str:
    """Pick the external-identifier column present in the scenario's entity spec."""
    for key in IDENTIFIER_COLUMNS:
        if entity.get(key):
            return key
    raise ValueError(f"scenario entity has no natural key from {sorted(IDENTIFIER_COLUMNS)}")


async def _upsert_scenario_entity(session: AsyncSession, scenario: ScenarioSpec) -> uuid.UUID:
    """Idempotently upsert the scenario's canonical entity, returning its id."""
    natural_key = _entity_natural_key(scenario.entity)
    entity = await entities_services.upsert_entity(
        session, natural_key=natural_key, **scenario.entity
    )
    return entity.id


@dataclass(slots=True)
class ScenarioResult:
    scenario_id: str
    signal_ids: list[uuid.UUID]
    signal_types: list[str]
    skipped: bool


async def run_scenario(
    session: AsyncSession,
    storage: InMemoryRawDocumentStorage,
    gateway: LLMGateway,
    scenario: ScenarioSpec,
) -> ScenarioResult:
    """Store the source + run the real pipeline for one scenario (DB-backed)."""
    entity_id = await _upsert_scenario_entity(session, scenario)

    content = scenario.source_path.read_bytes()
    stored = await ingestion_services.store_raw_document(
        session,
        cast(Any, storage),
        content=content,
        recipe_id=scenario.recipe_id,
        connector=scenario.connector,
        source_url=scenario.source_url,
        content_type=scenario.content_type,
        entity_id=entity_id,
    )

    job_id = uuid.uuid4()
    result = await pipeline.run_extraction_pipeline(
        session,
        cast(Any, storage),
        job_id=job_id,
        raw_document_id=stored.id,
        gateway=gateway,
        prefilter="classifier",
    )

    # Map the produced candidates back to their stored signal rows. The pipeline
    # returns the surviving candidates; we read the freshly-stored signals for this
    # recipe to collect their ids (the seed runs one document per recipe).
    signal_ids: list[uuid.UUID] = []
    signal_types: list[str] = []
    if not result.skipped:
        page = await signals_services.list_signals(session, limit=signals_services.MAX_LIMIT)
        for sig in page.items:
            if sig.recipe_id == scenario.recipe_id and sig.id not in signal_ids:
                signal_ids.append(sig.id)
                signal_types.append(sig.signal_type)

    return ScenarioResult(
        scenario_id=scenario.id,
        signal_ids=signal_ids,
        signal_types=signal_types,
        skipped=result.skipped,
    )


# ---------------------------------------------------------------------------
# Users / workspaces / ICPs
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SeededUser:
    key: str
    user_id: uuid.UUID
    workspace_id: uuid.UUID
    email: str


async def upsert_user_workspace_icp(session: AsyncSession, spec: UserSpec) -> SeededUser:
    """Get-or-create the user + workspace + active ICP for one manifest user.

    Idempotent on the user's email and the workspace slug: a re-run reuses the
    existing rows (and refreshes the ICP) rather than duplicating.
    """
    # Lazy import so the auth password-hashing seam is only pulled when seeding users.
    from civicsignals_api.modules.auth.services import hash_password

    user = await accounts_services.get_user_by_email(session, spec.email)
    if user is None:
        user = await accounts_services.create_user(
            session,
            email=spec.email,
            password_hash=hash_password(spec.password),
            name=spec.name,
            email_verified=True,
        )

    # Workspace: reuse by slug if present (the slug is unique across the install).
    workspace = await _get_workspace_by_slug(session, spec.workspace_slug)
    if workspace is None:
        workspace = await accounts_services.create_workspace(
            session,
            owner=user,
            name=spec.workspace_name,
            slug=spec.workspace_slug,
        )

    # ICP: ensure exactly one active ICP matching the spec (idempotent reseed).
    existing_icp = await icp_services.get_active_icp(session, workspace_id=workspace.id)
    if existing_icp is None:
        await icp_services.create_icp(
            session,
            workspace_id=workspace.id,
            name=spec.icp["name"],
            countries=spec.icp.get("countries"),
            states=spec.icp.get("states"),
            entity_kinds=spec.icp.get("entity_kinds"),
            signal_types=spec.icp.get("signal_types"),
            threshold=int(spec.icp.get("threshold", 50)),
            is_active=True,
        )

    return SeededUser(
        key=spec.key,
        user_id=user.id,
        workspace_id=workspace.id,
        email=spec.email,
    )


async def _get_workspace_by_slug(session: AsyncSession, slug: str) -> Any:
    from sqlalchemy import select

    from civicsignals_api.modules.accounts.models import Workspace

    result = await session.execute(select(Workspace).where(Workspace.slug == slug))
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SeedSummary:
    users: list[SeededUser] = field(default_factory=list)
    scenarios: list[ScenarioResult] = field(default_factory=list)
    # workspace_id -> [signal_id, ...] that scored into that workspace's feed.
    workspace_scores: dict[uuid.UUID, list[uuid.UUID]] = field(default_factory=dict)


async def seed_e2e(session: AsyncSession, *, settings: Settings | None = None) -> SeedSummary:
    """Run the full e2e seed against ``session`` (the caller owns commit/rollback).

    Returns a :class:`SeedSummary` so the verifying test can assert the routing
    invariant directly from the seeded ids without re-reading the DB.
    """
    settings = settings or get_settings()
    manifest = load_manifest()
    storage = InMemoryRawDocumentStorage()
    gateway = build_fake_gateway(_fake_settings(settings, manifest))

    summary = SeedSummary()

    # 1. Run every scenario's real pipeline → signals_signal rows.
    for scenario in manifest.scenarios:
        result = await run_scenario(session, storage, gateway, scenario)
        summary.scenarios.append(result)
    await session.flush()

    # 2. Users + workspaces + ICPs.
    for user_spec in manifest.users:
        seeded = await upsert_user_workspace_icp(session, user_spec)
        summary.users.append(seeded)
    await session.flush()

    # 3. Score every produced signal for every workspace (the F3 fan-out).
    all_signal_ids = [sid for sc in summary.scenarios for sid in sc.signal_ids]
    for sid in all_signal_ids:
        await signals_services.score_signal_for_all_workspaces(session, signal_id=sid)
    await session.flush()

    # 4. Read back which signals scored into each workspace (for the summary).
    for seeded in summary.users:
        page = await signals_services.list_workspace_signals(
            session, workspace_id=seeded.workspace_id, limit=signals_services.MAX_LIMIT
        )
        summary.workspace_scores[seeded.workspace_id] = [item.signal.id for item in page.items]

    return summary


def _fake_settings(settings: Settings, manifest: Manifest) -> Settings:
    """Return a settings copy that points the fake gateway at the merged script.

    We serialise the merged fixture script to a temp file and set
    ``llm_fake_fixtures`` so :func:`build_fake_gateway` loads it (the same wiring a
    running api/worker uses). Keeping it on disk — rather than constructing the
    backend by hand — exercises the exact env-driven path the e2e stack boots with.
    """
    import tempfile

    script = build_fixture_script(manifest)
    fh = tempfile.NamedTemporaryFile(  # noqa: SIM115 - kept open for the process lifetime
        mode="w", suffix=".e2e-llm.json", delete=False, encoding="utf-8"
    )
    json.dump(script, fh)
    fh.flush()
    return settings.model_copy(update={"llm_backend": "fake", "llm_fake_fixtures": fh.name})


async def _amain() -> None:
    settings = get_settings()
    # Seeding is a batch of writes; use the direct (non-PgBouncer) URL when available
    # for a stable session (doc 06 §4, doc 18 §6.3).
    db_url = settings.database_direct_url or settings.database_url
    engine = create_async_engine(db_url, echo=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            summary = await seed_e2e(session, settings=settings)
            await session.commit()
        _print_summary(summary)
    finally:
        await engine.dispose()


def _print_summary(summary: SeedSummary) -> None:
    """Print a human-readable seed summary (users, signals, per-workspace scores)."""
    by_ws_email = {u.workspace_id: u.email for u in summary.users}
    logger.info("seed_e2e.done", users=len(summary.users), scenarios=len(summary.scenarios))
    print("\n=== seed_e2e summary ===")
    print(f"users:     {len(summary.users)}")
    for u in summary.users:
        print(f"  - {u.key:6s} {u.email}  workspace={u.workspace_id}")
    total_signals = sum(len(s.signal_ids) for s in summary.scenarios)
    print(f"scenarios: {len(summary.scenarios)}  (signals produced: {total_signals})")
    for s in summary.scenarios:
        types = ",".join(s.signal_types) or "(none)"
        print(
            f"  - {s.scenario_id:16s} signals={len(s.signal_ids)} "
            f"types=[{types}] skipped={s.skipped}"
        )
    print("workspace scores:")
    for ws_id, signal_ids in summary.workspace_scores.items():
        print(f"  - {by_ws_email.get(ws_id, str(ws_id))}: {len(signal_ids)} scored signal(s)")
    print("========================\n")


def main() -> None:
    configure_logging(get_settings().log_level)
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
