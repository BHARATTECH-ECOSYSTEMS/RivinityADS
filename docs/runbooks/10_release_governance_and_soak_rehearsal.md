# Runbook 10: Release Governance, Branch Protection & Soak Rehearsal

## Objective
Standard operating procedure for release governance, mandatory GitHub repository branch protection gates, vulnerability triage protocols, staging soak rehearsal, and pre-launch production sign-off.

---

## 1. Branch Protection & Quality Gates Policy

All production code merges into `main` require strict continuous integration passing status and peer review. No direct pushes or force pushes are permitted.

### 1.1 Verified Mandatory CI Status Check Names
The following **4 exact status check names** (emitted by `.github/workflows/ci.yml` and verified against live GitHub check runs for workflow `RivinityADS CI Quality Gates`) must be marked as required in GitHub repository settings:
1. `Backend Quality & Regression Gates` (17-suite multi-phase backend pytest + Alembic single-head validation)
2. `Frontend Typecheck, Build & Unit Tests` (TypeScript `tsc --noEmit`, Vite production bundle, 33-suite automated Node test runner)
3. `PostgreSQL Concurrency & Ledger Integration` (PostgreSQL 15 transactional concurrency, idempotency, and zero-drift verification)
4. `Playwright Real-Browser E2E Security Quality Gate` (Chromium browser-level security, consent gate, and 3-role journey assertions)

### 1.2 GitHub Repository Ruleset Configuration (Recommended Modern Path)
In GitHub Repository: `Settings` -> `Rules` -> `Rulesets` -> `New branch ruleset`:

- **Ruleset Name:** `main-release-governance`
- **Enforcement status:** `Active`
- **Bypass list:** Empty by default (no routine admin bypass). Emergency break-glass access requires documented approval in the engineering incident channel.
- **Target branches:**
  - Select: `Include default branch` (targets `main`).

**Rules to Enable:**
1. **Restrict deletions:** Checked (prevents accidental branch deletion).
2. **Block force pushes:** Checked (prevents history rewriting).
3. **Require a pull request before merging:** Checked.
   - **Required approvals:** Minimum `1` authorized engineering lead.
   - **Dismiss stale pull request approvals when new commits are pushed:** Checked.
   - **Require review from Code Owners:** Checked (activates once `.github/CODEOWNERS` is created with verified GitHub handle `@[OWNER_GITHUB_HANDLE]`).
   - **Require conversation resolution before merging:** Checked.
4. **Require status checks to pass:** Checked.
   - **Require branches to be up to date before merging:** Checked (`strict: true`).
   - **Status checks that are required:** Add all 4 exact verified check names:
     - `Backend Quality & Regression Gates`
     - `Frontend Typecheck, Build & Unit Tests`
     - `PostgreSQL Concurrency & Ledger Integration`
     - `Playwright Real-Browser E2E Security Quality Gate`
5. **Recommended Additional Rules (enable once all contributors have signing configured):**
   - **Require signed commits:** Optional / Recommended (cryptographic GPG/SSH commit signature verification).
   - **Require linear history:** Optional / Recommended (Squash and merge or Rebase and merge; prevent merge bubbles).

### 1.3 Classic Branch Protection Rules (Alternative Path)
If using classic branch protection (`Settings` -> `Branches` -> `Add branch protection rule` for `main`):
1. **Branch name pattern:** `main`
2. **Require a pull request before merging:**
   - Require approvals: `1`
   - Dismiss stale pull request approvals when new commits are pushed: Checked
   - Require review from Code Owners: Checked
3. **Require status checks to pass before merging:**
   - Require branches to be up to date before merging: Checked
   - Status checks to search and select:
     - `Backend Quality & Regression Gates`
     - `Frontend Typecheck, Build & Unit Tests`
     - `PostgreSQL Concurrency & Ledger Integration`
     - `Playwright Real-Browser E2E Security Quality Gate`
4. **Require conversation resolution before merging:** Checked
5. **Do not allow bypassing the above settings:** Checked (enforce on administrators)
6. **Require signed commits:** Optional / Recommended
7. **Require linear history:** Optional / Recommended

### 1.4 Live Verification Commands
Run the following read-only commands to verify branch governance status on GitHub:
```bash
# 1. Check classic branch protection
curl -s https://api.github.com/repos/BHARATTECH-ECOSYSTEMS/RivinityADS/branches/main | jq .protection

# 2. Check repository rulesets
curl -s https://api.github.com/repos/BHARATTECH-ECOSYSTEMS/RivinityADS/rulesets | jq .
```
*Expected Status:*
- When Rulesets are active: `/rulesets` returns an array containing `{"name": "main-release-governance", "enforcement": "active", ...}`.
- When classic protection is active: `branches/main` returns `"protected": true`.

### 1.5 Branch Lifecycle & Stale Branch Policy
- All feature and fix branches must branch from latest `main`.
- Feature and bugfix branches must be deleted upon successful merge to keep the repository clean.
- In GitHub repository `Settings` -> `General` -> `Pull Requests`:
  - Check **Automatically delete head branches**.

---

## 2. Security Vulnerability & Dependency Triage (SLA Matrix)

Automated scanners run via dedicated repository workflows:
- **Dependabot (`.github/dependabot.yml`):** Weekly automated dependency PRs for `github-actions` (`/`), `pip` (`/backend`), and `npm` (`/frontend`).
- **CodeQL SAST (`.github/workflows/codeql.yml`):** Automated static analysis across `python` and `javascript-typescript` on every push/PR to `main` and weekly schedule.
- **Trivy & Artifact-Tied SBOM (`.github/workflows/security-scan-and-sbom.yml`):** Scans repository filesystem and built production Docker images for `CRITICAL`/`HIGH` CVEs, uploads SARIF to GitHub Security, and attaches commit-SHA (`${{ github.sha }}`) CycloneDX SBOMs as CI build artifacts (`cyclonedx-sbom-${{ github.sha }}`). *(Note: `backend/deployment/sbom.json` is preserved only as an initial static CycloneDX baseline snapshot; release audits must consume the build-tied CI artifact SBOM for the target commit SHA).*

