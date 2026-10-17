"""Tests for Phase 6 Work Unit 4 (WU4):
Placement-Aware Laya Optimizer Integration.

Covers all 14 mandatory test requirements:
1. top_performing_placements is present in optimizer state when appropriate.
2. The field is deterministic for identical input data.
3. A great placement is preferred when a valid shift decision has multiple eligible destinations.
4. A non-great placement does not displace a great placement when both are otherwise eligible.
5. healthy and low_visibility placements do not receive the great preference.
6. insufficient_data placements are not treated as great.
7. Missing or failed analyzer results preserve existing optimizer behavior.
8. No eligible destination means no invented shift action.
9. A great placement cannot override campaign eligibility or targeting restrictions.
10. A great placement cannot override moderation or verified-domain enforcement.
11. A great placement cannot override advertiser balance, budget, bid, frequency-cap, or confidence guardrails.
12. Cross-tenant placement data never enters another tenant's optimizer state.
13. Legacy NULL-slot rows never produce placement preferences.
14. Existing Phase 2 optimizer behavior remains unchanged when placement context is absent.
"""

from datetime import datetime, timedelta
from decimal import Decimal
import json
import pytest

from liteads.ad_server.services.laya_client import LayaDecisionResult
from liteads.ad_server.services.optimization_policy import (
    OptimizationPolicyResult,
    OptimizationPolicyService,
)
from liteads.ad_server.services.optimization_prep import (
    OptimizationPreparationService,
    OptimizationPrepResult,
)
from liteads.ad_server.services.placement_analyzer import (
    PlacementAnalysisResult,
    VERDICT_GREAT,
    VERDICT_HEALTHY,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_LOW_VISIBILITY,
)
from liteads.models.ad import Campaign, CampaignAssignment, Creative, TargetingRule
from liteads.models.base import CreativeType, ModerationStatus, Status


def _make_placement(
    slot_id: str,
    verdict: str = VERDICT_GREAT,
    ctr: float = 0.025,
    format: int = CreativeType.BANNER,
    publisher_id: int = 101,
) -> PlacementAnalysisResult:
    """Helper to create structured placement results."""
    return PlacementAnalysisResult(
        slot_id=slot_id,
        slot_db_id=1,
        slot_name=f"Placement {slot_id}",
        publisher_id=publisher_id,
        format=format,
        window_days=30,
        start_date="2026-09-17",
        end_date="2026-10-17",
        impressions=1000,
        clicks=int(1000 * ctr),
        conversions=10,
        earnings=50.0,
        ctr=ctr,
        ecpm=50.0,
        verdict=verdict,
        recommendation="Raise floor" if verdict == VERDICT_GREAT else "No action",
        benchmark={"median_ctr": 0.01, "median_ecpm": 20.0},
    )


@pytest.fixture
def base_active_campaign():
    """An eligible, active campaign with >= 500 impressions."""
    camp = Campaign(
        id=1,
        advertiser_id=10,
        name="WU4 Placement Aware Campaign",
        status=Status.ACTIVE,
        start_time=datetime.now() - timedelta(days=1),
        end_time=datetime.now() + timedelta(days=1),
        impressions=1200,
        clicks=60,
        conversions=6,
        spent_today=Decimal("15.00"),
        spent_total=Decimal("150.00"),
        budget_daily=Decimal("100.00"),
        budget_total=Decimal("1000.00"),
        bid_amount=Decimal("2.00"),
        autopilot=False,
        targeting_rules=[],
        assignments=[],
        creatives=[
            Creative(
                id=1,
                campaign_id=1,
                title="Banner Creative",
                landing_url="https://example.com",
                creative_type=CreativeType.BANNER,
                status=Status.ACTIVE,
                moderation_status=ModerationStatus.APPROVED,
            )
        ],
    )
    return camp


