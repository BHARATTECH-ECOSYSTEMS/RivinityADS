"""
Conversion attribution and measurement service for Phase 11.

Semantics:
- Validates and ingests conversion events from client pixels and S2S postbacks.
- Applies conservative last-touch (last-click) attribution within a 30-day window (2,592,000s) in UTC.
- Enforces strict deduplication on (campaign_id, external_order_id).
- Enforces CPA billing invariants (charges wallet only on attributed non-test conversions).
- Test events (is_test=True) never generate billable activity or contaminate production stats.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select, and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.billing import BillingService
from liteads.common.logger import get_logger
from liteads.common.cache import redis_client
from liteads.common.tracking_sig import verify_click_token, verify_conversion_sig
from liteads.models.ad import AdEvent, AdSet, Advertiser, Campaign, CampaignAssignment, Creative
from liteads.models.base import EventType
from liteads.models.publisher import AdSlot, Publisher

logger = get_logger(__name__)

ATTRIBUTION_WINDOW_SECONDS = 30 * 86400  # 30 days = 2,592,000 seconds
ISO_CURRENCY_REGEX = re.compile(r"^[A-Z]{3}$")


class ConversionResult:
    """Outcome of a conversion processing attempt."""

    def __init__(
        self,
        success: bool,
        event_id: int | None = None,
        is_attributed: bool = False,
        attribution_reason: str = "unattributed",
        campaign_id: int | None = None,
        creative_id: int | None = None,
        conversion_value: Decimal | None = None,
        currency: str = "INR",
        is_test: bool = False,
        is_duplicate: bool = False,
        message: str = "",
    ):
        self.success = success
        self.event_id = event_id
        self.is_attributed = is_attributed
        self.attribution_reason = attribution_reason
        self.campaign_id = campaign_id
        self.creative_id = creative_id
        self.conversion_value = conversion_value
        self.currency = currency
        self.is_test = is_test
        self.is_duplicate = is_duplicate
        self.message = message


class ConversionService:
    """Service handling conversion attribution, deduplication, and outcome measurement."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def track_conversion(
        self,
        *,
        click_token: str | None = None,
        request_id: str | None = None,
        campaign_id: int | None = None,
        creative_id: int | None = None,
        order_id: str | None = None,
        conversion_value: float | Decimal | None = None,
        currency: str = "INR",
        timestamp: int | None = None,
        user_id: str | None = None,
        signature: str | None = None,
        is_test: bool = False,
        ip_address: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> ConversionResult:
        """
        Process a conversion event from a client pixel or server-to-server postback.
        """
        now_utc = datetime.now(timezone.utc)
        event_dt = (
            datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
            if timestamp
            else now_utc.replace(tzinfo=None)
        )

        # 1. Validate currency code
        currency_clean = (currency or "INR").strip().upper()
        if not ISO_CURRENCY_REGEX.match(currency_clean):
            raise HTTPException(status_code=422, detail=f"Invalid ISO-4217 currency code: {currency}")

        # 2. Validate conversion value
        val_decimal: Decimal | None = None
        if conversion_value is not None:
            try:
                val_decimal = Decimal(str(conversion_value))
                if val_decimal < Decimal("0"):
                    raise HTTPException(status_code=422, detail="conversion_value must not be negative")
            except Exception as e:
                if isinstance(e, HTTPException):
                    raise
                raise HTTPException(status_code=422, detail=f"Invalid conversion_value: {conversion_value}")

        # 3. Handle click token if provided
        click_event: AdEvent | None = None
        is_attributed = False
        attribution_reason = "click_not_found"
        attributed_event_id: int | None = None
        resolved_campaign_id = campaign_id
        resolved_creative_id = creative_id
        resolved_ad_set_id: int | None = None

        if click_token:
            token_data = verify_click_token(click_token, max_age_seconds=ATTRIBUTION_WINDOW_SECONDS)
            token_req_id = token_data["request_id"]
            token_camp_id = token_data["campaign_id"]
            token_creat_id = token_data["creative_id"]
            token_within_window = token_data["is_within_window"]

            resolved_campaign_id = resolved_campaign_id or token_camp_id
            resolved_creative_id = resolved_creative_id or token_creat_id
            request_id = request_id or token_req_id

            # Verify campaign match
            if campaign_id is not None and campaign_id != token_camp_id:
                raise HTTPException(
                    status_code=400,
                    detail=f"Campaign ID mismatch: token has {token_camp_id}, payload has {campaign_id}",
                )

            # Query the database for the matching click event
            click_res = await self.session.execute(
                select(AdEvent)
                .where(AdEvent.request_id == token_req_id, AdEvent.event_type == EventType.CLICK)
                .order_by(AdEvent.id.desc())
                .limit(1)
            )
            click_event = click_res.scalar_one_or_none()

            if token_within_window:
                is_attributed = True
                attribution_reason = "last_click"
                if click_event:
                    attributed_event_id = click_event.id
                    resolved_ad_set_id = click_event.ad_set_id
            else:
                is_attributed = False
                attribution_reason = "outside_window"
                if click_event:
                    attributed_event_id = click_event.id
                    resolved_ad_set_id = click_event.ad_set_id

        elif request_id:
            # Query the database for click matching request_id
            click_res = await self.session.execute(
                select(AdEvent)
                .where(AdEvent.request_id == request_id, AdEvent.event_type == EventType.CLICK)
                .order_by(AdEvent.id.desc())
                .limit(1)
            )
            click_event = click_res.scalar_one_or_none()

            if click_event:
                resolved_campaign_id = resolved_campaign_id or click_event.campaign_id
                resolved_creative_id = resolved_creative_id or click_event.creative_id
                resolved_ad_set_id = click_event.ad_set_id
                attributed_event_id = click_event.id

                # Verify campaign match if both provided
                if campaign_id is not None and click_event.campaign_id is not None and campaign_id != click_event.campaign_id:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Campaign ID mismatch: click has {click_event.campaign_id}, payload has {campaign_id}",
                    )

                # Check 30-day attribution window (event_dt - click_dt)
                click_dt = click_event.event_time
                age_delta = event_dt - click_dt
                if timedelta(seconds=0) <= age_delta <= timedelta(seconds=ATTRIBUTION_WINDOW_SECONDS):
                    is_attributed = True
                    attribution_reason = "last_click"
                else:
                    is_attributed = False
                    attribution_reason = "outside_window"
            else:
                is_attributed = False
                attribution_reason = "click_not_found"
        else:
            is_attributed = False
            attribution_reason = "missing_click_identifier"

        # If campaign_id was not resolved from click or token, use what was supplied
        resolved_campaign_id = resolved_campaign_id or campaign_id

        # 4. Verify campaign exists if campaign_id is resolved
        campaign: Campaign | None = None
        if resolved_campaign_id:
            campaign = await self.session.get(Campaign, resolved_campaign_id)
            if not campaign:
                raise HTTPException(status_code=404, detail=f"Campaign {resolved_campaign_id} not found")

        # 5. Check order deduplication if external_order_id is provided
        clean_order_id = order_id.strip() if order_id else None
        if clean_order_id and not is_test and resolved_campaign_id:
            dedup_res = await self.session.execute(
                select(AdEvent).where(
                    AdEvent.campaign_id == resolved_campaign_id,
                    AdEvent.external_order_id == clean_order_id,
                    AdEvent.event_type == EventType.CONVERSION,
                    AdEvent.is_test == False,
                )
            )
            existing_conversion = dedup_res.scalar_one_or_none()
            if existing_conversion:
                logger.info(
                    "Duplicate conversion postback deduplicated",
                    campaign_id=resolved_campaign_id,
                    order_id=clean_order_id,
                    existing_event_id=existing_conversion.id,
                )
                return ConversionResult(
                    success=True,
                    event_id=existing_conversion.id,
                    is_attributed=existing_conversion.is_attributed,
                    attribution_reason="deduplicated_existing",
                    campaign_id=existing_conversion.campaign_id,
                    creative_id=existing_conversion.creative_id,
                    conversion_value=existing_conversion.conversion_value,
                    currency=existing_conversion.currency or currency_clean,
                    is_test=False,
                    is_duplicate=True,
                    message="Duplicate order conversion deduplicated idempotently",
                )

        # 6. Verify S2S Signature if provided
        if signature:
            ts_for_sig = timestamp or int(now_utc.timestamp())
            val_for_sig = float(val_decimal) if val_decimal is not None else 0.0
            verify_conversion_sig(
                campaign_id=resolved_campaign_id or 0,
                order_id=clean_order_id or "",
                value=val_for_sig,
                currency=currency_clean,
                timestamp=ts_for_sig,
                signature=signature,
            )

        # 7. Financial Invariant & CPA Billing
        cost = Decimal("0")
        if is_attributed and not is_test and campaign:
            # Check if CPA bidding (bid_type == 3)
            bid_type = campaign.bid_type
            bid_amount = campaign.bid_amount
            if resolved_ad_set_id:
                ad_set = await self.session.get(AdSet, resolved_ad_set_id)
                if ad_set and ad_set.bid_amount and Decimal(ad_set.bid_amount) > 0:
                    bid_type = ad_set.bid_type
                    bid_amount = ad_set.bid_amount

            if bid_type == 3:  # CPA
                cost = Decimal(bid_amount)
                # Find publisher to credit if click was associated with a slot
                publisher_id = None
                if click_event:
                    # Check extra or ad slot if stored
                    pass

                if campaign.advertiser_id and cost > 0:
                    ref_key = f"conv:{resolved_campaign_id}:{clean_order_id or request_id or int(now_utc.timestamp())}"
                    pub_id_to_bill = None
                    camp_assign = await self.session.execute(
                        select(CampaignAssignment).where(CampaignAssignment.campaign_id == campaign.id).limit(1)
                    )
                    assign = camp_assign.scalar_one_or_none()
                    if assign:
                        pub_id_to_bill = assign.publisher_id
                    else:
                        pub_res = await self.session.execute(select(Publisher).limit(1))
                        pub = pub_res.scalar_one_or_none()
                        if pub:
                            pub_id_to_bill = pub.id

                    if pub_id_to_bill:
                        bill_success = await BillingService.process_impression_charge(
                            self.session,
                            advertiser_id=campaign.advertiser_id,
                            campaign_id=campaign.id,
                            publisher_id=pub_id_to_bill,
                            cost=cost,
                            reference_id=ref_key,
                        )
                        if not bill_success:
                            logger.warning(
                                "Billing failed for CPA conversion: insufficient funds",
                                campaign_id=campaign.id,
                                cost=cost,
                            )
                            raise HTTPException(
                                status_code=402,
                                detail="Insufficient advertiser balance for CPA conversion charge",
                            )
                    else:
                        # Direct advertiser charge when no publisher is in system
                        adv = await self.session.get(Advertiser, campaign.advertiser_id, with_for_update=True)
                        if not adv or adv.balance < cost:
                            raise HTTPException(
                                status_code=402,
                                detail="Insufficient advertiser balance for CPA conversion charge",
                            )
                        adv.balance -= cost
                        campaign.spent_today += cost
                        campaign.spent_total += cost

        # 8. Insert conversion AdEvent
        gen_request_id = request_id or f"conv_{clean_order_id or now_utc.strftime('%Y%m%d%H%M%S%f')}"
        logical_key = f"conv:{resolved_campaign_id or 0}:{clean_order_id or gen_request_id}:{int(event_dt.timestamp())}"

        conversion_event = AdEvent(
            request_id=gen_request_id,
            campaign_id=resolved_campaign_id,
            ad_set_id=resolved_ad_set_id,
            creative_id=resolved_creative_id,
            event_type=EventType.CONVERSION,
            event_time=event_dt,
            user_id=user_id,
            ip_address=ip_address,
            cost=cost,
            idempotency_key=logical_key,
            conversion_value=val_decimal,
            currency=currency_clean,
            is_attributed=is_attributed,
            attribution_reason=attribution_reason,
            attributed_event_id=attributed_event_id,
            external_order_id=clean_order_id,
            is_test=is_test,
        )

        try:
            self.session.add(conversion_event)
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            if clean_order_id and resolved_campaign_id:
                dedup_res = await self.session.execute(
                    select(AdEvent).where(
                        AdEvent.campaign_id == resolved_campaign_id,
                        AdEvent.external_order_id == clean_order_id,
                        AdEvent.event_type == EventType.CONVERSION,
                        AdEvent.is_test == False,
                    )
                )
                existing_conversion = dedup_res.scalar_one_or_none()
                if existing_conversion:
                    logger.info(
                        "Concurrent duplicate conversion postback deduplicated via constraint",
                        campaign_id=resolved_campaign_id,
                        order_id=clean_order_id,
                        existing_event_id=existing_conversion.id,
                    )
                    return ConversionResult(
                        success=True,
                        event_id=existing_conversion.id,
                        is_attributed=existing_conversion.is_attributed,
                        attribution_reason="deduplicated_existing",
                        campaign_id=existing_conversion.campaign_id,
                        creative_id=existing_conversion.creative_id,
                        conversion_value=existing_conversion.conversion_value,
                        currency=existing_conversion.currency or currency_clean,
                        is_test=False,
                        is_duplicate=True,
                        message="Duplicate order conversion deduplicated idempotently (concurrent race resolved)",
                    )
            raise

        # 9. Update campaign stats only if non-test
        if not is_test and campaign:
            campaign.conversions += 1
            if resolved_creative_id:
                creat = await self.session.get(Creative, resolved_creative_id)
                if creat:
                    creat.conversions += 1

        return ConversionResult(
            success=True,
            event_id=conversion_event.id,
            is_attributed=is_attributed,
            attribution_reason=attribution_reason,
            campaign_id=resolved_campaign_id,
            creative_id=resolved_creative_id,
            conversion_value=val_decimal,
            currency=currency_clean,
            is_test=is_test,
            is_duplicate=False,
            message=f"Conversion processed: {attribution_reason}",
        )
