"""
Prompt 6 (`P1-04`, `P1-05`, `P2-02`) Verification Tests:
CI Coverage Inventory, Workflow Integrity, Action SHA Pinning & Security Automation.
"""

from pathlib import Path
import re
import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW_PATH = WORKFLOWS_DIR / "ci.yml"
CODEQL_WORKFLOW_PATH = WORKFLOWS_DIR / "codeql.yml"
SECURITY_SBOM_WORKFLOW_PATH = WORKFLOWS_DIR / "security-scan-and-sbom.yml"
DEPENDABOT_PATH = REPO_ROOT / ".github" / "dependabot.yml"
INVENTORY_DOC_PATH = REPO_ROOT / "docs" / "testing" / "ci_test_inventory.md"
BACKEND_TESTS_DIR = REPO_ROOT / "backend" / "tests"
FRONTEND_E2E_DIR = REPO_ROOT / "frontend" / "e2e"

REQUIRED_CI_JOB_NAMES = {
    "Backend Quality & Regression Gates",
    "Frontend Typecheck, Build & Unit Tests",
    "PostgreSQL Concurrency & Ledger Integration",
    "Playwright Real-Browser E2E Security Quality Gate",
}


def _load_yaml(path: Path) -> dict:
    assert path.exists(), f"Expected YAML file does not exist: {path}"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"Failed to parse YAML mapping from {path}"
    return data


def _extract_triggers(workflow_data: dict) -> dict:
    # PyYAML parses unquoted `on:` as boolean True unless quoted.
    triggers = workflow_data.get("on")
    if triggers is None:
        triggers = workflow_data.get(True)
    assert isinstance(triggers, dict), "Workflow missing `on:` trigger mapping"
    return triggers


def test_all_github_workflows_and_dependabot_parse_cleanly():
    """All GitHub Actions workflows and Dependabot configuration must parse cleanly."""
    for path in (
        CI_WORKFLOW_PATH,
        CODEQL_WORKFLOW_PATH,
        SECURITY_SBOM_WORKFLOW_PATH,
        DEPENDABOT_PATH,
    ):
        _load_yaml(path)


def test_ci_test_inventory_covers_every_backend_and_e2e_file():
    """Every `.py` under `backend/tests/` and `.spec.ts` under `frontend/e2e/` must be documented in `ci_test_inventory.md`."""
    assert INVENTORY_DOC_PATH.exists(), f"Missing inventory document: {INVENTORY_DOC_PATH}"
    inv_text = INVENTORY_DOC_PATH.read_text(encoding="utf-8")

    # 1. Verify zero omissions across backend/tests and frontend/e2e
    backend_files = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in BACKEND_TESTS_DIR.rglob("*.py")
    )
    e2e_files = sorted(
        p.relative_to(REPO_ROOT).as_posix()
        for p in FRONTEND_E2E_DIR.glob("*.spec.ts")
    )
    assert len(backend_files) >= 140
    assert len(e2e_files) >= 3

    missing = [rel for rel in (*backend_files, *e2e_files) if rel not in inv_text]
    assert not missing, f"Files missing from ci_test_inventory.md: {missing}"

    # 2. Extract section 2 (`unit-regression-ci`) and section 5 (`external-or-manual`)
    sec2_match = re.search(
        r"## 2\. `unit-regression-ci`.*?(?=## 3\.)", inv_text, flags=re.DOTALL
    )
    sec5_match = re.search(
        r"## 5\. `external-or-manual`.*?(?=## 6\.)", inv_text, flags=re.DOTALL
    )
    assert sec2_match is not None, "Missing Section 2 in ci_test_inventory.md"
    assert sec5_match is not None, "Missing Section 5 in ci_test_inventory.md"

    unit_ci_paths = re.findall(r"`(backend/tests/[^`]+test_[^`]+\.py)`", sec2_match.group(0))
    external_paths = re.findall(
        r"^\|\s*`(backend/tests/[^`]+test_[^`]+\.py)`\s*\|",
        sec5_match.group(0),
        flags=re.MULTILINE,
    )
    assert len(external_paths) == 37, f"Expected 37 external-or-manual backend files, found {len(external_paths)}"

    assert len(unit_ci_paths) >= 100, f"Expected expanded CI suite >=100, found {len(unit_ci_paths)}"
    overlap = set(unit_ci_paths) & set(external_paths)
    assert not overlap, f"Paths listed in both unit-regression-ci and external-or-manual: {overlap}"

    # 3. Verify every unit-regression-ci test file is executed inside `.github/workflows/ci.yml`
    ci_text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    not_in_ci = [p for p in unit_ci_paths if p not in ci_text]
    assert not not_in_ci, f"unit-regression-ci tests missing from ci.yml: {not_in_ci}"


