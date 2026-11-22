"""
Test suite for Phase 0: CI and migration truthfulness (P0).

Validates:
1. Migration tree has exactly one authoritative head.
2. Migration graph is linear and fully connected (no dangling/orphan revisions).
3. No stale revision literals (such as r0a1b2c3d4e5) are hardcoded in CI or quality gate scripts.
4. Dynamic head resolution matches between ScriptDirectory API and Alembic configuration.
"""

import os
from pathlib import Path
import subprocess
import sys
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from liteads.common.alembic_utils import (
    find_alembic_ini,
    get_authoritative_alembic_heads,
    get_authoritative_alembic_head,
    get_preceding_alembic_revision,
    validate_single_head,
)


def test_single_authoritative_alembic_head():
    """Verify that ScriptDirectory identifies exactly one head revision without hardcoded literals."""
    heads = get_authoritative_alembic_heads()
    assert len(heads) == 1, f"Expected exactly 1 Alembic head, found {len(heads)}: {heads}"

    head = validate_single_head()
    assert head == heads[0]
    assert len(head) >= 8, f"Head revision identifier seems invalid: {head}"

    ini_path = find_alembic_ini()
    config = Config(str(ini_path))
    script = ScriptDirectory.from_config(config)
    assert head == script.get_current_head(), f"Expected head {script.get_current_head()}, got '{head}'"


def test_alembic_migration_graph_linearity():
    """Verify that every revision in alembic/versions is linearly reachable from the head."""
    ini_path = find_alembic_ini()
    config = Config(str(ini_path))
    script = ScriptDirectory.from_config(config)

    head = get_authoritative_alembic_head()
    all_revisions = {rev.revision for rev in script.walk_revisions()}
    
    # Traverse from head downwards
    visited = set()
    curr = script.get_revision(head)
    while curr is not None:
        visited.add(curr.revision)
        if curr.down_revision is None:
            break
        if isinstance(curr.down_revision, tuple):
            assert len(curr.down_revision) <= 1, f"Multiple down-revisions detected at {curr.revision}: {curr.down_revision}"
            down = curr.down_revision[0] if curr.down_revision else None
        else:
            down = curr.down_revision
        curr = script.get_revision(down) if down else None

    # Assert that all revisions in the directory are part of the linear chain
    unreachable = all_revisions - visited
    assert not unreachable, f"Found orphan/unreachable migrations not in head's history: {unreachable}"


def test_no_stale_hardcoded_heads_in_scripts_or_ci():
    """Ensure scripts and CI do not contain stale hardcoded head literals (e.g. r0a1b2c3d4e5)."""
    ini_path = find_alembic_ini()
    repo_root = ini_path.parent.parent

    ci_workflow = repo_root / ".github" / "workflows" / "ci.yml"
    assert ci_workflow.exists(), f"CI workflow not found at {ci_workflow}"
    ci_content = ci_workflow.read_text(encoding="utf-8")
    assert "r0a1b2c3d4e5" not in ci_content, (
        "Found stale revision literal 'r0a1b2c3d4e5' in .github/workflows/ci.yml"
    )

    pg_concurrency = repo_root / "backend" / "scripts" / "verify_postgres_phase11_concurrency.py"
    assert pg_concurrency.exists(), f"Concurrency verification script not found at {pg_concurrency}"
    pg_content = pg_concurrency.read_text(encoding="utf-8")
    assert "EXPECTED_HEAD = \"r0a1b2c3d4e5\"" not in pg_content, (
        "Found stale EXPECTED_HEAD in backend/scripts/verify_postgres_phase11_concurrency.py"
    )
    assert "get_authoritative_alembic_head" in pg_content, (
        "verify_postgres_phase11_concurrency.py must derive expected head dynamically"
    )

    quality_gate = repo_root / "scripts" / "run_quality_gate.py"
    assert quality_gate.exists(), f"Quality gate runner not found at {quality_gate}"
    gate_content = quality_gate.read_text(encoding="utf-8")
    assert "r0a1b2c3d4e5" not in gate_content, (
        "Found stale revision literal 'r0a1b2c3d4e5' in scripts/run_quality_gate.py"
    )


def test_postgres_service_healthcheck_in_ci():
    """Verify that CI postgres service healthcheck specifies user and db flags."""
    ini_path = find_alembic_ini()
    repo_root = ini_path.parent.parent
    ci_workflow = repo_root / ".github" / "workflows" / "ci.yml"
    ci_content = ci_workflow.read_text(encoding="utf-8")

    assert "pg_isready -U liteads -d liteads" in ci_content, (
        "Postgres service in CI workflow must use explicit healthcheck: pg_isready -U liteads -d liteads"
    )


def test_drift_prevention_dynamic_head_matches():
    """Verify that dynamic head matches the actual terminal revision in the migration graph."""
    ini_path = find_alembic_ini()
    config = Config(str(ini_path))
    script = ScriptDirectory.from_config(config)
    head_from_script = script.get_current_head()
    authoritative_head = get_authoritative_alembic_head()
    assert authoritative_head == head_from_script, (
        f"Dynamic head {authoritative_head} does not match script directory head {head_from_script}"
    )


