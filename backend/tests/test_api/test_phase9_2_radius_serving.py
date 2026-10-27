"""
Phase 9.2: End-to-End Candidate Retrieval and Serving Integration Tests for Radius Targeting.

Validates:
1. Full retrieval pipeline (`TargetingRetrieval.retrieve`) evaluates radius targeting against real database models.
2. Users matching radius geofence with consented GPS retrieve active candidates.
3. Users outside radius or with IP centroids are strictly filtered out.
4. Users with unconsented GPS or excessive location uncertainty are filtered out.
5. Inactive campaigns, paused ad sets, and unapproved creatives are barred even if the user location matches.
6. Sibling ad sets with mixed administrative and radius targeting remain strictly isolated.
"""

from decimal import Decimal
import pytest
from liteads.models import (
    Advertiser,
    Campaign,
    AdSet,
    Creative,
    ModerationStatus,
    Status,
    TargetingRule,
)
from liteads.rec_engine.retrieval.targeting import TargetingRetrieval
from liteads.schemas.internal import UserContext

pytestmark = pytest.mark.asyncio


async def test_radius_candidate_retrieval_e2e(test_db):
    """
    Test real candidate retrieval for an ad set targeting a 5km radius in Mumbai.
    """
    # 1. Setup Advertiser and Campaign
    adv = Advertiser(name="Radius Adv", balance=Decimal("1000.00"), status=1)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Hyperlocal Coffee Campaign",
        status=Status.ACTIVE,
        budget_daily=Decimal("200.00"),
        bid_type=1,
        bid_amount=Decimal("1.50"),
        spent_today=Decimal("0.00"),
        spent_total=Decimal("0.00"),
    )
    test_db.add(camp)
    await test_db.flush()

    # 2. Setup AdSet
    ad_set = AdSet(
        campaign_id=camp.id,
        name="Bandra Cafe 5km Radius",
        status=Status.ACTIVE,
        daily_budget=Decimal("100.00"),
        bid_type=1,
        bid_amount=Decimal("1.50"),
    )
    test_db.add(ad_set)
    await test_db.flush()

    # 3. Setup Radius Targeting Rule (5,000m around Bandra: 19.0596, 72.8295)
    bandra_lat, bandra_lon = 19.0596, 72.8295
    rule = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set.id,
        rule_type="geo",
        is_include=True,
        rule_value={
            "entries": [
                {
                    "latitude": bandra_lat,
                    "longitude": bandra_lon,
                    "radius_meters": 5000.0,
                    "country": "IN",
                    "name": "Bandra Store",
                }
            ]
        },
    )
    test_db.add(rule)

    # 4. Setup Active Approved Creative
    creative = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set.id,
        title="20% Off at Bandra Coffee",
        landing_url="https://example.com/bandra",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(creative)
    await test_db.commit()

    retrieval = TargetingRetrieval(test_db)
    await retrieval.refresh()

    # Scenario A: User 1km away in Bandra with consented GPS -> Candidate MATCHES
    ctx_match = UserContext(
        country="IN",
        latitude=19.0650,  # ~600m north
        longitude=72.8295,
        location_source="device_gps",
        location_accuracy_meters=20.0,
        location_consent=True,
    )
    candidates = await retrieval.retrieve(user_context=ctx_match, slot_id="banner_slot")
    assert len(candidates) == 1
    assert candidates[0].creative_id == creative.id
    assert candidates[0].ad_set_id == ad_set.id
    assert candidates[0].campaign_id == camp.id

    # Scenario B: User 15km away in South Mumbai -> Outside radius -> NO CANDIDATE
    ctx_outside = UserContext(
        country="IN",
        latitude=18.9220,  # Colaba, ~15km south
        longitude=72.8347,
        location_source="device_gps",
        location_accuracy_meters=20.0,
        location_consent=True,
    )
    candidates_outside = await retrieval.retrieve(user_context=ctx_outside, slot_id="banner_slot")
    assert len(candidates_outside) == 0

    # Scenario C: User in Bandra with IP-derived centroid -> IP barred -> NO CANDIDATE
    ctx_ip = UserContext(
        country="IN",
        latitude=bandra_lat,
        longitude=bandra_lon,
        location_source="ip",
        location_accuracy_meters=10000.0,
        location_consent=False,
    )
    candidates_ip = await retrieval.retrieve(user_context=ctx_ip, slot_id="banner_slot")
    assert len(candidates_ip) == 0

    # Scenario D: User in Bandra with GPS but NO consent -> NO CANDIDATE
    ctx_no_consent = UserContext(
        country="IN",
        latitude=bandra_lat,
        longitude=bandra_lon,
        location_source="device_gps",
        location_accuracy_meters=20.0,
        location_consent=False,
    )
    candidates_no_consent = await retrieval.retrieve(user_context=ctx_no_consent, slot_id="banner_slot")
    assert len(candidates_no_consent) == 0

    # Scenario E: User in Bandra with excessive uncertainty (10km > 5km radius) -> NO CANDIDATE
    ctx_uncertain = UserContext(
        country="IN",
        latitude=bandra_lat,
        longitude=bandra_lon,
        location_source="device_gps",
        location_accuracy_meters=10000.0,
        location_consent=True,
    )
    candidates_uncertain = await retrieval.retrieve(user_context=ctx_uncertain, slot_id="banner_slot")
    assert len(candidates_uncertain) == 0