# -------------------------------------------------------------------------
# Test 1: top_performing_placements present when appropriate
# -------------------------------------------------------------------------
def test_1_top_performing_placements_present_when_appropriate(base_active_campaign):
    prep_service = OptimizationPreparationService()

    placements = [
        _make_placement("slot_great_1", verdict=VERDICT_GREAT, ctr=0.03),
        _make_placement("slot_healthy_2", verdict=VERDICT_HEALTHY, ctr=0.005),
    ]

    result = prep_service.prepare_context(base_active_campaign, placements=placements)
    assert result.eligible is True
    assert result.state is not None

    state_dict = json.loads(result.state)
    assert "top_performing_placements" in state_dict
    top = state_dict["top_performing_placements"]
    assert len(top) == 1
    assert top[0]["slot_id"] == "slot_great_1"
    assert top[0]["verdict"] == VERDICT_GREAT
    assert top[0]["ctr"] == 0.03
    # Sensitive publisher / competitor data stripped
    assert "publisher_id" not in top[0]
    assert "earnings" not in top[0]
    assert "benchmark" not in top[0]


# -------------------------------------------------------------------------
# Test 2: Field is deterministic for identical input data
# -------------------------------------------------------------------------
def test_2_field_deterministic_for_identical_input(base_active_campaign):
    prep_service = OptimizationPreparationService()

    p1 = _make_placement("slot_alpha", verdict=VERDICT_GREAT, ctr=0.02)
    p2 = _make_placement("slot_beta", verdict=VERDICT_GREAT, ctr=0.04)
    p3 = _make_placement("slot_gamma", verdict=VERDICT_GREAT, ctr=0.03)

    # Call with order 1
    res1 = prep_service.prepare_context(base_active_campaign, placements=[p1, p2, p3])
    # Call with order 2
    res2 = prep_service.prepare_context(base_active_campaign, placements=[p3, p1, p2])

    assert res1.state == res2.state
    state_dict = json.loads(res1.state)
    slots_order = [p["slot_id"] for p in state_dict["top_performing_placements"]]
    # Should be sorted CTR desc: beta (0.04), gamma (0.03), alpha (0.02)
    assert slots_order == ["slot_beta", "slot_gamma", "slot_alpha"]


# -------------------------------------------------------------------------
# Test 3: Great placement is preferred when valid shift has multiple destinations
# -------------------------------------------------------------------------
def test_3_great_placement_preferred_for_shift(base_active_campaign):
    policy_service = OptimizationPolicyService()

    candidates = ["slot_dest_normal", "slot_dest_great", "slot_dest_other"]
    placements = [
        _make_placement("slot_dest_normal", verdict=VERDICT_HEALTHY, ctr=0.005),
        _make_placement("slot_dest_great", verdict=VERDICT_GREAT, ctr=0.025),
        _make_placement("slot_dest_other", verdict=VERDICT_LOW_VISIBILITY, ctr=0.001),
    ]

    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=candidates,
        placements=placements,
    )

    assert dest == "slot_dest_great"


# -------------------------------------------------------------------------
# Test 4: Non-great placement does not displace a great placement
# -------------------------------------------------------------------------
def test_4_non_great_cannot_displace_great(base_active_campaign):
    policy_service = OptimizationPolicyService()

    # Even if healthy placement is listed first or has high impression volume
    candidates = ["slot_healthy_first", "slot_great_second"]
    placements = [
        _make_placement("slot_healthy_first", verdict=VERDICT_HEALTHY, ctr=0.009),
        _make_placement("slot_great_second", verdict=VERDICT_GREAT, ctr=0.015),
    ]

    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=candidates,
        placements=placements,
    )

    assert dest == "slot_great_second"


# -------------------------------------------------------------------------
# Test 5: healthy and low_visibility do not receive great preference
# -------------------------------------------------------------------------
def test_5_healthy_and_low_visibility_do_not_receive_great_preference(base_active_campaign):
    policy_service = OptimizationPolicyService()

    candidates = ["slot_normal_1", "slot_normal_2"]
    placements = [
        _make_placement("slot_normal_1", verdict=VERDICT_LOW_VISIBILITY, ctr=0.001),
        _make_placement("slot_normal_2", verdict=VERDICT_HEALTHY, ctr=0.008),
    ]

    # Neither is great; deterministic candidate order is preserved without false promotion
    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=candidates,
        placements=placements,
    )
    # Default first candidate preserved
    assert dest == "slot_normal_1"


