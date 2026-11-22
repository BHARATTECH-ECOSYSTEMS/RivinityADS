# Runbook 08: Inventory Disclosure & Programmatic Transparency Standards

## Objective
Authoritative reference on inventory disclosure policies, direct-sold ad serving boundaries, and programmatic transparency standards (`ads.txt`, `app-ads.txt`, and `sellers.json`) within Bharattech Ads / RivinityADS.

---

## 1. Architectural Boundary: Direct-Sold MVP vs. Open Programmatic RTB

Bharattech Ads is architected across structured phases as defined in the canonical roadmap:
- **Phases 0–6 (Current Architecture):** Direct Ad Serving Foundation. Direct publisher onboarding, verified publisher properties, zero-integration iframe and JSON ad requests, signed event tracking, and immutable internal ledger accounting.
- **Phase 7 (OpenRTB & Programmatic Scale):** RTB/ADX, DSP connectors, SSP auction dynamics, and public supply-chain transparency files.

### Why `ads.txt` / `app-ads.txt` / `sellers.json` Apply to Phase 7
1. **Direct-Sold Inventory Is Closed-Loop:**
   - In direct ad serving, RivinityADS is the exclusive, single-party serving engine for contracted publishers.
   - All publisher properties are verified via DNS TXT records (`liteads-site-verification=<token>`) or HTML meta tags prior to ad delivery.
   - Fraudulent domain spoofing is prevented at request time via `Origin` and `Referer` domain verification against the database of verified properties.
2. **Programmatic Intermediaries Require Public Supply Chain Graphs:**
   - IAB Tech Lab standards (`ads.txt`, `app-ads.txt`, `sellers.json`) exist to prevent unauthorized arbitrage and domain spoofing across open ad exchanges where multi-hop intermediaries (resellers, SSPs, DSPs) buy and sell bid requests.
   - In Phase 7, when OpenRTB endpoints (`/api/v1/rtb/bid`, `/sellers.json`) are activated, RivinityADS will automatically host `/sellers.json` and generate `ads.txt` records (`bharattech.org, <publisher_id>, DIRECT, <certification_id>`) for publishers.
   - Attempting to mandate `ads.txt` crawling for closed direct-sold campaigns before programmatic exchange activation creates unnecessary external network dependencies and failure modes.

---

## 2. Inventory Quality & Safety Invariants

Even in direct serving, RivinityADS enforces rigorous publisher safety and inventory controls:
1. **Property Verification Mandatory:** Ad slots bound to unverified properties return HTTP 403 Forbidden.
2. **Delivery Cessation on Suspension:**
   - Suspended publishers (`status = PAUSED` or `SUSPENDED`) immediately halt ad delivery (HTTP 400).
   - Deactivated properties immediately halt ad delivery (HTTP 400).
   - Zero billable impressions or clicks are recorded in the transactional database.
3. **Multi-Signal Invalid Traffic (IVT) Filtering:**
   - Bot user-agents, click velocity (>15 clicks/60s), click bursts (>3 clicks/5s), clickjacking coordinates, and replay attacks are rejected in real-time.
   - NAT / IP-sharing protection: A single IP address is never treated as conclusive fraud.
   - Publisher traffic safety summaries provide actionable guidance without exposing proprietary scoring thresholds.

---

## 3. Website Builder & AI Discovery Invariants

Websites generated or hosted via the RivinityADS website engine adhere to strict safety boundaries:
1. **Anti-Fabrication Policy:** Content generated for business profiles must derive strictly from owner-verified facts. Unsupported superlatives, fabricated statistics, and fake reviews are flagged and rejected.
2. **Accessibility Compliance:** All generated pages undergo automated contrast ratio, alt-text, and heading hierarchy audits.
3. **AI Search Indexing Disclaimer:** The experimental `llms.txt` endpoint summarizes factual NAP (Name, Address, Phone) data for AI crawlers with the mandatory notice:
   > *"Experimental factual summary generated from verified owner profile for AI search engine indexing. This document does not guarantee search engine ranking or AI citation placement."*
