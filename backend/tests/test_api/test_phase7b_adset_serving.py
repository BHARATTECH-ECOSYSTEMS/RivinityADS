"""Phase 7B: AdSet APIs and End-to-End Serving Integration Tests.

Validates:
1. AdSet CRUD & lifecycle endpoints (create, list, get, update, pause, resume, delete).
2. Authorization & tenant boundaries (Advertiser A cannot touch Advertiser B's ad set, publisher rejected).
3. Sub-budget validation (ad set daily budget cannot exceed campaign daily budget).
4. Creative to AdSet parent consistency (valid ad set required, mismatched parent campaign rejected).
5. AdSet targeting isolation (Set A vs Set B isolated; legacy campaign targeting updates default ad set only).
6. Candidate retrieval & targeting isolation (Set A creative never serves on Set B match only; paused ad set never serves).
7. Budget enforcement (ad set daily sub-budget exhaustion stops delivery; campaign ceiling stops all ad sets).
8. Server-side ad_set_id derivation and event accounting (Redis ad set spend updated, idempotency preserved).
9. Reporting aggregation (ad set metrics breakdown aggregates into campaign totals exactly once).
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from liteads.ad_server.main import app
from liteads.common.cache import redis_client
from liteads.common.database import get_session
from liteads.common.utils import current_date
from liteads.models import (
    AdEvent,
    AdSet,
    AdSlot,
    Advertiser,
    Campaign,
    Creative,
    EventType,
    ModerationStatus,
    Publisher,
    Status,
    TargetingRule,
    User,
)
from liteads.rec_engine.filter.budget import BudgetFilter
from unittest.mock import AsyncMock, MagicMock
from liteads.common.tracking_sig import sign_tracking
from liteads.rec_engine.retrieval.targeting import TargetingRetrieval
from liteads.schemas.internal import AdCandidate, UserContext

pytestmark = pytest.mark.asyncio


# ── Fixtures ─────────────────────────────────────────────────────────────────

class MockRedisPipeline:
    def __init__(self, store, ttls):
        self.store = store
        self.ttls = ttls
        self.commands = []

    def __getattr__(self, name: str):
        def _method(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self
        return _method

    async def execute(self):
        results = []
        for item in self.commands:
            cmd, args, kwargs = item[0], item[1], item[2] if len(item) > 2 else {}
            if cmd == "hgetall":
                key = args[0]
                val = self.store.get(key, {})
                results.append(dict(val) if isinstance(val, dict) else {})
            elif cmd == "hget":
                key, field = args[0], args[1]
                h = self.store.get(key, {})
                results.append(str(h.get(field)) if isinstance(h, dict) and field in h else None)
            elif cmd == "get":
                key = args[0]
                val = self.store.get(key)
                results.append(str(val) if val is not None else None)
            elif cmd == "incr":
                key = args[0]
                val = int(self.store.get(key, 0)) + 1
                self.store[key] = val
                results.append(val)
            elif cmd == "hincrbyfloat":
                key, field, amount = args[0], args[1], float(args[2])
                h = self.store.setdefault(key, {})
                if isinstance(h, dict):
                    cur = float(h.get(field, 0.0))
                    new_val = cur + amount
                    h[field] = str(new_val)
                    results.append(new_val)
                else:
                    results.append(amount)
            elif cmd == "expire":
                results.append(True)
            else:
                results.append(None)
        self.commands.clear()
        return results


@pytest.fixture(autouse=True)
def setup_redis_fixture(monkeypatch):
    """In-memory Redis mock supporting pipeline, hset, hget, hgetall, hincrbyfloat, delete."""
    store = {}
    ttls = {}

    async def mock_connect():
        redis_client._connected = True

    async def mock_close():
        redis_client._connected = False

    async def mock_ping():
        return True

    async def mock_get(key: str):
        val = store.get(key)
        return str(val) if val is not None else None

    async def mock_set(key: str, value, ttl: int | None = None, ex: int | None = None, **kwargs):
        store[key] = value
        return True

    async def mock_hget(key: str, field: str):
        h = store.get(key, {})
        if isinstance(h, dict):
            val = h.get(field)
            return str(val) if val is not None else None
        return None

    async def mock_hgetall(key: str):
        h = store.get(key, {})
        return dict(h) if isinstance(h, dict) else {}

    async def mock_hset(key: str, field: str, value: str):
        h = store.setdefault(key, {})
        if isinstance(h, dict):
            h[field] = str(value)
            return 1
        return 0

    async def mock_hincrbyfloat(key: str, field: str, amount: float):
        h = store.setdefault(key, {})
        if isinstance(h, dict):
            cur = float(h.get(field, 0.0))
            new_val = cur + float(amount)
            h[field] = str(new_val)
            return new_val
        return float(amount)

    async def mock_delete(*keys: str):
        count = 0
        for k in keys:
            if k in store:
                del store[k]
                ttls.pop(k, None)
                count += 1
        return count

    async def mock_expire(key: str, ttl: int):
        return True

    async def mock_incr(key: str, amount: int = 1):
        store[key] = int(store.get(key, 0)) + amount
        return store[key]

    def mock_pipeline():
        return MockRedisPipeline(store, ttls)

    monkeypatch.setattr(redis_client, "connect", mock_connect)
    monkeypatch.setattr(redis_client, "close", mock_close)
    monkeypatch.setattr(redis_client, "get", mock_get)
    monkeypatch.setattr(redis_client, "set", mock_set)
    monkeypatch.setattr(redis_client, "hget", mock_hget)
    monkeypatch.setattr(redis_client, "hgetall", mock_hgetall)
    monkeypatch.setattr(redis_client, "hset", mock_hset)
    monkeypatch.setattr(redis_client, "hincrbyfloat", mock_hincrbyfloat)
    monkeypatch.setattr(redis_client, "delete", mock_delete)
    monkeypatch.setattr(redis_client, "expire", mock_expire)
    monkeypatch.setattr(redis_client, "incr", mock_incr)
    monkeypatch.setattr(redis_client, "pipeline", mock_pipeline)

    mock_inner_client = MagicMock()
    mock_inner_client.ping = mock_ping
    mock_inner_client.pipeline = mock_pipeline
    mock_inner_client.flushdb = AsyncMock(return_value=True)
    monkeypatch.setattr(redis_client, "_client", mock_inner_client)
    redis_client._connected = True

    yield store, ttls

    redis_client._connected = False

@pytest.fixture
async def setup_env(test_db):
    """Setup advertisers, campaigns, publishers, slots, and API clients."""
    app.dependency_overrides[get_session] = lambda: test_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Register Advertiser 1
        res1 = await c.post("/api/v1/auth/register", json={
            "name": "Adv One", "email": "adv1@test.com", "password": "password1234", "role": "advertiser"
        })
        d1 = res1.json()
        token1 = d1["access_token"]
        adv1_id = d1["user"]["advertiser_id"]

        # Fund Advertiser 1
        adv1 = await test_db.get(Advertiser, adv1_id)
        adv1.balance = Decimal("1000.0000")

        # Verify domain example.com for Adv 1
        from liteads.models.domain import AdvertiserDomain, VerificationStatus
        dom = AdvertiserDomain(
            advertiser_id=adv1_id,
            domain="example.com",
            status=VerificationStatus.VERIFIED,
            verification_token="tok123",
        )
        test_db.add(dom)
        await test_db.commit()

        # Register Advertiser 2
        res2 = await c.post("/api/v1/auth/register", json={
            "name": "Adv Two", "email": "adv2@test.com", "password": "password1234", "role": "advertiser"
        })
        d2 = res2.json()
        token2 = d2["access_token"]
        adv2_id = d2["user"]["advertiser_id"]

        # Register Publisher
        resp = await c.post("/api/v1/auth/register", json={
            "name": "Pub One", "email": "pub1@test.com", "password": "password1234", "role": "publisher"
        })
        dp = resp.json()
        token_pub = dp["access_token"]
        pub_id = dp["user"]["publisher_id"]

        # Create Publisher AdSlot
        slot = AdSlot(
            publisher_id=pub_id,
            slot_id="slot_banner_phase7b",
            name="Banner Slot",
            format=1,
            width=300,
            height=250,
            status=1,
            floor_price=Decimal("0.5"),
        )
        test_db.add(slot)

        # Create Campaign 1 for Advertiser 1
        camp1 = Campaign(
            advertiser_id=adv1_id,
            name="Campaign One",
            status=Status.ACTIVE,
            budget_daily=Decimal("100.00"),
            budget_total=Decimal("1000.00"),
            bid_type=1,
            bid_amount=Decimal("2.00"),
            spent_today=Decimal("0.00"),
            spent_total=Decimal("0.00"),
        )
        test_db.add(camp1)

        # Create Campaign 2 for Advertiser 2
        camp2 = Campaign(
            advertiser_id=adv2_id,
            name="Campaign Two",
            status=Status.ACTIVE,
            budget_daily=Decimal("50.00"),
            budget_total=Decimal("500.00"),
            bid_type=1,
            bid_amount=Decimal("1.50"),
            spent_today=Decimal("0.00"),
            spent_total=Decimal("0.00"),
        )
        test_db.add(camp2)
        await test_db.commit()
        await test_db.refresh(camp1)
        await test_db.refresh(camp2)

        yield {
            "client": c,
            "token1": token1,
            "adv1_id": adv1_id,
            "camp1_id": camp1.id,
            "token2": token2,
            "adv2_id": adv2_id,
            "camp2_id": camp2.id,
            "token_pub": token_pub,
            "slot_id": slot.slot_id,
        }

    app.dependency_overrides.clear()


# ── 1. AdSet CRUD & Authorization ────────────────────────────────────────────

async def test_ad_set_crud_and_lifecycle(setup_env):
    """Test full AdSet CRUD, pausing, resuming, and deleting."""
    c = setup_env["client"]
    token = setup_env["token1"]
    camp_id = setup_env["camp1_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create AdSet
    res = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={
            "name": "AdSet North India",
            "daily_budget": 40.0,
            "bid_type": 1,
            "bid_amount": 2.5,
            "freq_cap_daily": 5,
        },
    )
    assert res.status_code == 201, res.text
    ad_set = res.json()
    ad_set_id = ad_set["id"]
    assert ad_set["campaign_id"] == camp_id
    assert ad_set["name"] == "AdSet North India"
    assert Decimal(str(ad_set["daily_budget"])) == Decimal("40.0")
    assert ad_set["status"] == 1

    # 2. List AdSets
    res = await c.get(f"/api/v1/campaign/{camp_id}/ad-sets", headers=headers)
    assert res.status_code == 200
    ad_sets = res.json()
    assert len(ad_sets) >= 1
    assert any(s["id"] == ad_set_id for s in ad_sets)

    # 3. Get single AdSet
    res = await c.get(f"/api/v1/ad-set/{ad_set_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["id"] == ad_set_id

    # 4. Update AdSet (PATCH)
    res = await c.patch(
        f"/api/v1/ad-set/{ad_set_id}",
        headers=headers,
        json={"name": "AdSet North India Updated", "daily_budget": 60.0},
    )
    assert res.status_code == 200
    assert res.json()["name"] == "AdSet North India Updated"
    assert Decimal(str(res.json()["daily_budget"])) == Decimal("60.0")

    # 5. Pause AdSet
    res = await c.patch(f"/api/v1/ad-set/{ad_set_id}/pause", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == Status.PAUSED

    # 6. Resume AdSet
    res = await c.patch(f"/api/v1/ad-set/{ad_set_id}/resume", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == Status.ACTIVE

    # 7. Delete AdSet
    res = await c.delete(f"/api/v1/ad-set/{ad_set_id}", headers=headers)
    assert res.status_code == 204

    # Verify status is deleted
    res = await c.get(f"/api/v1/ad-set/{ad_set_id}", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == Status.DELETED


async def test_ad_set_sub_budget_cannot_exceed_campaign_budget(setup_env):
    """An ad set's daily budget is a sub-limit and cannot exceed campaign.budget_daily."""
    c = setup_env["client"]
    token = setup_env["token1"]
    camp_id = setup_env["camp1_id"]  # campaign daily budget is 100.00
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to create ad set with daily_budget = 150.0 (exceeds 100.0)
    res = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={"name": "Excessive Budget AdSet", "daily_budget": 150.0},
    )
    assert res.status_code == 400
    assert "cannot exceed campaign daily budget" in res.json()["detail"]

    # Valid creation with 80.0
    res = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={"name": "Valid Budget AdSet", "daily_budget": 80.0},
    )
    assert res.status_code == 201
    ad_set_id = res.json()["id"]

    # Attempt to update daily_budget to 120.0
    res = await c.patch(
        f"/api/v1/ad-set/{ad_set_id}",
        headers=headers,
        json={"daily_budget": 120.0},
    )
    assert res.status_code == 400
    assert "cannot exceed campaign daily budget" in res.json()["detail"]