async def test_radius_lifecycle_and_moderation_guards(test_db):
    """
    Status and moderation guards MUST NOT be bypassed by radius targeting matches.
    """
    adv = Advertiser(name="Guard Adv", balance=Decimal("500.00"), status=1)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Guard Campaign",
        status=Status.ACTIVE,
        budget_daily=Decimal("100.00"),
        bid_type=1,
        bid_amount=Decimal("1.00"),
        spent_today=Decimal("0.00"),
        spent_total=Decimal("0.00"),
    )
    test_db.add(camp)
    await test_db.flush()

    # Paused AdSet
    ad_set_paused = AdSet(
        campaign_id=camp.id,
        name="Paused AdSet",
        status=Status.PAUSED,  # PAUSED
        daily_budget=Decimal("50.00"),
        bid_type=1,
        bid_amount=Decimal("1.00"),
    )
    test_db.add(ad_set_paused)
    await test_db.flush()

    # Active Creative in Paused AdSet
    rule = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set_paused.id,
        rule_type="geo",
        is_include=True,
        rule_value={"entries": [{"latitude": 19.0, "longitude": 72.0, "radius_meters": 5000.0}]},
    )
    test_db.add(rule)

    creative_paused = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set_paused.id,
        title="Paused Ad",
        landing_url="https://example.com/p",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(creative_paused)

    # Active AdSet with Unapproved Creative
    ad_set_active = AdSet(
        campaign_id=camp.id,
        name="Active AdSet",
        status=Status.ACTIVE,
        daily_budget=Decimal("50.00"),
        bid_type=1,
        bid_amount=Decimal("1.00"),
    )
    test_db.add(ad_set_active)
    await test_db.flush()

    rule_active = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set_active.id,
        rule_type="geo",
        is_include=True,
        rule_value={"entries": [{"latitude": 19.0, "longitude": 72.0, "radius_meters": 5000.0}]},
    )
    test_db.add(rule_active)

    creative_pending = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set_active.id,
        title="Pending Ad",
        landing_url="https://example.com/pending",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.PENDING_REVIEW.value,  # PENDING MODERATION
    )
    test_db.add(creative_pending)
    await test_db.commit()

    retrieval = TargetingRetrieval(test_db)
    await retrieval.refresh()

    ctx = UserContext(
        latitude=19.0,
        longitude=72.0,
        location_source="device_gps",
        location_consent=True,
    )
    candidates = await retrieval.retrieve(user_context=ctx, slot_id="slot_guard")
    # Neither the paused ad set creative nor the pending creative should be returned
    assert len(candidates) == 0