def test_get_authoritative_head_fails_closed_on_multiple_heads(monkeypatch):
    """Verify that get_authoritative_alembic_head fails closed if multiple heads exist."""
    from liteads.common import alembic_utils

    monkeypatch.setattr(
        alembic_utils,
        "get_authoritative_alembic_heads",
        lambda *args, **kwargs: ["rev_head_a", "rev_head_b"],
    )

    with pytest.raises(ValueError, match="Multiple Alembic migration heads detected"):
        alembic_utils.get_authoritative_alembic_head()


def test_get_authoritative_head_fails_closed_on_zero_heads(monkeypatch):
    """Verify that get_authoritative_alembic_head fails closed if zero heads exist."""
    from liteads.common import alembic_utils

    monkeypatch.setattr(
        alembic_utils,
        "get_authoritative_alembic_heads",
        lambda *args, **kwargs: [],
    )

    with pytest.raises(ValueError, match="No Alembic migration heads found"):
        alembic_utils.get_authoritative_alembic_head()


def test_preceding_alembic_revision_resolution():
    """Verify that get_preceding_alembic_revision accurately identifies head-1."""
    ini_path = find_alembic_ini()
    config = Config(str(ini_path))
    script = ScriptDirectory.from_config(config)
    head = get_authoritative_alembic_head(ini_path)
    head_rev = script.get_revision(head)
    expected_parent = (
        head_rev.down_revision[0]
        if isinstance(head_rev.down_revision, tuple) and head_rev.down_revision
        else head_rev.down_revision
    )
    actual_parent = get_preceding_alembic_revision(ini_path)
    assert actual_parent == expected_parent


def test_verify_postgres_fails_closed_by_default():
    """Verify that verify_postgres_phase11_concurrency.py exits non-zero when DB is unreachable."""
    ini_path = find_alembic_ini()
    script_path = ini_path.parent / "scripts" / "verify_postgres_phase11_concurrency.py"

    env = os.environ.copy()
    env.pop("CI", None)
    env.pop("GITHUB_ACTIONS", None)
    env["LITEADS_DATABASE__PORT"] = "59999"
    env["PYTHONPATH"] = str(ini_path.parent)

    res = subprocess.run(
        [sys.executable, str(script_path)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, f"Expected non-zero exit code, got {res.returncode}. Output:\n{res.stdout}\n{res.stderr}"
    assert "FAIL CLOSED" in (res.stdout + res.stderr)


def test_verify_postgres_fails_closed_in_ci_even_with_skip_flag():
    """Verify that verify_postgres_phase11_concurrency.py ignores skip flag and fails closed in CI."""
    ini_path = find_alembic_ini()
    script_path = ini_path.parent / "scripts" / "verify_postgres_phase11_concurrency.py"

    env = os.environ.copy()
    env["CI"] = "true"
    env["LITEADS_DATABASE__PORT"] = "59999"
    env["PYTHONPATH"] = str(ini_path.parent)

    res = subprocess.run(
        [sys.executable, str(script_path), "--allow-skip-if-unavailable"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0, f"Expected non-zero exit code in CI, got {res.returncode}. Output:\n{res.stdout}\n{res.stderr}"
    assert "FAIL CLOSED (CI environment detected)" in (res.stdout + res.stderr)


def test_verify_postgres_allows_skip_in_local_opt_in():
    """Verify that verify_postgres_phase11_concurrency.py allows exit 0 ONLY when explicitly requested outside CI."""
    ini_path = find_alembic_ini()
    script_path = ini_path.parent / "scripts" / "verify_postgres_phase11_concurrency.py"

    env = os.environ.copy()
    env.pop("CI", None)
    env.pop("GITHUB_ACTIONS", None)
    env["LITEADS_DATABASE__PORT"] = "59999"
    env["PYTHONPATH"] = str(ini_path.parent)

    res = subprocess.run(
        [sys.executable, str(script_path), "--allow-skip-if-unavailable"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Expected 0 exit code in local opt-in, got {res.returncode}. Output:\n{res.stdout}\n{res.stderr}"
    assert "SKIPPED (Local non-CI opt-in)" in (res.stdout + res.stderr)


def test_ci_workflow_no_skip_or_continue_on_error():
    """Verify that .github/workflows/ci.yml enforces strict fail-closed execution."""
    ini_path = find_alembic_ini()
    repo_root = ini_path.parent.parent
    ci_workflow = repo_root / ".github" / "workflows" / "ci.yml"
    ci_content = ci_workflow.read_text(encoding="utf-8")

    assert "continue-on-error" not in ci_content, (
        "ci.yml must not use 'continue-on-error' in any job or step"
    )
    assert "--allow-skip-if-unavailable" not in ci_content, (
        "ci.yml must not pass '--allow-skip-if-unavailable' to PostgreSQL verification"
    )
    assert "python backend/scripts/verify_postgres_phase11_concurrency.py" in ci_content, (
        "ci.yml must invoke verify_postgres_phase11_concurrency.py directly without skip flags"
    )