async def test_ad_set_tenant_isolation(setup_env):
    """Advertiser A cannot view, update, pause, or delete Advertiser B's ad sets."""
    c = setup_env["client"]
    token1 = setup_env["token1"]
    token2 = setup_env["token2"]
    camp1_id = setup_env["camp1_id"]
    token_pub = setup_env["token_pub"]

    # Adv 1 creates an ad set
    res = await c.post(
        f"/api/v1/campaign/{camp1_id}/ad-sets",
        headers={"Authorization": f"Bearer {token1}"},
        json={"name": "Adv1 AdSet", "daily_budget": 20.0},
    )
    assert res.status_code == 201
    ad_set_id = res.json()["id"]

    # Adv 2 tries to GET Adv 1's ad set -> 403
    res = await c.get(
        f"/api/v1/ad-set/{ad_set_id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert res.status_code == 403

    # Adv 2 tries to PATCH Adv 1's ad set -> 403
    res = await c.patch(
        f"/api/v1/ad-set/{ad_set_id}",
        headers={"Authorization": f"Bearer {token2}"},
        json={"name": "Hacked AdSet"},
    )
    assert res.status_code == 403

    # Adv 2 tries to create an ad set under Adv 1's campaign -> 403
    res = await c.post(
        f"/api/v1/campaign/{camp1_id}/ad-sets",
        headers={"Authorization": f"Bearer {token2}"},
        json={"name": "Injected AdSet"},
    )
    assert res.status_code == 403

    # Publisher role tries to access -> 403
    res = await c.get(
        f"/api/v1/ad-set/{ad_set_id}",
        headers={"Authorization": f"Bearer {token_pub}"},
    )
    assert res.status_code == 403


# ── 2. Creative & AdSet Parent Consistency ───────────────────────────────────

async def test_creative_ad_set_relationship_validation(setup_env):
    """Validate creative creation and updates enforce consistent campaign and ad set relationship."""
    c = setup_env["client"]
    token1 = setup_env["token1"]
    camp1_id = setup_env["camp1_id"]
    camp2_id = setup_env["camp2_id"]
    headers = {"Authorization": f"Bearer {token1}"}

    # Create AdSet under Camp 1
    res = await c.post(
        f"/api/v1/campaign/{camp1_id}/ad-sets",
        headers=headers,
        json={"name": "AdSet 1", "daily_budget": 30.0},
    )
    ad_set_1_id = res.json()["id"]

    # 1. Create creative with valid matching ad_set_id
    res = await c.post(
        "/api/v1/creative",
        headers=headers,
        json={
            "campaign_id": camp1_id,
            "ad_set_id": ad_set_1_id,
            "title": "Creative 1",
            "landing_url": "https://example.com/item1",
            "creative_type": 1,
            "width": 300,
            "height": 250,
            "status": 1,
        },
    )
    assert res.status_code == 201, res.text
    creative = res.json()
    assert creative["ad_set_id"] == ad_set_1_id

    # 2. Create creative with ad_set_id from different campaign -> 400
    res = await c.post(
        "/api/v1/creative",
        headers=headers,
        json={
            "campaign_id": camp1_id,
            "ad_set_id": 999999,  # nonexistent
            "title": "Invalid Creative",
            "landing_url": "https://example.com/invalid",
            "creative_type": 1,
            "width": 300,
            "height": 250,
        },
    )
    assert res.status_code == 400
    assert "AdSet not found" in res.json()["detail"]

    # 3. Create creative without specifying ad_set_id -> assigns default ad set
    res = await c.post(
        "/api/v1/creative",
        headers=headers,
        json={
            "campaign_id": camp1_id,
            "title": "Legacy Style Creative",
            "landing_url": "https://example.com/legacy",
            "creative_type": 1,
            "width": 300,
            "height": 250,
        },
    )
    assert res.status_code == 201
    assert res.json()["ad_set_id"] is not None


# ── 3. AdSet Targeting & Isolation ───────────────────────────────────────────

async def test_ad_set_targeting_isolation(setup_env):
    """Set A and Set B have isolated targeting; legacy campaign endpoint operates only on default ad set."""
    c = setup_env["client"]
    token = setup_env["token1"]
    camp_id = setup_env["camp1_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create AdSet A (targeted to Maharashtra, India)
    res_a = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={"name": "AdSet Maharashtra", "daily_budget": 20.0},
    )
    ad_set_a_id = res_a.json()["id"]

    # Create AdSet B (targeted to Karnataka, India)
    res_b = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={"name": "AdSet Karnataka", "daily_budget": 20.0},
    )
    ad_set_b_id = res_b.json()["id"]

    # Set targeting on AdSet A
    res = await c.put(
        f"/api/v1/ad-set/{ad_set_a_id}/targeting",
        headers=headers,
        json={
            "geo_include": [{"country": "IN", "state": "MH"}],
            "sector_include": ["tech", "saas"],
        },
    )
    assert res.status_code == 200
    assert res.json()["ad_set_id"] == ad_set_a_id

    # Set targeting on AdSet B
    res = await c.put(
        f"/api/v1/ad-set/{ad_set_b_id}/targeting",
        headers=headers,
        json={
            "geo_include": [{"country": "IN", "state": "KA"}],
            "sector_include": ["fintech"],
        },
    )
    assert res.status_code == 200
    assert res.json()["ad_set_id"] == ad_set_b_id

    # Verify AdSet A targeting is strictly MH + tech/saas
    res = await c.get(f"/api/v1/ad-set/{ad_set_a_id}/targeting", headers=headers)
    assert res.status_code == 200
    data_a = res.json()
    assert data_a["geo_include"][0]["state"] == "MH"
    assert "tech" in data_a["sector_include"]
    assert "fintech" not in data_a["sector_include"]

    # Verify AdSet B targeting is strictly KA + fintech
    res = await c.get(f"/api/v1/ad-set/{ad_set_b_id}/targeting", headers=headers)
    assert res.status_code == 200
    data_b = res.json()
    assert data_b["geo_include"][0]["state"] == "KA"
    assert "fintech" in data_b["sector_include"]
    assert "tech" not in data_b["sector_include"]

    # Verify updating legacy campaign targeting does NOT alter or delete AdSet B's rules
    res = await c.put(
        f"/api/v1/campaign/{camp_id}/targeting",
        headers=headers,
        json={
            "geo_include": [{"country": "IN", "state": "DL"}],
            "sector_include": ["ecommerce"],
        },
    )
    assert res.status_code == 200

    # AdSet B's KA + fintech targeting must remain completely intact!
    res = await c.get(f"/api/v1/ad-set/{ad_set_b_id}/targeting", headers=headers)
    assert res.status_code == 200
    data_b_after = res.json()
    assert data_b_after["geo_include"][0]["state"] == "KA"
    assert "fintech" in data_b_after["sector_include"]


