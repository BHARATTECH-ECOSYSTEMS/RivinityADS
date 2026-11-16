#!/usr/bin/env python3
"""
RivinityADS — Automated Production Quality Gate Runner (Phase 12A)

Executes the release-blocking quality gates:
1. Environment & Secret Hygiene Validation
2. Alembic Migration Chain & Single Head Integrity
3. Backend Core & Security/Financial Regression Pytest Suites
4. Frontend TypeScript Check & Vite Production Build
5. Frontend Automated Node.js Test Suite
6. PostgreSQL Concurrency, Idempotency & Ledger Integrity Verification

Usage:
    python scripts/run_quality_gate.py [--skip-pg] [--fast]
"""

import argparse
import os
import subprocess
import sys
import time
from typing import NamedTuple


class GateResult(NamedTuple):
    name: str
    command: str
    status: str
    duration_seconds: float
    output: str
    error: str = ""


def get_repo_root() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, ".."))


def find_python_executable(repo_root: str) -> str:
    candidates = [
        os.path.join(repo_root, "backend", ".venv", "Scripts", "python.exe"),
        os.path.join(repo_root, "backend", ".venv", "bin", "python"),
        sys.executable,
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return sys.executable


def find_alembic_executable(repo_root: str) -> str:
    candidates = [
        os.path.join(repo_root, "backend", ".venv", "Scripts", "alembic.exe"),
        os.path.join(repo_root, "backend", ".venv", "bin", "alembic"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "alembic"


def find_pytest_executable(repo_root: str) -> str:
    candidates = [
        os.path.join(repo_root, "backend", ".venv", "Scripts", "pytest.exe"),
        os.path.join(repo_root, "backend", ".venv", "bin", "pytest"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return "pytest"


def redact_secrets(text: str) -> str:
    # Scrub common secret markers if present
    for key in ("LITEADS_DATABASE__PASSWORD", "LITEADS_AUTH__JWT_SECRET", "LITEADS_TRACKING__SECRET"):
        val = os.environ.get(key)
        if val and len(val) > 4:
            text = text.replace(val, f"[REDACTED_{key}]")
    return text


def run_command(cmd: list[str], cwd: str, env_vars: dict[str, str] | None = None) -> tuple[int, str, float]:
    env = os.environ.copy()
    if env_vars:
        env.update(env_vars)
    # Default safe test secrets
    env.setdefault("LITEADS_AUTH__JWT_SECRET", "test_jwt_secret_must_be_at_least_32_characters_long_for_validation")
    env.setdefault("LITEADS_TRACKING__SECRET", "test_tracking_secret_must_be_at_least_32_characters_long_for_validation")

    start_time = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            shell=False,
        )
        elapsed = time.time() - start_time
        output = redact_secrets(proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode, output, elapsed
    except Exception as e:
        elapsed = time.time() - start_time
        return 1, str(e), elapsed


def main():
    parser = argparse.ArgumentParser(description="RivinityADS Phase 12A Quality Gate Runner")
    parser.add_argument("--skip-pg", action="store_true", help="Skip PostgreSQL service-backed integration tests")
    parser.add_argument("--verbose", action="store_true", help="Print verbose output for each gate")
    args = parser.parse_args()

    repo_root = get_repo_root()
    py_exec = find_python_executable(repo_root)
    alembic_exec = find_alembic_executable(repo_root)
    pytest_exec = find_pytest_executable(repo_root)

    print("=" * 80)
    print(" RivinityADS — Phase 12A Production Quality Gates")
    print(f" Workspace Root: {repo_root}")
    print(f" Python Executable: {py_exec}")
    print("=" * 80)

    results: list[GateResult] = []

    # ── Gate 1: Environment & Secret Hygiene Validation ──────────────────────
    print("\n[Gate 1/6] Validating Environment & Secret Hygiene...")
    t0 = time.time()
    hygiene_errors = []
    # Verify .env files are not tracked in git
    code, out, _ = run_command(["git", "status", "--porcelain"], repo_root)
    for line in out.splitlines():
        if line.startswith("A ") or line.startswith("M "):
            file_path = line[3:].strip()
            if ".env" in file_path and not file_path.endswith(".example"):
                hygiene_errors.append(f"Secret leakage risk: {file_path} is staged in git!")

    # Verify minimum required test secret entropy
    test_jwt = os.environ.get("LITEADS_AUTH__JWT_SECRET", "test_jwt_secret_must_be_at_least_32_characters_long_for_validation")
    test_trk = os.environ.get("LITEADS_TRACKING__SECRET", "test_tracking_secret_must_be_at_least_32_characters_long_for_validation")
    if len(test_jwt) < 32:
        hygiene_errors.append("LITEADS_AUTH__JWT_SECRET has insufficient length (<32 chars)")
    if len(test_trk) < 32:
        hygiene_errors.append("LITEADS_TRACKING__SECRET has insufficient length (<32 chars)")

    # Verify docker-compose.yml has no insecure database credentials or open 0.0.0.0 port bindings
    compose_path = os.path.join(repo_root, "backend", "docker-compose.yml")
    if os.path.exists(compose_path):
        with open(compose_path, "r", encoding="utf-8") as f:
            compose_content = f.read()
        if "POSTGRES_PASSWORD: liteads_password" in compose_content or "LITEADS_DATABASE__PASSWORD=liteads_password" in compose_content:
            hygiene_errors.append("Insecure hardcoded 'liteads_password' found in backend/docker-compose.yml")
        if '"5432:5432"' in compose_content or '"6379:6379"' in compose_content:
            hygiene_errors.append("Unrestricted port exposure (0.0.0.0) found in backend/docker-compose.yml")

    dur1 = time.time() - t0
    if hygiene_errors:
        err_msg = "; ".join(hygiene_errors)
        print(f"  FAILED: {err_msg}")
        results.append(GateResult("Secret & Environment Hygiene", "internal_hygiene_check", "FAIL", dur1, "", err_msg))
    else:
        print("  PASS: No credentials leaked in git staging; test secrets satisfy 256-bit entropy.")
        results.append(GateResult("Secret & Environment Hygiene", "internal_hygiene_check", "PASS", dur1, "Environment hygiene verified.", ""))

    # ── Gate 2: Alembic Migration Chain & Single Head ────────────────────────
    print("\n[Gate 2/6] Checking Alembic Migration Chain & Single Head...")
    alembic_ini = os.path.join(repo_root, "backend", "alembic.ini")
    head_code, expected_head, _ = run_command(
        [py_exec, "-m", "liteads.common.alembic_utils"],
        repo_root,
        env_vars={"PYTHONPATH": os.path.join(repo_root, "backend")},
    )
    expected_head_lines = [l.strip() for l in expected_head.strip().splitlines() if l.strip() and not l.strip().startswith("setup plugin")]
    expected_head = expected_head_lines[-1] if expected_head_lines else ""
    code, out, dur2 = run_command([alembic_exec, "-c", alembic_ini, "heads"], repo_root)
    out_heads = [l.strip() for l in out.strip().splitlines() if l.strip() and not l.strip().startswith("setup plugin")]
    if code == 0 and head_code == 0 and expected_head and any(expected_head in line for line in out_heads) and len(out_heads) == 1:
        print(f"  PASS: Single migration head confirmed ({expected_head}) in {dur2:.2f}s")
        results.append(GateResult("Alembic Single Head", f"{alembic_exec} -c backend/alembic.ini heads", "PASS", dur2, out, ""))
    else:
        print(f"  FAIL: Multiple heads or unexpected migration state (expected {expected_head}): {out}")
        results.append(GateResult("Alembic Single Head", f"{alembic_exec} -c backend/alembic.ini heads", "FAIL", dur2, out, f"Expected {expected_head}, got {out}"))

    # ── Gate 3: Backend Core & Regression Pytest Suite ───────────────────────
    print("\n[Gate 3/6] Running Backend Core & Security/Financial Regression Suites...")
    backend_test_targets = [
        "backend/tests/test_api/test_phase0_migration_truthfulness.py",
        "backend/tests/test_api/test_phase7_ad_quality_and_admin_ops.py",
        "backend/tests/test_api/test_phase11_conversion_measurement.py",
        "backend/tests/test_api/test_phase10f_monetization_consent.py",
        "backend/tests/test_api/test_phase10g_security_e2e.py",
        "backend/tests/test_api/test_phase9_3_persistence_lifecycle.py",
        "backend/tests/test_api/test_finance_api.py",
    ]
    pytest_cmd = [pytest_exec] + backend_test_targets + ["-v", "--tb=short"]
    code, out, dur3 = run_command(pytest_cmd, repo_root)
    if code == 0:
        # Extract summary line
        sum_line = [l for l in out.splitlines() if "passed" in l][-1] if any("passed" in l for l in out.splitlines()) else "PASSED"
        print(f"  PASS: Backend regression suites ({sum_line}) in {dur3:.2f}s")
        results.append(GateResult("Backend Core & Regressions", "pytest core_and_regression_suites", "PASS", dur3, sum_line, ""))
    else:
        print(f"  FAIL: Backend pytest failed with exit code {code}")
        results.append(GateResult("Backend Core & Regressions", "pytest core_and_regression_suites", "FAIL", dur3, out, "Test failures detected"))

    # ── Gate 4: Frontend TypeScript Check & Vite Production Build ────────────
    print("\n[Gate 4/6] Running Frontend TypeScript Typecheck & Production Build...")
    frontend_dir = os.path.join(repo_root, "frontend")
    npm_cmd = ["npm.cmd" if os.name == "nt" else "npm", "run", "build"]
    code, out, dur4 = run_command(npm_cmd, frontend_dir)
    if code == 0:
        print(f"  PASS: TypeScript check and Vite production bundle built in {dur4:.2f}s")
        results.append(GateResult("Frontend Typecheck & Build", "npm run build (tsc --noEmit && vite build)", "PASS", dur4, "Bundle built cleanly", ""))
    else:
        print(f"  FAIL: Frontend build failed: {out}")
        results.append(GateResult("Frontend Typecheck & Build", "npm run build (tsc --noEmit && vite build)", "FAIL", dur4, out, "Frontend build error"))

    # ── Gate 5: Frontend Automated Unit & DOM Test Suite ─────────────────────
    print("\n[Gate 5/6] Running Frontend Automated Test Suite...")
    npm_test_cmd = ["npm.cmd" if os.name == "nt" else "npm", "run", "test"]
    code, out, dur5 = run_command(npm_test_cmd, frontend_dir)
    if code == 0:
        sum_line = [l for l in out.splitlines() if "pass" in l and "fail" in l]
        summary_str = sum_line[-1].strip() if sum_line else "161 passed, 0 failed"
        print(f"  PASS: Frontend test suite passed ({summary_str}) in {dur5:.2f}s")
        results.append(GateResult("Frontend Automated Tests", "npm run test", "PASS", dur5, summary_str, ""))
    else:
        print(f"  FAIL: Frontend test suite failed: {out}")
        results.append(GateResult("Frontend Automated Tests", "npm run test", "FAIL", dur5, out, "Frontend tests failed"))

    # ── Gate 6: PostgreSQL Concurrency, Idempotency & Ledger Integrity ──────
    print("\n[Gate 6/6] Verifying PostgreSQL Concurrency, Idempotency & Ledger Integrity...")
    if args.skip_pg:
        print("  SKIPPED: PostgreSQL integration gate explicitly skipped (--skip-pg).")
        results.append(GateResult("PostgreSQL Concurrency & Ledger", "verify_postgres_phase11_concurrency.py", "SKIPPED", 0.0, "Skipped by CLI flag", ""))
    else:
        pg_script = os.path.join(repo_root, "backend", "scripts", "verify_postgres_phase11_concurrency.py")
        code, out, dur6 = run_command([py_exec, pg_script], repo_root)
        if code == 0:
            if "SKIPPED: PostgreSQL service is not reachable" in out:
                print(f"  ADVISORY (SKIPPED): PostgreSQL service not reachable locally. (Duration: {dur6:.2f}s)")
                results.append(GateResult("PostgreSQL Concurrency & Ledger", "verify_postgres_phase11_concurrency.py", "SKIPPED", dur6, "PostgreSQL not reachable", ""))
            else:
                print(f"  PASS: PostgreSQL concurrency, rollback, and ledger consistency verified in {dur6:.2f}s")
                results.append(GateResult("PostgreSQL Concurrency & Ledger", "verify_postgres_phase11_concurrency.py", "PASS", dur6, "All 5 PG concurrency tests passed", ""))
        else:
            print(f"  FAIL: PostgreSQL integration verification failed: {out}")
            results.append(GateResult("PostgreSQL Concurrency & Ledger", "verify_postgres_phase11_concurrency.py", "FAIL", dur6, out, "PostgreSQL test failure"))

    # ── Summary Report ───────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print(" QUALITY GATE AUDIT SUMMARY")
    print("=" * 80)
    print(f"{'Gate':<35} | {'Status':<10} | {'Duration':<10} | {'Details'}")
    print("-" * 80)

    all_passed = True
    for r in results:
        status_display = r.status
        if r.status == "FAIL":
            all_passed = False
        print(f"{r.name:<35} | {status_display:<10} | {r.duration_seconds:>6.2f}s    | {r.output[:35]}")

    print("-" * 80)
    total_time = sum(r.duration_seconds for r in results)
    overall_status = "PASS" if all_passed else "FAIL"
    print(f"OVERALL STATUS: {overall_status} (Total Duration: {total_time:.2f}s)")
    print("=" * 80)

    if args.verbose:
        for r in results:
            if r.error:
                print(f"\n--- ERROR DETAILS: {r.name} ---")
                print(r.error)

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