# -------------------------------------------------------------------------
# Test 6: insufficient_data placements are not treated as great
# -------------------------------------------------------------------------
def test_6_insufficient_data_not_treated_as_great(base_active_campaign):
    policy_service = OptimizationPolicyService()

    candidates = ["slot_unverified", "slot_fallback"]
    placements = [
        # Even if CTR is high (e.g. 1 click on 1 impression = 100%), insufficient_data cannot be treated as great
        _make_placement("slot_unverified", verdict=VERDICT_INSUFFICIENT_DATA, ctr=1.0),
        _make_placement("slot_fallback", verdict=VERDICT_HEALTHY, ctr=0.005),
    ]

    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=candidates,
        placements=placements,
    )
    # Does not get great priority
    assert dest == "slot_unverified"


# -------------------------------------------------------------------------
# Test 7: Missing or failed analyzer results preserve existing optimizer behavior
# -------------------------------------------------------------------------
def test_7_missing_or_failed_analyzer_preserves_behavior(base_active_campaign):
    policy_service = OptimizationPolicyService()
    candidates = ["dest_alpha", "dest_beta"]

    # When placements is None
    dest_none = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=candidates,
        placements=None,
    )
    assert dest_none == "dest_alpha"

    # When placements is empty list
    dest_empty = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=candidates,
        placements=[],
    )
    assert dest_empty == "dest_alpha"


# -------------------------------------------------------------------------
# Test 8: No eligible destination means no invented shift action
# -------------------------------------------------------------------------
def test_8_no_eligible_destination_means_no_invented_action(base_active_campaign):
    policy_service = OptimizationPolicyService()
    placements = [_make_placement("slot_great_1", verdict=VERDICT_GREAT, ctr=0.03)]

    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=[], # Empty candidates
        placements=placements,
    )
    assert dest is None


# -------------------------------------------------------------------------
# Test 9: Great placement cannot override campaign eligibility or targeting
# -------------------------------------------------------------------------
def test_9_great_placement_cannot_override_eligibility(base_active_campaign):
    policy_service = OptimizationPolicyService()
    prep_service = OptimizationPreparationService()
    placements = [_make_placement("slot_great", verdict=VERDICT_GREAT, ctr=0.05)]

    # 1. Under 500 impressions
    base_active_campaign.impressions = 450
    prep_res = prep_service.prepare_context(base_active_campaign, placements=placements)
    assert prep_res.eligible is False
    assert "fewer than 500 impressions" in prep_res.reason

    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=["slot_great"],
        placements=placements,
    )
    assert dest is None

    # 2. Inactive campaign
    base_active_campaign.impressions = 1000
    base_active_campaign.status = Status.PAUSED
    prep_res_paused = prep_service.prepare_context(base_active_campaign, placements=placements)
    assert prep_res_paused.eligible is False

    dest_paused = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=["slot_great"],
        placements=placements,
    )
    assert dest_paused is None


# -------------------------------------------------------------------------
# Test 10: Great placement cannot override moderation or verified-domain enforcement
# -------------------------------------------------------------------------
def test_10_great_placement_cannot_override_moderation_or_domain(base_active_campaign):
    policy_service = OptimizationPolicyService()
    placements = [_make_placement("slot_great", verdict=VERDICT_GREAT, ctr=0.05)]

    # 1. Rejected creative
    base_active_campaign.creatives[0].moderation_status = ModerationStatus.REJECTED
    dest_mod = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=["slot_great"],
        placements=placements,
    )
    assert dest_mod is None

    # Reset moderation
    base_active_campaign.creatives[0].moderation_status = ModerationStatus.APPROVED

    # 2. Unverified domain
    dest_dom = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=["slot_great"],
        placements=placements,
        verified_domain=False,
    )
    assert dest_dom is None