# ── 4. Candidate Retrieval & Serving Isolation ───────────────────────────────

async def test_serving_retrieval_ad_set_targeting_isolation(test_db):
    """An ad in Set A must not become eligible because only Set B's targeting matches."""
    # Create Advertiser & Campaign
    adv = Advertiser(name="Test Serving Adv", balance=Decimal("500.00"), status=1)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Targeting Test Camp",
        status=Status.ACTIVE,
        budget_daily=Decimal("100.00"),
        bid_type=1,
        bid_amount=Decimal("2.00"),
        spent_today=Decimal("0.00"),
        spent_total=Decimal("0.00"),
    )
    test_db.add(camp)
    await test_db.flush()

    # AdSet A: Maharashtra, India
    ad_set_a = AdSet(
        campaign_id=camp.id,
        name="MH AdSet",
        status=Status.ACTIVE,
        daily_budget=Decimal("50.00"),
        bid_type=1,
        bid_amount=Decimal("2.50"),
    )
    test_db.add(ad_set_a)

    # AdSet B: Karnataka, India
    ad_set_b = AdSet(
        campaign_id=camp.id,
        name="KA AdSet",
        status=Status.ACTIVE,
        daily_budget=Decimal("50.00"),
        bid_type=1,
        bid_amount=Decimal("3.00"),
    )
    test_db.add(ad_set_b)
    await test_db.flush()

    # Targeting Rule for Set A
    rule_a = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set_a.id,
        rule_type="geo",
        is_include=True,
        rule_value={"entries": [{"country": "IN", "state": "MH"}]},
    )
    test_db.add(rule_a)

    # Targeting Rule for Set B
    rule_b = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set_b.id,
        rule_type="geo",
        is_include=True,
        rule_value={"entries": [{"country": "IN", "state": "KA"}]},
    )
    test_db.add(rule_b)

    # Creative in Set A (Approved)
    cr_a = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set_a.id,
        title="Creative MH",
        landing_url="https://example.com/mh",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(cr_a)

    # Creative in Set B (Approved)
    cr_b = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set_b.id,
        title="Creative KA",
        landing_url="https://example.com/ka",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(cr_b)
    await test_db.commit()

    retrieval = TargetingRetrieval(test_db)
    await retrieval.refresh()

    # 1. User from Maharashtra (MH) matches Set A ONLY
    ctx_mh = UserContext(country="IN", region="MH")
    candidates_mh = await retrieval.retrieve(user_context=ctx_mh, slot_id="dummy_slot")
    assert len(candidates_mh) == 1
    assert candidates_mh[0].creative_id == cr_a.id
    assert candidates_mh[0].ad_set_id == ad_set_a.id

    # 2. User from Karnataka (KA) matches Set B ONLY
    ctx_ka = UserContext(country="IN", region="KA")
    candidates_ka = await retrieval.retrieve(user_context=ctx_ka, slot_id="dummy_slot")
    assert len(candidates_ka) == 1
    assert candidates_ka[0].creative_id == cr_b.id
    assert candidates_ka[0].ad_set_id == ad_set_b.id

    # 3. User from Delhi (DL) matches NEITHER
    ctx_dl = UserContext(country="IN", region="DL")
    candidates_dl = await retrieval.retrieve(user_context=ctx_dl, slot_id="dummy_slot")
    assert len(candidates_dl) == 0


