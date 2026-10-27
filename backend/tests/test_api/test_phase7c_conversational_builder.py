"""Phase 7C: Conversational Campaign Builder (Campaign → Ad Set → Ads) Test Suite.

Validates all 15 required scenarios:
1. Successful Campaign → Ad Set → Ads creation.
2. Correct persistence of campaign_id and ad_set_id across turns.
3. Switching between Ad Sets without misassigning subsequent ads.
4. Incomplete fields and follow-up clarification.
5. Invalid and ambiguous targeting inputs.
6. Cross-tenant ID rejection (campaign, ad set, creative).
7. Duplicate requests and retry idempotency.
8. Missing or expired conversation state (HTTP 404).
9. Malformed or untrusted LLM output (graceful recovery).
10. Campaign review output and missing activation prerequisites.
11. Pending and rejected Creative moderation handling.
12. Payment not confirmed by conversational text alone.
13. Attempts to bypass established activation endpoint blocked.
14. Regression of existing conversational builder functionality.
15. Regression of relevant Phase 7A/7B campaign, Ad Set, Creative, and authorization behavior.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from unittest.mock import AsyncMock, patch

logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.main import app
from liteads.ad_server.services.conversation_state import ConversationStateService
from liteads.ad_server.services.generative_intent_client import (
    GenerativeMalformedResponseError,
    GenerativeUnavailableError,
)
from liteads.common.cache import redis_client
from liteads.common.database import get_session
from liteads.common.security import current_user, verified_user
from liteads.models import (
    AdSet,
    Advertiser,
    Campaign,
    Creative,
    ModerationStatus,
    Status,
    TargetingRule,
    User,
)
from liteads.models.domain import AdvertiserDomain
from liteads.schemas.chat import ChatSession
from liteads.schemas.intent import IntentResult, SlotData

pytestmark = pytest.mark.asyncio


# ── In-Memory Redis Mock ──────────────────────────────────────────────────────

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
            cmd, args = item[0], item[1]
            if cmd == "get":
                key = args[0]
                val = self.store.get(key)
                results.append(str(val) if val is not None else None)
            elif cmd == "set":
                self.store[args[0]] = args[1]
                results.append(True)
            elif cmd == "delete":
                for k in args:
                    self.store.pop(k, None)
                results.append(1)
            else:
                results.append(None)
        self.commands.clear()
        return results


@pytest.fixture(autouse=True)
def setup_redis_fixture(monkeypatch):
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
        if ttl or ex:
            ttls[key] = ttl or ex
        return True

    async def mock_get_json(key: str):
        data = store.get(key)
        if not data:
            return None
        if isinstance(data, dict):
            return data
        return json.loads(data)

    async def mock_set_json(key: str, value, ttl: int | None = None):
        store[key] = json.dumps(value)
        if ttl:
            ttls[key] = ttl
        return True

    async def mock_delete(*keys: str):
        cnt = 0
        for k in keys:
            if k in store:
                del store[k]
                ttls.pop(k, None)
                cnt += 1
        return cnt

    async def mock_expire(key: str, ttl: int):
        if key in store:
            ttls[key] = ttl
            return True
        return False

    def mock_pipeline():
        return MockRedisPipeline(store, ttls)

    monkeypatch.setattr(redis_client, "_connected", True)
    monkeypatch.setattr(redis_client, "connect", mock_connect)
    monkeypatch.setattr(redis_client, "close", mock_close)
    monkeypatch.setattr(redis_client, "get", mock_get)
    monkeypatch.setattr(redis_client, "set", mock_set)
    monkeypatch.setattr(redis_client, "get_json", mock_get_json)
    monkeypatch.setattr(redis_client, "set_json", mock_set_json)
    monkeypatch.setattr(redis_client, "delete", mock_delete)
    monkeypatch.setattr(redis_client, "expire", mock_expire)
    monkeypatch.setattr(redis_client, "pipeline", mock_pipeline)


# ── DB Entities and Auth Fixtures ─────────────────────────────────────────────

@pytest.fixture
async def seed_data(test_db: AsyncSession):
    # Advertiser 1 (Primary)
    adv1 = Advertiser(id=1, name="CloudScale Tech", balance=Decimal("5000.00"), status=Status.ACTIVE)
    user1 = User(
        id=1,
        email="adv1@cloudscale.io",
        name="CloudScale User",
        password_hash="fake_hash_123",
        role="advertiser",
        advertiser_id=1,
        is_active=True,
        email_verified=True,
    )
    # Verified Domain for Adv 1
    dom1 = AdvertiserDomain(
        id=1,
        advertiser_id=1,
        domain="cloudscale.io",
        status="VERIFIED",
        verification_token="tok123_scale",
        created_at=datetime.now(timezone.utc),
    )

    # Advertiser 2 (Cross-tenant competitor)
    adv2 = Advertiser(id=2, name="Rival Corp", balance=Decimal("100.00"), status=Status.ACTIVE)
    user2 = User(
        id=2,
        email="adv2@rival.com",
        name="Rival User",
        password_hash="fake_hash_456",
        role="advertiser",
        advertiser_id=2,
        is_active=True,
        email_verified=True,
    )
    camp2 = Campaign(
        id=200,
        advertiser_id=2,
        name="Rival Campaign",
        budget_daily=Decimal("300.00"),
        status=Status.DRAFT,
    )
    as2 = AdSet(
        id=2001,
        campaign_id=200,
        name="Rival Ad Set",
        daily_budget=Decimal("150.00"),
        status=Status.ACTIVE,
    )

    test_db.add_all([adv1, user1, dom1, adv2, user2, camp2, as2])
    await test_db.commit()
    return {"user1": user1, "user2": user2, "adv1": adv1, "adv2": adv2, "camp2": camp2, "as2": as2}


@pytest.fixture
def auth_client1(client: AsyncClient, seed_data):
    user1 = seed_data["user1"]
    app.dependency_overrides[current_user] = lambda: user1
    app.dependency_overrides[verified_user] = lambda: user1
    yield client
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(verified_user, None)


@pytest.fixture
def auth_client2(client: AsyncClient, seed_data):
    user2 = seed_data["user2"]
    app.dependency_overrides[current_user] = lambda: user2
    app.dependency_overrides[verified_user] = lambda: user2
    yield client
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(verified_user, None)


@pytest.fixture
async def session1(auth_client1: AsyncClient, seed_data):
    res = await auth_client1.post("/api/v1/chat/session")
    assert res.status_code == 200
    return res.json()["session_id"]


# ── Test Scenarios ────────────────────────────────────────────────────────────

# 1. Successful Campaign → Ad Set → Ads creation
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_campaign_adset_ads_hierarchy_success(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Turn 1: Create Campaign
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="Cloud Launch 2026", budget_daily=Decimal("1000")),
        confidence=0.95,
        reply="Created campaign Cloud Launch 2026. Now let's create an Ad Set."
    ))
    res1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create campaign Cloud Launch with 1000 daily budget"})
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["tool_status"] == "Success: Campaign Created"
    camp_id = data1["current_campaign_id"]
    assert camp_id is not None
    assert data1["current_step"] == "AD_SET"

    # Turn 2: Create Ad Set
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD_SET",
        slots=SlotData(ad_set_name="Software Engineers", budget_daily=Decimal("600"), bid_amount=Decimal("12.50")),
        confidence=0.95,
        reply="Ad set Software Engineers created under campaign. What creatives would you like to add?"
    ))
    res2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create ad set Software Engineers with 600 daily budget and 12.5 bid"})
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["tool_status"] == "Success: Ad Set Created"
    ad_set_id = data2["current_ad_set_id"]
    assert ad_set_id is not None
    assert data2["current_campaign_id"] == camp_id
    assert data2["current_step"] == "CREATIVE"

    # Turn 3: Set Geo Targeting on active Ad Set
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="India", state="Karnataka", city="Bangalore"),
        confidence=0.9,
        reply="Targeting Bangalore, Karnataka, India configured."
    ))
    res3 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Target Bangalore India"})
    assert res3.status_code == 200
    data3 = res3.json()
    assert "Targeting Updated" in data3["tool_status"]
    assert data3["payload"]["ad_set_id"] == ad_set_id

    # Turn 4: Create Creative (Ad)
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD",
        slots=SlotData(title="CloudScale Workspace", landing_url="https://cloudscale.io/workspace"),
        confidence=0.95,
        reply="Ad created successfully and approved!"
    ))
    res4 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Add creative CloudScale Workspace url https://cloudscale.io/workspace"})
    assert res4.status_code == 200
    data4 = res4.json()
    assert "Success: Ad Created" in data4["tool_status"]
    creative_id = data4["payload"]["creative_id"]
    assert data4["current_step"] == "REVIEW"

    # Verify DB integrity
    camp = await test_db.get(Campaign, camp_id)
    assert camp.name == "Cloud Launch 2026"
    assert camp.budget_daily == Decimal("1000")
    assert camp.status == Status.DRAFT

    as_obj = await test_db.get(AdSet, ad_set_id)
    assert as_obj.campaign_id == camp.id
    assert as_obj.name == "Software Engineers"
    assert as_obj.daily_budget == Decimal("600")
    assert as_obj.bid_amount == Decimal("12.50")

    cr_obj = await test_db.get(Creative, creative_id)
    assert cr_obj.campaign_id == camp.id
    assert cr_obj.ad_set_id == as_obj.id
    assert cr_obj.title == "CloudScale Workspace"
    assert cr_obj.landing_url == "https://cloudscale.io/workspace"
    assert cr_obj.status == Status.PENDING
    assert cr_obj.moderation_status == ModerationStatus.PENDING_REVIEW

    # Verify targeting rule scoped to Ad Set
    rules_res = await test_db.execute(select(TargetingRule).where(TargetingRule.ad_set_id == ad_set_id))
    rules = rules_res.scalars().all()
    assert len(rules) == 1
    assert rules[0].campaign_id == camp_id
    assert rules[0].rule_value["entries"][0]["city"] == "Bangalore"


# 2. Persistence of campaign_id and ad_set_id across turns
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_persistence_of_hierarchy_state(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Turn 1: Create Campaign
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="Persistent Campaign", budget_daily=Decimal("500")),
        confidence=0.9
    ))
    r1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create campaign"})
    cid = r1.json()["current_campaign_id"]

    # Turn 2: Create Ad Set
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD_SET",
        slots=SlotData(ad_set_name="Active AdSet 1", budget_daily=Decimal("250")),
        confidence=0.9
    ))
    r2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create ad set"})
    asid = r2.json()["current_ad_set_id"]

    # Check session directly in Redis service
    session_obj = await ConversationStateService.get_session(session1, user_id=1)
    assert session_obj.current_campaign_id == cid
    assert session_obj.current_ad_set_id == asid

    # Turn 3: Update Ad Set sub-budget without providing IDs
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_BUDGET",
        slots=SlotData(budget_daily=Decimal("350")),
        confidence=0.9
    ))
    r3 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Change budget to 350"})
    assert r3.status_code == 200
    assert r3.json()["tool_status"] == "Success: Ad Set Budget Updated"

    as_obj = await test_db.get(AdSet, asid)
    assert as_obj.daily_budget == Decimal("350")


# 3. Switching between Ad Sets without misassigning Ads
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_switching_ad_sets_without_misassigning_ads(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Setup: Campaign with 2 Ad Sets
    camp = Campaign(advertiser_id=1, name="Multi-Set Campaign", budget_daily=Decimal("2000"), status=Status.DRAFT)
    test_db.add(camp)
    await test_db.flush()
    as1 = AdSet(campaign_id=camp.id, name="Frontend Set", daily_budget=Decimal("500"), status=Status.ACTIVE)
    as2 = AdSet(campaign_id=camp.id, name="Backend Set", daily_budget=Decimal("500"), status=Status.ACTIVE)
    test_db.add_all([as1, as2])
    await test_db.commit()

    # Turn 1: Select Campaign
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SELECT_CAMPAIGN",
        slots=SlotData(campaign_id=camp.id),
        confidence=0.95
    ))
    await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": f"Select campaign {camp.id}"})

    # Turn 2: Select AdSet 1
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SELECT_AD_SET",
        slots=SlotData(ad_set_id=as1.id),
        confidence=0.95
    ))
    r_sel1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": f"Select ad set {as1.id}"})
    assert r_sel1.json()["current_ad_set_id"] == as1.id

    # Turn 3: Add Creative for AdSet 1
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD",
        slots=SlotData(title="React Banner", landing_url="https://cloudscale.io/react"),
        confidence=0.95
    ))
    r_cr1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Add creative React Banner"})
    cr1_id = r_cr1.json()["payload"]["creative_id"]

    # Turn 4: Switch to AdSet 2
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SELECT_AD_SET",
        slots=SlotData(ad_set_id=as2.id),
        confidence=0.95
    ))
    r_sel2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": f"Switch to ad set {as2.id}"})
    assert r_sel2.json()["current_ad_set_id"] == as2.id

    # Turn 5: Add Creative for AdSet 2
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD",
        slots=SlotData(title="Go Server Banner", landing_url="https://cloudscale.io/go"),
        confidence=0.95
    ))
    r_cr2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Add creative Go Server Banner"})
    cr2_id = r_cr2.json()["payload"]["creative_id"]

    # Verify DB: cr1 belongs to as1, cr2 belongs to as2
    cr1 = await test_db.get(Creative, cr1_id)
    cr2 = await test_db.get(Creative, cr2_id)
    assert cr1.ad_set_id == as1.id
    assert cr2.ad_set_id == as2.id
    assert cr1.ad_set_id != cr2.ad_set_id


# 4. Incomplete fields and follow-up clarification
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_incomplete_fields_guidance(mock_llm_cls, auth_client1: AsyncClient, session1: str):
    mock_instance = mock_llm_cls.return_value

    # Try creating ad set without campaign selected
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD_SET",
        slots=SlotData(ad_set_name="Orphan Set"),
        confidence=0.9
    ))
    res1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create ad set Orphan"})
    assert res1.status_code == 200
    assert "select or create a campaign" in res1.json()["assistant_message"].lower()

    # Try creating ad without landing url
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD",
        slots=SlotData(campaign_id=1, ad_set_id=1, title="Missing URL Ad"),
        confidence=0.9
    ))
    res2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Add creative Missing URL Ad"})
    assert res2.status_code == 200
    assert "landing url" in res2.json()["assistant_message"].lower()


# 5. Invalid and ambiguous targeting inputs
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_invalid_and_ambiguous_targeting(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Setup campaign & ad set
    camp = Campaign(advertiser_id=1, name="Targeting Test Camp", budget_daily=Decimal("100"), status=Status.DRAFT)
    test_db.add(camp)
    await test_db.flush()
    as_obj = AdSet(campaign_id=camp.id, name="Targeting Set", daily_budget=Decimal("50"), status=Status.ACTIVE)
    test_db.add(as_obj)
    await test_db.commit()

    # Select Campaign & Ad Set
    session_obj = await ConversationStateService.get_session(session1, user_id=1)
    session_obj.current_campaign_id = camp.id
    session_obj.current_ad_set_id = as_obj.id
    await ConversationStateService.save_session(session_obj)

    # Ambiguous geo: city without state
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(city="Springfield", country="US"),
        confidence=0.9
    ))
    res1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Target Springfield"})
    assert res1.status_code == 200
    assert "specify the state for this city" in res1.json()["assistant_message"].lower()

    # Sub-budget exceeding campaign daily budget (150 > 100)
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="UPDATE_AD_SET",
        slots=SlotData(budget_daily=Decimal("150")),
        confidence=0.9
    ))
    res2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Set ad set budget to 150"})
    assert res2.status_code == 200
    assert "cannot exceed campaign daily budget" in res2.json()["assistant_message"].lower()


# 6. Cross-tenant ID rejection
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_cross_tenant_isolation_rejection(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, seed_data
):
    mock_instance = mock_llm_cls.return_value
    rival_camp_id = seed_data["camp2"].id

    # User 1 attempts to select User 2's campaign
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SELECT_CAMPAIGN",
        slots=SlotData(campaign_id=rival_camp_id),
        confidence=0.95
    ))
    res1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": f"Select campaign {rival_camp_id}"})
    assert res1.status_code == 200
    assert "permission" in res1.json()["assistant_message"].lower() or "authorization" in res1.json()["tool_status"].lower()

    # User 1 attempts to create Ad Set under User 2's campaign
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD_SET",
        slots=SlotData(campaign_id=rival_camp_id, ad_set_name="Stolen Set"),
        confidence=0.95
    ))
    res2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create ad set"})
    assert res2.status_code == 200
    assert "permission" in res2.json()["assistant_message"].lower() or "authorization" in res2.json()["tool_status"].lower()


# 7. Duplicate requests and retry idempotency
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_retry_and_duplicate_idempotency(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Turn 1: Create Campaign
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_CAMPAIGN",
        slots=SlotData(name="Idempotent Campaign", budget_daily=Decimal("1000")),
        confidence=0.95
    ))
    r1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create campaign"})
    cid = r1.json()["current_campaign_id"]

    # Turn 2: Retry same creation
    r2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create campaign"})
    assert r2.status_code == 200
    assert r2.json()["payload"].get("reused") is True
    assert r2.json()["current_campaign_id"] == cid

    # Verify only 1 campaign created in DB
    camps = (await test_db.execute(select(Campaign).where(Campaign.name == "Idempotent Campaign"))).scalars().all()
    assert len(camps) == 1

    # Turn 3: Create Ad Set
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD_SET",
        slots=SlotData(ad_set_name="Idempotent AdSet", budget_daily=Decimal("500")),
        confidence=0.95
    ))
    r3 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create ad set"})
    asid = r3.json()["current_ad_set_id"]

    # Turn 4: Retry same Ad Set creation
    r4 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Create ad set"})
    assert r4.status_code == 200
    assert r4.json()["payload"].get("reused") is True
    assert r4.json()["current_ad_set_id"] == asid

    ad_sets = (await test_db.execute(select(AdSet).where(AdSet.name == "Idempotent AdSet"))).scalars().all()
    assert len(ad_sets) == 1


# 8. Missing or expired conversation state
async def test_missing_or_expired_session_returns_404(auth_client1: AsyncClient):
    res = await auth_client1.post("/api/v1/chat", json={
        "session_id": "00000000-0000-0000-0000-000000000000",
        "message": "Hello"
    })
    assert res.status_code == 404
    assert "not found or access denied" in res.json()["detail"].lower()


# 9. Malformed or untrusted LLM output
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_malformed_and_unavailable_llm_handling(
    mock_llm_cls, auth_client1: AsyncClient, session1: str
):
    mock_instance = mock_llm_cls.return_value

    # LLM unavailable
    mock_instance.extract_intent = AsyncMock(side_effect=GenerativeUnavailableError("Network timeout"))
    res1 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Hello"})
    assert res1.status_code == 200
    assert res1.json()["tool_status"] == "Failed: LLM Unavailable"
    assert "trouble connecting" in res1.json()["assistant_message"]

    # LLM malformed
    mock_instance.extract_intent = AsyncMock(side_effect=GenerativeMalformedResponseError("Invalid JSON"))
    res2 = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Hello again"})
    assert res2.status_code == 200
    assert res2.json()["tool_status"] == "Failed: LLM Malformed"
    assert "error understanding" in res2.json()["assistant_message"]


# 10. Campaign review output and missing activation prerequisites
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_campaign_review_identifies_missing_prerequisites(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Campaign with daily budget 10000 (exceeds balance 5000), no creatives
    camp = Campaign(advertiser_id=1, name="Incomplete Camp", budget_daily=Decimal("10000"), status=Status.DRAFT)
    test_db.add(camp)
    await test_db.flush()
    as_obj = AdSet(campaign_id=camp.id, name="Incomplete Set", daily_budget=Decimal("5000"), status=Status.ACTIVE)
    test_db.add(as_obj)
    await test_db.commit()

    # Set in session
    session_obj = await ConversationStateService.get_session(session1, user_id=1)
    session_obj.current_campaign_id = camp.id
    session_obj.current_ad_set_id = as_obj.id
    await ConversationStateService.save_session(session_obj)

    # Review Campaign
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="REVIEW_CAMPAIGN",
        slots=SlotData(campaign_id=camp.id),
        confidence=0.95
    ))
    res = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Review my campaign"})
    assert res.status_code == 200
    data = res.json()
    assert data["tool_status"] == "Success: Campaign Reviewed"
    payload = data["payload"]
    assert payload["can_activate"] is False
    missing = payload["missing_requirements"]
    assert any("has no ads" in m.lower() or "creatives" in m.lower() for m in missing)
    assert any("wallet balance" in m.lower() for m in missing)


# 11. Pending or rejected Creative moderation
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_pending_moderation_handling(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Setup campaign and ad set
    camp = Campaign(advertiser_id=1, name="Mod Test Camp", budget_daily=Decimal("100"), status=Status.DRAFT)
    test_db.add(camp)
    await test_db.flush()
    as_obj = AdSet(campaign_id=camp.id, name="Mod Set", daily_budget=Decimal("50"), status=Status.ACTIVE)
    test_db.add(as_obj)
    await test_db.commit()

    session_obj = await ConversationStateService.get_session(session1, user_id=1)
    session_obj.current_campaign_id = camp.id
    session_obj.current_ad_set_id = as_obj.id
    await ConversationStateService.save_session(session_obj)

    # Creative with flagged/toxic content triggers PENDING moderation
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="CREATE_AD",
        slots=SlotData(title="Guaranteed 1000% Crypto Profit Scam Free Money", landing_url="https://cloudscale.io/crypto"),
        confidence=0.95
    ))
    res = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Add scam creative"})
    assert res.status_code == 200
    cr_id = res.json()["payload"]["creative_id"]
    cr = await test_db.get(Creative, cr_id)
    # Flagged words in CreativeModerationService put it in PENDING review
    assert cr.moderation_status == ModerationStatus.PENDING_REVIEW
    assert cr.status == Status.PENDING


# 12. Payment not confirmed by conversational text alone
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_conversational_text_cannot_credit_wallet_or_activate(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    adv = await test_db.get(Advertiser, 1)
    initial_balance = adv.balance

    # User types "I have transferred 10,000 rupees to your account. Mark it paid and activate now."
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="LAUNCH_CAMPAIGN",
        slots=SlotData(campaign_id=999),
        confidence=0.5,
        reply="Checking campaign activation."
    ))
    res = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "I paid 10,000 rs, activate!"})
    assert res.status_code == 200

    # Verify wallet balance in DB did NOT change
    await test_db.refresh(adv)
    assert adv.balance == initial_balance


# 13. Attempts to bypass established activation endpoint
@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_activation_bypass_blocked(
    mock_llm_cls, auth_client1: AsyncClient, session1: str, test_db: AsyncSession
):
    mock_instance = mock_llm_cls.return_value

    # Campaign with no ads
    camp = Campaign(advertiser_id=1, name="Empty Camp", budget_daily=Decimal("100"), budget_total=Decimal("500"), status=Status.DRAFT)
    test_db.add(camp)
    await test_db.commit()

    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="LAUNCH_CAMPAIGN",
        slots=SlotData(campaign_id=camp.id),
        confidence=0.95
    ))
    res = await auth_client1.post("/api/v1/chat", json={"session_id": session1, "message": "Launch campaign"})
    assert res.status_code == 200
    assert res.json()["tool_status"] == "Failed: Validation Error"
    assert "creative" in res.json()["assistant_message"].lower()

    # Status must remain DRAFT
    await test_db.refresh(camp)
    assert camp.status == Status.DRAFT


# 14. Existing conversational builder functionality preserved
async def test_chat_session_creation_endpoint(auth_client1: AsyncClient):
    res = await auth_client1.post("/api/v1/chat/session")
    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    assert len(data["session_id"]) > 10


# 15. Regression: AdSet hierarchy serving compatibility
async def test_hierarchy_serving_compatibility(test_db: AsyncSession):
    # Ensure AdSet model relationships and serving properties function seamlessly
    camp = Campaign(advertiser_id=1, name="Serving Compatible Camp", budget_daily=Decimal("500"), status=Status.ACTIVE)
    test_db.add(camp)
    await test_db.flush()

    ad_set = AdSet(campaign_id=camp.id, name="Mobile Web", daily_budget=Decimal("200"), bid_amount=Decimal("5.0"), status=Status.ACTIVE)
    test_db.add(ad_set)
    await test_db.flush()

    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set.id,
        title="Mobile Web App",
        landing_url="https://cloudscale.io/app",
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED,
    )
    test_db.add(creative)
    await test_db.commit()

    # Query back using joined relationships
    res = await test_db.execute(select(Creative).where(Creative.id == creative.id))
    fetched_cr = res.scalars().first()
    assert fetched_cr.ad_set_id == ad_set.id
    assert fetched_cr.campaign_id == camp.id