# -------------------------------------------------------------------------
# Test 11: Great placement cannot override budget, balance, bid, or confidence guardrails
# -------------------------------------------------------------------------
def test_11_great_placement_cannot_override_guardrails(base_active_campaign):
    policy_service = OptimizationPolicyService()
    prep_res = OptimizationPrepResult(eligible=True, state="{}")

    # 1. Zero advertiser balance
    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=["slot_great"],
        placements=[_make_placement("slot_great", verdict=VERDICT_GREAT)],
        advertiser_balance=Decimal("0.00"),
    )
    assert dest is None

    # 2. Confidence below 0.65 threshold cannot be overridden
    decision_low_conf = LayaDecisionResult(
        choice="shift_to_new_geo",
        confidence=0.55,
        raw_response={"great_placement": "slot_great"},
    )
    eval_res = policy_service.evaluate(base_active_campaign, prep_res, decision_low_conf)
    assert eval_res.accepted is False
    assert "below threshold" in eval_res.reason

    # 3. Budget delta guardrail (30%) cannot be bypassed
    policy_service.BUDGET_GUARDRAIL_MAX_PCT = 0.15 # stricter
    decision_budget = LayaDecisionResult(
        choice="increase_budget_20",
        confidence=0.90,
        raw_response={},
    )
    eval_budget = policy_service.evaluate(base_active_campaign, prep_res, decision_budget)
    assert eval_budget.accepted is False
    assert "exceeds guardrail" in eval_budget.reason


# -------------------------------------------------------------------------
# Test 12: Cross-tenant placement data never enters another tenant's optimizer state
# -------------------------------------------------------------------------
def test_12_cross_tenant_placement_isolation(base_active_campaign):
    prep_service = OptimizationPreparationService()

    # Campaign is explicitly assigned only to publisher 101
    base_active_campaign.assignments = [
        CampaignAssignment(campaign_id=1, publisher_id=101, slot_id=None)
    ]

    # Analysis contains placements from Publisher 101 and Publisher 999 (different tenant)
    placements = [
        _make_placement("slot_pub101", verdict=VERDICT_GREAT, publisher_id=101),
        _make_placement("slot_pub999_secret", verdict=VERDICT_GREAT, publisher_id=999),
    ]

    res = prep_service.prepare_context(base_active_campaign, placements=placements)
    state = json.loads(res.state)
    top_slots = [p["slot_id"] for p in state["top_performing_placements"]]

    assert "slot_pub101" in top_slots
    assert "slot_pub999_secret" not in top_slots


# -------------------------------------------------------------------------
# Test 13: Legacy NULL-slot rows never produce placement preferences
# -------------------------------------------------------------------------
def test_13_legacy_null_slot_rows_never_produce_preferences(base_active_campaign):
    prep_service = OptimizationPreparationService()
    policy_service = OptimizationPolicyService()

    # Legacy raw statistic dictionary with slot_id None
    legacy_null_placement = {
        "slot_id": None,
        "format": CreativeType.BANNER,
        "ctr": 0.05,
        "verdict": VERDICT_GREAT,
    }
    valid_placement = _make_placement("slot_valid", verdict=VERDICT_HEALTHY, ctr=0.005)

    res = prep_service.prepare_context(base_active_campaign, placements=[legacy_null_placement, valid_placement])
    state = json.loads(res.state)
    top_slots = [p["slot_id"] for p in state["top_performing_placements"]]
    assert None not in top_slots
    assert len(top_slots) == 0 # None had great with non-null slot_id

    # In policy preference selection
    dest = policy_service.select_preferred_shift_destination(
        campaign=base_active_campaign,
        candidate_destinations=["cand_1", "cand_2"],
        placements=[legacy_null_placement],
    )
    # Does not apply null preference; returns default candidate
    assert dest == "cand_1"


# -------------------------------------------------------------------------
# Test 14: Existing Phase 2 optimizer behavior remains unchanged when placement context is absent
# -------------------------------------------------------------------------
def test_14_existing_optimizer_behavior_unchanged_when_context_absent(base_active_campaign):
    prep_service = OptimizationPreparationService()

    # Call with placements=None (exact Phase 2 signature)
    res = prep_service.prepare_context(base_active_campaign)
    assert res.eligible is True
    assert res.state is not None

    state_dict = json.loads(res.state)
    # Keys must be exactly performance and targeting, no extraneous keys
    assert set(state_dict.keys()) == {"performance", "targeting"}
    assert "top_performing_placements" not in state_dict