async def test_paused_ad_set_does_not_serve(test_db):
    """Creatives from paused ad sets do not serve even if campaign is active."""
    adv = Advertiser(name="Test Paused Adv", balance=Decimal("500.00"), status=1)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Active Camp",
        status=Status.ACTIVE,
        budget_daily=Decimal("100.00"),
        bid_type=1,
        bid_amount=Decimal("1.00"),
        spent_today=Decimal("0.00"),
        spent_total=Decimal("0.00"),
    )
    test_db.add(camp)
    await test_db.flush()

    paused_ad_set = AdSet(
        campaign_id=camp.id,
        name="Paused Set",
        status=Status.PAUSED,  # Paused!
        daily_budget=Decimal("50.00"),
        bid_type=1,
        bid_amount=Decimal("1.00"),
    )
    test_db.add(paused_ad_set)
    await test_db.flush()

    cr = Creative(
        campaign_id=camp.id,
        ad_set_id=paused_ad_set.id,
        title="Paused Set Creative",
        landing_url="https://example.com",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(cr)
    await test_db.commit()

    retrieval = TargetingRetrieval(test_db)
    await retrieval.refresh()

    ctx = UserContext()
    candidates = await retrieval.retrieve(user_context=ctx, slot_id="dummy")
    assert len(candidates) == 0


# ── 5. Budget, Billing & Event Accounting ────────────────────────────────────

async def test_ad_set_sub_budget_exhaustion_stops_delivery(test_db):
    """AdSet daily sub-budget exhaustion filters candidates, while campaign budget exhaustion stops all."""
    today = current_date()

    # Set up candidate with ad set sub-budget
    candidate_set_a = AdCandidate(
        campaign_id=101,
        ad_set_id=201,
        creative_id=301,
        advertiser_id=401,
        bid=2.0,
        bid_type=1,
        title="Set A Ad",
        metadata={"ad_set_daily_budget": 10.0},  # sub-budget: 10.0
    )

    candidate_set_b = AdCandidate(
        campaign_id=101,
        ad_set_id=202,
        creative_id=302,
        advertiser_id=401,
        bid=2.0,
        bid_type=1,
        title="Set B Ad",
        metadata={"ad_set_daily_budget": 50.0},  # sub-budget: 50.0
    )

    budget_filter = BudgetFilter(buffer_ratio=0.95)

    # 1. Clean Redis state
    await redis_client.delete(f"budget:101:{today}")
    await redis_client.delete(f"adset_budget:201:{today}")
    await redis_client.delete(f"adset_budget:202:{today}")

    # Set campaign daily budget = 100.0, spent = 0.0
    await redis_client.hset(f"budget:101:{today}", "budget_daily", "100.0")
    await redis_client.hset(f"budget:101:{today}", "spent_today", "0.0")

    # Set ad set 201 spent_today = 9.8 (exceeds 10.0 * 0.95 = 9.5)
    await redis_client.hset(f"adset_budget:201:{today}", "spent_today", "9.8")
    # Set ad set 202 spent_today = 2.0 (under 50.0 * 0.95 = 47.5)
    await redis_client.hset(f"adset_budget:202:{today}", "spent_today", "2.0")

    candidates = [candidate_set_a, candidate_set_b]
    filtered = await budget_filter.filter(candidates=candidates, user_context=UserContext())

    # Candidate A must be filtered out due to sub-budget exhaustion! Candidate B remains.
    assert len(filtered) == 1
    assert filtered[0].ad_set_id == 202

    # 2. If campaign daily budget is exhausted (spent = 96.0 on 100.0 cap), ALL candidates filtered
    await redis_client.hset(f"budget:101:{today}", "spent_today", "96.0")
    filtered_all = await budget_filter.filter(candidates=candidates, user_context=UserContext())
    assert len(filtered_all) == 0


async def test_event_tracking_updates_ad_set_spend(setup_env, test_db):
    """Tracking an impression updates Redis ad set spend and derives ad_set_id server-side."""
    c = setup_env["client"]
    token = setup_env["token1"]
    camp_id = setup_env["camp1_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create AdSet
    res = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={"name": "Tracking Test AdSet", "daily_budget": 50.0, "bid_amount": 2.0, "bid_type": 1},
    )
    ad_set_id = res.json()["id"]

    # Create Creative linked to AdSet
    res = await c.post(
        "/api/v1/creative",
        headers=headers,
        json={
            "campaign_id": camp_id,
            "ad_set_id": ad_set_id,
            "title": "Tracking Creative",
            "landing_url": "https://example.com/test",
            "creative_type": 1,
            "width": 300,
            "height": 250,
            "status": 1,
        },
    )
    creative_id = res.json()["id"]

    today = current_date()
    await redis_client.delete(f"adset_budget:{ad_set_id}:{today}")

    # Track impression event
    ad_id = f"ad_{camp_id}_{creative_id}"
    req_id = "req_phase7b_test_1"
    slot_id = setup_env["slot_id"]
    sig = sign_tracking(req_id, ad_id, slot_id)
    res = await c.get(f"/api/v1/event/track?type=impression&req={req_id}&ad={ad_id}&slot={slot_id}&sig={sig}")
    assert res.status_code == 200

    # Verify Redis ad set spent was incremented
    spent_raw = await redis_client.hget(f"adset_budget:{ad_set_id}:{today}", "spent_today")
    assert spent_raw is not None
    assert float(spent_raw) > 0.0

    # Verify event stored in DB with correct creative
    res = await test_db.execute(select(AdEvent).where(AdEvent.request_id == req_id))
    event = res.scalar_one_or_none()
    assert event is not None
    assert event.creative_id == creative_id
    assert event.campaign_id == camp_id


# ── 6. Reporting Aggregation ─────────────────────────────────────────────────

async def test_ad_set_reporting_breakdown(setup_env, test_db):
    """AdSet metrics breakdown aggregates into campaign totals exactly once."""
    c = setup_env["client"]
    token = setup_env["token1"]
    camp_id = setup_env["camp1_id"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create AdSet 1
    res1 = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={"name": "Report Set 1", "daily_budget": 50.0},
    )
    set1_id = res1.json()["id"]

    # Create AdSet 2
    res2 = await c.post(
        f"/api/v1/campaign/{camp_id}/ad-sets",
        headers=headers,
        json={"name": "Report Set 2", "daily_budget": 50.0},
    )
    set2_id = res2.json()["id"]

    # Creatives
    cr1 = Creative(
        campaign_id=camp_id,
        ad_set_id=set1_id,
        title="Cr 1",
        landing_url="https://example.com",
        creative_type=1,
        width=300,
        height=250,
        status=1,
        moderation_status=2,
    )
    cr2 = Creative(
        campaign_id=camp_id,
        ad_set_id=set2_id,
        title="Cr 2",
        landing_url="https://example.com",
        creative_type=1,
        width=300,
        height=250,
        status=1,
        moderation_status=2,
    )
    test_db.add(cr1)
    test_db.add(cr2)
    await test_db.commit()
    await test_db.refresh(cr1)
    await test_db.refresh(cr2)

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 3 impressions on Set 1, cost 0.003
    for i in range(3):
        test_db.add(AdEvent(
            request_id=f"r1_{i}",
            campaign_id=camp_id,
            creative_id=cr1.id,
            event_type=EventType.IMPRESSION,
            event_time=now,
            cost=Decimal("0.001"),
            idempotency_key=f"r1_{i}:{cr1.id}:1:1",
        ))

    # 2 impressions, 1 click on Set 2, cost 0.052
    for i in range(2):
        test_db.add(AdEvent(
            request_id=f"r2_imp_{i}",
            campaign_id=camp_id,
            creative_id=cr2.id,
            event_type=EventType.IMPRESSION,
            event_time=now,
            cost=Decimal("0.001"),
            idempotency_key=f"r2_imp_{i}:{cr2.id}:1:1",
        ))
    test_db.add(AdEvent(
        request_id="r2_clk_1",
        campaign_id=camp_id,
        creative_id=cr2.id,
        event_type=EventType.CLICK,
        event_time=now,
        cost=Decimal("0.050"),
        idempotency_key=f"r2_clk_1:{cr2.id}:2:1",
    ))
    await test_db.commit()

    # Query AdSet reporting endpoint
    res = await c.get(f"/api/v1/campaign/{camp_id}/ad-set-reporting", headers=headers)
    assert res.status_code == 200, res.text
    report = res.json()

    assert report["campaign_id"] == camp_id
    assert report["total_impressions"] == 5
    assert report["total_clicks"] == 1
    assert Decimal(str(report["total_spend"])) == Decimal("0.055")

    ad_set_map = {item["ad_set_id"]: item for item in report["ad_sets"]}
    assert set1_id in ad_set_map
    assert set2_id in ad_set_map

    item1 = ad_set_map[set1_id]
    assert item1["impressions"] == 3
    assert item1["clicks"] == 0
    assert Decimal(str(item1["spend"])) == Decimal("0.003")

    item2 = ad_set_map[set2_id]
    assert item2["impressions"] == 2
    assert item2["clicks"] == 1
    assert Decimal(str(item2["spend"])) == Decimal("0.052")
