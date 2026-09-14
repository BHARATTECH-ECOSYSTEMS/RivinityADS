# Bharattech Ads — Canonical Project Context

This is the authoritative project context document. Any AI agent working on this codebase must read and follow this before making changes.

## Core Identity
- **Product**: Bharattech Ads (RivinityADS / LiteAds)
- **Goal**: Self-hosted, open-source, end-to-end digital advertising & AdTech platform
- **NOT** just an ad server, dashboard, or ML demo — it's a full multi-tenant advertising ecosystem

## Technology Stack
- Frontend: React + TypeScript + Vite
- Backend: Python + FastAPI
- Database: PostgreSQL
- Cache: Redis
- Containers: Docker Compose
- ML: LR, FM, DeepFM CTR prediction
- No Java as core language

## Three User Roles
1. **Admin** — platform operator, never self-registerable
2. **Advertiser** — creates campaigns, uploads creatives, buys ads
3. **Publisher** — monetizes inventory, earns revenue

## Implementation Roadmap Priority
1. Phase 1: Production Foundation (auth, RBAC, admin bootstrap)
2. Phase 2: Real Application Data (remove mocks, connect to APIs)
3. Phase 3: Complete Ad Server (campaign lifecycle, targeting, reporting)
4. Phase 4: Money & Attribution (wallet, billing, payouts)
5. Phase 5: Publisher Platform (SDK, onboarding, floor pricing)
6. Phase 6: Programmatic (OpenRTB, SSP, ADX, DSP)
7. Phase 7: Intelligence (ML optimization, fraud, DMP)

## Engineering Rules
- Backend authorization is mandatory; frontend guards are NOT security
- Never trust client-supplied ownership IDs
- Never store plaintext passwords
- Don't break existing ad-serving, ML, recommendation, health, metrics, PostgreSQL, Redis, or Docker functionality
- Don't introduce large infrastructure (Kafka, etc.) prematurely
- Don't implement programmatic bidding before direct ad-serving is reliable
- Add tests for security-sensitive or financial behavior
- Use migrations for database changes
