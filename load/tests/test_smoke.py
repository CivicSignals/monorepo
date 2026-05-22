"""Smoke tests for the Locust load test suite (QA-3).

These tests verify:
  1. NFR target data structures (no locust import needed).
  2. Auth helper interface (no locust import needed).
  3. Scenario source files exist and are syntactically valid Python.
  4. A subprocess import check confirms each locustfile is importable by locust.

No real load is generated. The R3 task triggers the real Locust runs.

Gevent compatibility note
--------------------------
Locust monkey-patches ssl via gevent at module import time. pytest imports
urllib3/anyio before that patch can run, causing a RecursionError on Python
3.12 (gevent/issues/1016). To avoid this we do NOT directly import locust in
this file. Instead, struct checks cover locust-independent code, and the
subprocess test calls ``python -c "import load.locustfile"`` in a fresh
interpreter where gevent patches ssl first.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import uuid
from pathlib import Path

# Root of the monorepo worktree (two levels up from this file: load/tests/ → root)
_REPO_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# 1. NFR targets — pure dataclass checks (no locust)
# ---------------------------------------------------------------------------


def test_nfr_targets_all_present() -> None:
    from load.nfr_targets import ALL_TARGETS

    assert set(ALL_TARGETS.keys()) == {"L1", "L2", "L3", "L4", "L5"}


def test_nfr_target_l1_values() -> None:
    _check_target("L1")


def test_nfr_target_l2_values() -> None:
    _check_target("L2")


def test_nfr_target_l3_values() -> None:
    _check_target("L3")


def test_nfr_target_l4_values() -> None:
    _check_target("L4")


def test_nfr_target_l5_values() -> None:
    _check_target("L5")


def _check_target(scenario_id: str) -> None:
    from load.nfr_targets import ALL_TARGETS

    t = ALL_TARGETS[scenario_id]
    assert t.target_rps > 0, f"{scenario_id}: target_rps must be positive"
    assert 0 < t.max_error_rate < 1, f"{scenario_id}: max_error_rate must be in (0, 1)"
    assert t.endpoint_targets, f"{scenario_id}: endpoint_targets must not be empty"
    for name, lat in t.endpoint_targets.items():
        assert lat.p50_ms > 0, f"{scenario_id}/{name}: p50_ms must be positive"
        assert lat.p95_ms >= lat.p50_ms, f"{scenario_id}/{name}: p95 must be >= p50"


# ---------------------------------------------------------------------------
# 2. Auth helper interface — no locust import
# ---------------------------------------------------------------------------


def test_auth_helper_exports() -> None:
    from load.auth_helper import AuthContext, get_or_create_auth, random_idempotency_key

    assert callable(get_or_create_auth)
    assert callable(random_idempotency_key)
    ctx = AuthContext(
        access_token="tok_test",
        workspace_id="ws-test",
        user_id="user-test",
        email="test@example.com",
    )
    assert "Bearer tok_test" in ctx.headers["Authorization"]
    assert ctx.headers["X-Workspace-Id"] == "ws-test"


def test_random_idempotency_key_is_uuid() -> None:
    from load.auth_helper import random_idempotency_key

    key = random_idempotency_key()
    parsed = uuid.UUID(key)  # raises ValueError if invalid
    assert str(parsed) == key


# ---------------------------------------------------------------------------
# 3. Scenario source files — syntax validation via ast.parse
# ---------------------------------------------------------------------------

_SCENARIO_FILES = [
    "load/l1_steady_state.py",
    "load/l2_digest_spike.py",
    "load/l3_ingestion_load.py",
    "load/l4_smart_search_spike.py",
    "load/l5_public_crawl.py",
    "load/locustfile.py",
    "load/auth_helper.py",
    "load/nfr_targets.py",
]


def test_scenario_files_exist() -> None:
    for rel in _SCENARIO_FILES:
        p = _REPO_ROOT / rel
        assert p.exists(), f"Missing: {p}"


def test_scenario_files_valid_syntax() -> None:
    for rel in _SCENARIO_FILES:
        p = _REPO_ROOT / rel
        source = p.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(p))
        except SyntaxError as exc:
            raise AssertionError(f"Syntax error in {rel}: {exc}") from exc


def test_scenario_target_present_in_each_file() -> None:
    """Each scenario module exposes SCENARIO_TARGET at module level (AST check)."""
    scenario_files = _SCENARIO_FILES[:5]  # l1–l5 only
    for rel in scenario_files:
        p = _REPO_ROOT / rel
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        names = {
            node.targets[0].id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
        }
        assert "SCENARIO_TARGET" in names, f"{rel}: missing SCENARIO_TARGET assignment"


def test_task_decorators_present_in_l1() -> None:
    """@task decorators are present in l1_steady_state.py (AST check)."""
    p = _REPO_ROOT / "load/l1_steady_state.py"
    tree = ast.parse(p.read_text(encoding="utf-8"))
    task_decorated = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "task")
            or (isinstance(d, ast.Name) and d.id == "task")
            for d in node.decorator_list
        )
    ]
    assert len(task_decorated) >= 5, (
        f"Expected ≥5 @task-decorated methods in L1, found {task_decorated}"
    )


# ---------------------------------------------------------------------------
# 4. Subprocess import check — locust + gevent in a fresh interpreter
# ---------------------------------------------------------------------------


def _python_with_locust() -> str:
    """Return path to the python binary in the api venv."""
    venv_python = _REPO_ROOT / "apps/api/.venv/bin/python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def test_locustfile_importable_subprocess() -> None:
    """Run 'python -c import load.locustfile' in a fresh subprocess.

    In a fresh interpreter gevent patches ssl before urllib3 loads, so there is
    no RecursionError. This is the definitive check that the locustfile works.
    """
    result = subprocess.run(
        [
            _python_with_locust(),
            "-c",
            "import sys; sys.path.insert(0, '.'); from load.locustfile import ALL_TARGETS; "
            "assert len(ALL_TARGETS) == 5, f'Expected 5 targets, got {len(ALL_TARGETS)}'; "
            "print('locustfile import OK')",
        ],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, (
        f"locustfile import failed (rc={result.returncode}):\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "locustfile import OK" in result.stdout


def test_l1_scenario_importable_subprocess() -> None:
    """Confirm L1 user class is an HttpUser subclass in a fresh interpreter."""
    result = subprocess.run(
        [
            _python_with_locust(),
            "-c",
            "import sys; sys.path.insert(0, '.'); "
            "from load.l1_steady_state import SteadyStateMixUser; "
            "from locust import HttpUser; "
            "assert issubclass(SteadyStateMixUser, HttpUser); "
            "print('L1 OK')",
        ],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=60,
    )
    assert result.returncode == 0, (
        f"L1 import failed (rc={result.returncode}):\n"
        f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )
    assert "L1 OK" in result.stdout


def test_nfr_module_importable_no_locust() -> None:
    """nfr_targets.py must be importable without locust (pure dataclasses)."""
    # This is tested in-process (no gevent conflict).
    mod = importlib.import_module("load.nfr_targets")
    assert hasattr(mod, "ALL_TARGETS")
    assert hasattr(mod, "L1_TARGET")
