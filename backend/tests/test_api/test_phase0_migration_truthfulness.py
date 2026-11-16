"""
Test suite for Phase 0: CI and migration truthfulness (P0).

Validates:
1. Migration tree has exactly one authoritative head.
2. Migration graph is linear and fully connected (no dangling/orphan revisions).
3. No stale revision literals (such as r0a1b2c3d4e5) are hardcoded in CI or quality gate scripts.
4. Dynamic head resolution matches between ScriptDirectory API and Alembic configuration.
"""

from pathlib import Path
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from liteads.common.alembic_utils import (
    find_alembic_ini,
    get_authoritative_alembic_heads,
    get_authoritative_alembic_head,
    validate_single_head,
)


def test_single_authoritative_alembic_head():
    """Verify that ScriptDirectory identifies exactly one head revision."""
    heads = get_authoritative_alembic_heads()
    assert len(heads) == 1, f"Expected exactly 1 Alembic head, found {len(heads)}: {heads}"
    
    head = validate_single_head()
    assert head == heads[0]
    assert len(head) >= 8, f"Head revision identifier seems invalid: {head}"
    assert head == "x3y4z5a6b7c8", f"Expected current head 'x3y4z5a6b7c8', got '{head}'"


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