Findings must be triaged according to the following SLAs:

| Severity | Definition | Remediation SLA | Escalation Path |
| :--- | :--- | :--- | :--- |
| **Critical (CVSS 9.0–10.0)** | Remote code execution (RCE), unauthenticated bypass, SQLi, secrets leakage | **< 24 Hours** | Hotfix release container; immediate rollback or patch deployment |
| **High (CVSS 7.0–8.9)** | SSRF, privilege escalation, financial tampering, authenticated data leak | **< 7 Days** | Prioritized sprint blocker; staging validation |
| **Medium (CVSS 4.0–6.9)** | Reflected XSS, non-sensitive DoS, non-exploitable transitive dependency | **< 30 Days** | Standard sprint maintenance cycle |
| **Low (CVSS 0.1–3.9)** | Informational finding, minor library deprecation | **Next Minor Release** | Scheduled dependency upgrade |

### 2.1 Triage Protocol
1. **Identify Exposure:** Check whether the vulnerable function or transitive module is reachable in production runtime (e.g. PyTorch CPU serving vs. CUDA).
2. **Reproduce & Mitigate:** Apply upstream security patch or introduce defensive middleware/validator.
3. **Regression Validation:** Execute the complete CI test suite before merge.

### 2.2 Backend Python Dependency Lock & Reproducibility Policy
- `frontend/` locks exact transitive package versions via `frontend/package-lock.json` (`npm ci`).
- `backend/pyproject.toml` declares minimum version floors (`>=`) to preserve PyTorch CPU wheel compatibility across local and CI environments.
- **Release Reproducibility Guardrails:**
  1. Every production release container MUST be built from an immutable Git commit SHA and promoted by container digest (never `:latest`).
  2. Every build generates a build-tied CycloneDX SBOM (`sbom-backend-${{ github.sha }}.cyclonedx.json`) capturing exact resolved Python wheel versions installed inside the image.
  3. Operators producing offline/air-gapped releases must capture `pip freeze > requirements.lock` from the verified release image alongside the SBOM artifact.
  4. Generating a fully hash-pinned Python lockfile (`uv.lock` or `pip-compile --generate-hashes`) is tracked as a scheduled post-close-out enhancement so dependency resolution becomes lockfile-enforced before scaling.

---

## 3. Staging Soak & Drill Procedures

Before tagging a production release candidate (RC), a mandatory 24-hour staging soak and operational rehearsal must be executed.

### 3.1 24-Hour Staging Soak Test
1. **Traffic Replay:** Route synthetic traffic (100 QPS ad requests, 5 QPS tracking clicks, 0.5 QPS conversions) to the staging cluster.
2. **Resource Monitored Baselines:**
   - Memory leak inspection: API container RSS memory must remain flat over 24 hours.
   - Redis connection pool: Ensure connections return cleanly without pool starvation.
   - PostgreSQL lock contention: Ensure deadlocks remain zero (`pg_stat_database.deadlocks = 0`).
3. **Laya Model Optimizer:** Verify automated hourly optimization cycle finishes cleanly without raising unhandled exceptions or violating bidding bounds.

### 3.2 Database Restore Rehearsal (Disaster Recovery Drill)
Once per quarter or prior to major database migrations:
1. Generate an encrypted database snapshot using `backend/scripts/backup_db.py`.
2. Restore the encrypted backup into an isolated staging database per `Runbook 03`.
3. Verify table counts, zero ledger drift, and authoritative migration head (`x3y4z5a6b7c8`).

### 3.3 Production Rollback Drill
Verify team capability to execute zero-downtime rollback per `Runbook 02`:
1. Revert container image tags to the previous immutable release SHA.
2. Ensure downgrade or backward-compatible schema tolerance.
3. Verify ad serving health endpoints report `healthy` within 60 seconds of cutover.

---

## 4. Pre-Launch Production Sign-off Checklist

Before flipping traffic to a new release, all items must receive explicit sign-off:

- [ ] **CI Pipeline Green:** All 4 required GitHub Actions checks green on the release commit.
- [ ] **Alembic Single-Head:** Migration tree contains exactly 1 head matching `x3y4z5a6b7c8`.
- [ ] **Security Secrets Verification:** No default passwords in production `.env`; `LITEADS_AUTH__JWT_SECRET` and `LITEADS_TRACKING__SECRET` exceed 32 characters.
- [ ] **Deterministic Serving Invariant:** Ad serving defaults to statistical CTR/CVR scoring; ML prediction flag disabled or gated behind fallback.
- [ ] **Financial Ledger Audit:** Nightly reconciliation confirms zero discrepancy between `advertisers.balance` and `transactions` sums.
- [ ] **Browser E2E Verification:** Playwright Chromium tests pass all 3 role journeys (Advertiser, Publisher, Admin) and accessibility smoke checks.
- [ ] **Ephemeral Live Consent Test:** Live backend cookie banner and consent revocation verified with clean session teardown.
- [ ] **Monitoring & Alerting Readiness:** Prometheus metrics scraping active; Alertmanager rules loaded for outbox lag, ledger drift, and rate limiting.
- [ ] **Sign-off Complete:** Platform Lead and Security Lead approval recorded.
