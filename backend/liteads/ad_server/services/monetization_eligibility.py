"""Authoritative monetization eligibility engine (Phase 10F).

Single authoritative backend check evaluating all prerequisites before enabling monetization:
1. Publisher account exists and is ACTIVE.
2. Publisher has configured active payout details.
3. Linked property exists, is ACTIVE, and is VERIFIED (verification_status == 'VERIFIED').
4. All ad placements in the project map to authorized, active AdSlots owned by the property.
5. Explicit publisher monetization consent has been recorded and accepted.
6. Website project is not suspended or archived.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models.base import Status
from liteads.models.publisher import AdSlot, Publisher, PublisherProperty
from liteads.models.website import (
    WebsiteMonetizationStatus,
    WebsiteProject,
    WebsiteStatus,
)


@dataclass
class MonetizationEligibilityResult:
    is_eligible: bool
    current_status: int
    prerequisites: dict[str, bool]
    failure_reasons: list[str]
    slot_count: int


async def evaluate_monetization_eligibility(
    session: AsyncSession,
    project: WebsiteProject,
) -> MonetizationEligibilityResult:
    """Evaluate all required conditions before live monetization can be activated."""
    prereqs: dict[str, bool] = {
        "project_active": False,
        "publisher_active": False,
        "payout_configured": False,
        "property_verified": False,
        "slots_authorized": False,
        "consent_recorded": False,
    }
    reasons: list[str] = []

    # 1. Project lifecycle check
    if project.status == WebsiteStatus.SUSPENDED:
        reasons.append("Website project is suspended.")
    elif project.is_deleted or project.status == WebsiteStatus.ARCHIVED:
        reasons.append("Website project is archived or deleted.")
    else:
        prereqs["project_active"] = True

    # 2. Publisher account check
    publisher: Publisher | None = None
    if not project.publisher_id:
        reasons.append("Website project is not linked to a publisher account.")
    else:
        publisher = await session.get(Publisher, project.publisher_id)
        if publisher is None:
            reasons.append(f"Linked publisher account #{project.publisher_id} not found.")
        elif publisher.status != Status.ACTIVE:
            reasons.append("Linked publisher account is inactive or suspended.")
        else:
            prereqs["publisher_active"] = True

    # 3. Payout configuration check
    if publisher and publisher.payout_details and isinstance(publisher.payout_details, dict):
        pd = publisher.payout_details
        has_details = any(
            bool(pd.get(k))
            for k in ("account_number", "upi_id", "bank_name", "payout_account_id", "email")
        ) or pd.get("status") == "active"
        if has_details:
            prereqs["payout_configured"] = True
        else:
            reasons.append("Publisher payout account details are incomplete.")
    else:
        reasons.append("Publisher has not configured payout details (bank account or UPI required).")

    # 4. Property verification check
    property_obj: PublisherProperty | None = None
    if not project.property_id:
        reasons.append("Website project is not linked to a publisher property.")
    else:
        property_obj = await session.get(PublisherProperty, project.property_id)
        if property_obj is None:
            reasons.append(f"Linked publisher property #{project.property_id} not found.")
        elif property_obj.status != Status.ACTIVE:
            reasons.append("Linked publisher property is inactive or suspended.")
        elif property_obj.verification_status != "VERIFIED":
            reasons.append(
                f"Publisher property '{property_obj.domain}' is not verified (status: {property_obj.verification_status})."
            )
        else:
            prereqs["property_verified"] = True

    # 5. Ad slot authorization check
    bound_slots: list[str] = []
    for page in project.pages:
        blocks_data = page.blocks or {}
        if isinstance(blocks_data, dict):
            for blk in blocks_data.get("blocks", []):
                if isinstance(blk, dict) and blk.get("type") == "ad_placement":
                    content = blk.get("content") or {}
                    sid = content.get("ad_slot_id")
                    if sid and sid not in bound_slots:
                        bound_slots.append(sid)

    if not bound_slots:
        reasons.append("No ad placements with bound ad slots exist in the website project.")
    else:
        slots_ok = True
        if project.publisher_id and project.property_id:
            for sid in bound_slots:
                stmt = select(AdSlot).where(AdSlot.slot_id == sid)
                s_res = await session.execute(stmt)
                slot_obj = s_res.scalar_one_or_none()
                if slot_obj is None:
                    reasons.append(f"Bound AdSlot '{sid}' does not exist.")
                    slots_ok = False
                elif slot_obj.publisher_id != project.publisher_id or slot_obj.property_id != project.property_id:
                    reasons.append(f"AdSlot '{sid}' belongs to another publisher or property.")
                    slots_ok = False
                elif slot_obj.status != Status.ACTIVE:
                    reasons.append(f"AdSlot '{sid}' is not active.")
                    slots_ok = False
        else:
            slots_ok = False
        prereqs["slots_authorized"] = slots_ok

    # 6. Monetization consent check
    consent = project.monetization_consent
    if isinstance(consent, dict) and consent.get("accepted_terms") is True and not consent.get("revoked_at"):
        prereqs["consent_recorded"] = True
    else:
        reasons.append("Explicit publisher monetization terms have not been accepted.")

    all_eligible = all(prereqs.values())
    return MonetizationEligibilityResult(
        is_eligible=all_eligible,
        current_status=project.monetization_status,
        prerequisites=prereqs,
        failure_reasons=reasons,
        slot_count=len(bound_slots),
    )
