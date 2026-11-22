# Runbook 10: Release Governance, Branch Protection & Soak Rehearsal

## Objective
Standard operating procedure for release governance, mandatory GitHub repository branch protection gates, vulnerability triage protocols, staging soak rehearsal, and pre-launch production sign-off.

---

## 1. Branch Protection & Quality Gates Policy

All production code merges into `main` require strict continuous integration passing status and peer review. No direct pushes or force pushes are permitted.

### 1.1 Mandatory GitHub Branch Protection Configuration
In GitHub Repository Settings (`Settings` -> `Branches` -> `Branch protection rules` for `main`):
1. **Require a pull request before merging:**
   - Require approvals: Minimum 1 authorized engineering lead.
   - Dismiss stale pull request approvals when new commits are pushed.
   - Require review from Code Owners.
2. **Require status checks to pass before merging:**
   - Require branches to be up to date before merging (`Strict`).
   - The following **4 exact status check names** (matching `.github/workflows/ci.yml`) must be marked as required:
     - `Backend Quality & Regression Gates` (17-suite multi-phase backend pytest + Alembic single-head validation)
     - `Frontend Typecheck, Build & Unit Tests` (TypeScript `tsc --noEmit`, Vite production bundle, 33-suite automated Node test runner)
     - `PostgreSQL Concurrency & Ledger Integration` (PostgreSQL 15 transactional concurrency, idempotency, and zero-drift verification)
     - `Playwright Real-Browser E2E Security Quality Gate` (Chromium browser-level security, consent gate, and 3-role journey assertions)
3. **Require conversation resolution before merging.**
4. **Require signed commits** (cryptographic GPG/SSH commit signature verification).
5. **Require linear history** (Squash and merge or Rebase and merge; prevent merge bubbles).
6. **Do not allow bypassing the above settings** (Enforce restrictions for administrators).

---

## 2. Security Vulnerability & Dependency Triage (SLA Matrix)

Automated scanners (Dependabot, CodeQL, Trivy container analysis) run continuously. Findings must be triaged according to the following SLAs:

| Severity | Definition | Remediation SLA | Escalation Path |
| :--- | :--- | :--- | :--- |
| **Critical (CVSS 9.0–10.0)** | Remote code execution (RCE), unauthenticated bypass, SQLi, secrets leakage | **< 24 Hours** | Hotfix release container; immediate rollback or patch deployment |
| **High (CVSS 7.0–8.9)** | SSRF, privilege escalation, financial tampering, authenticated data leak | **< 7 Days** | Prioritized sprint blocker; staging validation |
| **Medium (CVSS 4.0–6.9)** | Reflected XSS, non-sensitive DoS, non-exploitable transitive dependency | **< 30 Days** | Standard sprint maintenance cycle |
| **Low (CVSS 0.1–3.9)** | Informational finding, minor library deprecation | **Next Minor Release** | Scheduled dependency upgrade |

### Triage Protocol
1. **Identify Exposure:** Check whether the vulnerable function or transitive module is reachable in production runtime (e.g. PyTorch CPU serving vs. CUDA).
2. **Reproduce & Mitigate:** Apply upstream security patch or introduce defensive middleware/validator.
3. **Regression Validation:** Execute the complete CI test suite before merge.

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