async def test_multi_ad_set_mixed_targeting_isolation(test_db):
    """
    Campaign with AdSet 1 (Administrative: Bangalore) and AdSet 2 (Radius: Mumbai 2km).
    Users are retrieved strictly by their matching ad set.
    """
    adv = Advertiser(name="Multi Adv", balance=Decimal("1000.00"), status=1)
    test_db.add(adv)
    await test_db.flush()

    camp = Campaign(
        advertiser_id=adv.id,
        name="Multi Region Campaign",
        status=Status.ACTIVE,
        budget_daily=Decimal("500.00"),
        bid_type=1,
        bid_amount=Decimal("2.00"),
        spent_today=Decimal("0.00"),
        spent_total=Decimal("0.00"),
    )
    test_db.add(camp)
    await test_db.flush()

    # AdSet 1: Bengaluru, KA
    ad_set_1 = AdSet(
        campaign_id=camp.id,
        name="Bengaluru Admin Set",
        status=Status.ACTIVE,
        daily_budget=Decimal("250.00"),
        bid_type=1,
        bid_amount=Decimal("2.00"),
    )
    test_db.add(ad_set_1)

    # AdSet 2: Mumbai BKC 2km
    ad_set_2 = AdSet(
        campaign_id=camp.id,
        name="Mumbai BKC Radius Set",
        status=Status.ACTIVE,
        daily_budget=Decimal("250.00"),
        bid_type=1,
        bid_amount=Decimal("2.00"),
    )
    test_db.add(ad_set_2)
    await test_db.flush()

    # Rule 1: Administrative Bangalore
    r1 = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set_1.id,
        rule_type="geo",
        is_include=True,
        rule_value={"entries": [{"country": "IN", "state": "KA", "city": "Bengaluru"}]},
    )
    test_db.add(r1)

    # Rule 2: Radius BKC (19.0674, 72.8687, 2000m)
    r2 = TargetingRule(
        campaign_id=camp.id,
        ad_set_id=ad_set_2.id,
        rule_type="geo",
        is_include=True,
        rule_value={"entries": [{"latitude": 19.0674, "longitude": 72.8687, "radius_meters": 2000.0}]},
    )
    test_db.add(r2)

    # Creatives
    cr1 = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set_1.id,
        title="Ad Bengaluru",
        landing_url="https://example.com/blr",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(cr1)

    cr2 = Creative(
        campaign_id=camp.id,
        ad_set_id=ad_set_2.id,
        title="Ad Mumbai BKC",
        landing_url="https://example.com/bkc",
        creative_type=1,
        width=300,
        height=250,
        status=Status.ACTIVE,
        moderation_status=ModerationStatus.APPROVED.value,
    )
    test_db.add(cr2)
    await test_db.commit()

    retrieval = TargetingRetrieval(test_db)
    await retrieval.refresh()

    # 1. User in Bengaluru with IP-derived geo (no GPS) -> AdSet 1 retrieved
    ctx_blr = UserContext(
        country="IN",
        region="KA",
        city="Bengaluru",
        location_source="ip",
        location_consent=False,
    )
    cands_blr = await retrieval.retrieve(user_context=ctx_blr, slot_id="slot_mixed")
    assert len(cands_blr) == 1
    assert cands_blr[0].ad_set_id == ad_set_1.id
    assert cands_blr[0].creative_id == cr1.id

    # 2. User in Mumbai BKC with GPS -> AdSet 2 retrieved
    ctx_bkc = UserContext(
        country="IN",
        region="MH",
        city="Mumbai",
        latitude=19.0675,
        longitude=72.8688,
        location_source="device_gps",
        location_consent=True,
    )
    cands_bkc = await retrieval.retrieve(user_context=ctx_bkc, slot_id="slot_mixed")
    assert len(cands_bkc) == 1
    assert cands_bkc[0].ad_set_id == ad_set_2.id
    assert cands_bkc[0].creative_id == cr2.id

    # 3. User in Pune -> Neither retrieved
    ctx_pune = UserContext(
        country="IN",
        region="MH",
        city="Pune",
        location_source="ip",
        location_consent=False,
    )
    cands_pune = await retrieval.retrieve(user_context=ctx_pune, slot_id="slot_mixed")
    assert len(cands_pune) == 0
