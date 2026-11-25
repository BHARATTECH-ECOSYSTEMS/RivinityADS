# RivinityADS — Backend Engine

High-performance, secure digital advertising and ad-serving backend built with FastAPI, SQLAlchemy (asyncio), PostgreSQL 15, Redis 7, and scikit-learn / PyTorch.

---

## 1. Core Architecture

The backend repository is organized into bounded modular subsystems:

```
backend/
├── liteads/
│   ├── ad_server/              # FastAPI application, REST endpoints, and middleware
│   │   ├── routers/            # Versioned API routes (/api/v1/ad, /auth, /campaigns, /finance, etc.)
│   │   ├── services/           # Domain business logic (billing, domain verification, pacing, etc.)
│   │   └── main.py             # ASGI entrypoint, exception handlers, and lifespan hooks
│   ├── rec_engine/             # Ad retrieval, filtering, statistical ranking, and scoring
│   ├── ml_engine/              # Offline model training, evaluation, governance, and serving validator
│   ├── models/                 # SQLAlchemy 2.0 async database models
│   ├── schemas/                # Pydantic v2 validation schemas
│   └── common/                 # Database connection pools, Redis client, logging, and configuration
├── alembic/                    # Database migration scripts (authoritative head: y4z5a6b7c8d9)
├── scripts/                    # Operational tooling (backup, restore, PostgreSQL concurrency tests)
└── tests/                      # Multi-phase pytest suites and regression tests
```

---

## 2. Prerequisites & Environment Setup

- **Python:** 3.11+
- **PostgreSQL:** 15.x
- **Redis:** 7.x

### Virtual Environment Setup
```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Upgrade pip and install CPU-only PyTorch wheel (avoids multi-GB CUDA wheels)
python -m pip install --upgrade pip
pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch

# Install backend package in editable mode with development dependencies
pip install -e "backend[dev]"
```

### Environment Variables
Configure `.env` or set environment variables before running:
```bash
export LITEADS_ENV=development
export LITEADS_DATABASE__HOST=localhost
export LITEADS_DATABASE__PORT=5432
export LITEADS_DATABASE__USER=liteads
export LITEADS_DATABASE__PASSWORD=your_password
export LITEADS_DATABASE__NAME=liteads
export LITEADS_AUTH__JWT_SECRET=super_secure_jwt_secret_at_least_32_chars_long
export LITEADS_TRACKING__SECRET=super_secure_tracking_secret_at_least_32_chars
```

---

## 3. Database Migrations

Alembic manages all schema migrations. Maintain a strictly linear migration graph with exactly **one head**:
```bash
# Check authoritative migration head (expected: y4z5a6b7c8d9)
python -m alembic -c backend/alembic.ini heads

# Upgrade database to latest schema
python -m alembic -c backend/alembic.ini upgrade head

# Validate migration graph truthfulness
python -m liteads.common.alembic_utils
```

---

## 4. Running the Server

Start the local FastAPI development server:
```bash
uvicorn liteads.ad_server.main:app --host 0.0.0.0 --port 8000 --reload
```

### Health & Readiness Endpoints
- **Liveness probe:** `GET /health` or `GET /health/live`
- **Readiness probe:** `GET /health/ready`
- **Dependency diagnostics:** `GET /health/dependencies`
- **Ad request smoke test:** `POST /api/v1/ad/request`

---

## 5. Running Automated Backend Tests

### Core Multi-Phase Pytest Suite (17 Suites)
```bash
pytest backend/tests/test_ad_server/test_health.py \
       backend/tests/test_ad_server/test_ad.py \
       backend/tests/test_api/test_phase0_migration_truthfulness.py \
       backend/tests/test_api/test_phase1_visitor_consent.py \
       backend/tests/test_api/test_phase1_visitor_consent_gate.py \
       backend/tests/test_api/test_phase2_channel_conformance.py \
       backend/tests/test_api/test_phase3_publisher_verification.py \
       backend/tests/test_api/test_phase4_advertiser_campaign_readiness.py \
       backend/tests/test_api/test_phase7_ad_quality_and_admin_ops.py \
       backend/tests/test_api/test_phase11_conversion_measurement.py \
       backend/tests/test_api/test_phase10f_monetization_consent.py \
       backend/tests/test_api/test_phase10g_security_e2e.py \
       backend/tests/test_api/test_phase9_3_persistence_lifecycle.py \
       backend/tests/test_api/test_finance_api.py \
       backend/tests/test_api/test_phase5_financial_event_pipeline_proof.py \
       backend/tests/test_api/test_phase6_laya_decision_hardening.py \
       backend/tests/test_api/test_phase8_ml_governance.py \
       -v --tb=short
```

### Real PostgreSQL 15 Concurrency & Idempotency Verification
```bash
python backend/scripts/verify_postgres_phase11_concurrency.py
```
This script exercises real PostgreSQL transactional isolation, balance race conditions, double-entry financial ledger invariants, and conversion deduplication.