def test_ci_workflow_preserves_required_job_names_and_expansions():
    """ci.yml must preserve the 4 required status-check job names and include full Prompt 6 checks."""
    ci_data = _load_yaml(CI_WORKFLOW_PATH)
    jobs = ci_data.get("jobs", {})
    actual_job_names = {cfg.get("name") for cfg in jobs.values() if isinstance(cfg, dict)}
    assert REQUIRED_CI_JOB_NAMES.issubset(actual_job_names), (
        f"Missing required job names: {REQUIRED_CI_JOB_NAMES - actual_job_names}"
    )

    ci_text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    backend_job = jobs["backend-quality"]
    backend_steps_text = "\n".join(str(step.get("run", "")) for step in backend_job["steps"])
    assert "--cov=liteads" in backend_steps_text
    assert "validate_alert_rules.py" in backend_steps_text
    assert "promtool" in backend_steps_text
    assert "amtool" in backend_steps_text
    assert "docker compose" in backend_steps_text
    for expected_suite in (
        "backend/tests/test_scripts/test_backup_restore.py",
        "backend/tests/test_scripts/test_docs_and_compose_consistency.py",
        "backend/tests/test_scripts/test_monitoring_and_alerts.py",
        "backend/tests/test_scripts/test_ci_and_security_automation.py",
        "backend/tests/test_ad_server/test_prompt2_optimization_and_laya_contract.py",
    ):
        assert expected_suite in backend_steps_text, f"Missing regression suite in backend-quality: {expected_suite}"

    pg_job = jobs["postgres-integration"]
    pg_steps_text = "\n".join(str(step.get("run", "")) for step in pg_job["steps"])
    assert "postgresql-client" in pg_steps_text
    assert "verify_postgres_phase11_concurrency.py" in pg_steps_text
    assert "test_phase5_postgres_concurrency.py" in pg_steps_text
    assert "test_backup_restore.py" in pg_steps_text
    assert "TEST_DATABASE_URL" in str(pg_job.get("env", {})) or "TEST_DATABASE_URL" in ci_text


def test_dependabot_config_covers_required_ecosystems():
    """Dependabot must monitor github-actions (`/`), pip (`/backend`), and npm (`/frontend`)."""
    data = _load_yaml(DEPENDABOT_PATH)
    assert data.get("version") == 2
    updates = data.get("updates", [])
    pairs = {(u.get("package-ecosystem"), u.get("directory")) for u in updates}
    assert ("github-actions", "/") in pairs
    assert ("pip", "/backend") in pairs
    assert ("npm", "/frontend") in pairs


def test_codeql_workflow_covers_python_and_javascript():
    """CodeQL SAST workflow must scan both python and javascript-typescript on schedule & PRs."""
    data = _load_yaml(CODEQL_WORKFLOW_PATH)
    triggers = _extract_triggers(data)
    assert "push" in triggers and "pull_request" in triggers and "schedule" in triggers

    raw = CODEQL_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "python" in raw
    assert "javascript-typescript" in raw


def test_security_scan_and_sbom_workflow_configured_properly():
    """Security workflow must run Trivy FS/image scans and generate commit-SHA CycloneDX SBOMs."""
    _load_yaml(SECURITY_SBOM_WORKFLOW_PATH)
    raw = SECURITY_SBOM_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "aquasecurity/trivy-action" in raw
    assert "anchore/sbom-action" in raw
    assert "${{ github.sha }}" in raw
    assert "cyclonedx-json" in raw
    assert "CRITICAL,HIGH" in raw
    assert "upload-sarif" in raw
    assert "upload-artifact" in raw


def test_all_workflow_actions_pinned_to_40_char_hex_sha():
    """Every `uses:` action in `.github/workflows/*.yml` must be pinned to an immutable 40-char commit SHA."""
    sha_pattern = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
    for wf_path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        data = _load_yaml(wf_path)
        for job_id, job_cfg in data.get("jobs", {}).items():
            for idx, step in enumerate(job_cfg.get("steps", [])):
                if "uses" in step:
                    uses_val = str(step["uses"]).strip()
                    assert sha_pattern.match(uses_val), (
                        f"{wf_path.name} job '{job_id}' step {idx} uses unpinned action '{uses_val}'"
                    )
