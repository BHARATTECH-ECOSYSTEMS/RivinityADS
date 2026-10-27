from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from liteads.common.database import get_session
from liteads.common.security import current_user, verified_user
from liteads.common.logger import get_logger
from liteads.models.auth import User
from liteads.models.ad import Campaign, TargetingRule, AdSet, Creative, Status

logger = get_logger(__name__)
from liteads.schemas.targeting import TargetingSet, GeoEntry
from liteads.ad_server.services.conversation_state import ConversationStateService
from liteads.ad_server.services.generative_intent_client import (
    GenerativeIntentClient,
    GenerativeUnavailableError,
    GenerativeMalformedResponseError
)
from liteads.ad_server.services.tools import (
    create_draft_campaign,
    select_campaign,
    update_campaign,
    create_draft_ad_set,
    select_ad_set,
    update_ad_set,
    create_ad,
    review_campaign,
    update_targeting,
    request_payment,
    activate_campaign,
    ToolValidationError,
    ToolAuthorizationError,
    ToolError
)

router = APIRouter(dependencies=[Depends(verified_user)])

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(BaseModel):
    session_id: str
    assistant_message: str
    intent: str
    tool_status: str | None = None
    payload: dict | None = None
    current_campaign_id: int | None = None
    current_ad_set_id: int | None = None
    current_step: str | None = None

class ChatSessionResponse(BaseModel):
    session_id: str

@router.post("/chat/session", response_model=ChatSessionResponse)
async def create_chat_session(
    user: User = Depends(current_user)
):
    if user.role != "advertiser" or not user.advertiser_id:
        raise HTTPException(status_code=403, detail="Only advertisers can create chat sessions")
    session = await ConversationStateService.create_session(user_id=user.id, advertiser_id=user.advertiser_id)
    return ChatSessionResponse(session_id=session.session_id)

@router.post("/chat", response_model=ChatResponse)
async def process_chat(
    request: ChatRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session)
):
    chat_session = await ConversationStateService.get_session(request.session_id, user.id)
    if not chat_session:
        raise HTTPException(status_code=404, detail="Chat session not found or access denied")

    await ConversationStateService.append_message(chat_session, "user", request.message)

    client = GenerativeIntentClient()
    
    try:
        intent_result = await client.extract_intent(request.message, chat_session)
    except GenerativeUnavailableError:
        err_msg = "I'm having trouble connecting to my processing core right now. Please try again."
        await ConversationStateService.append_message(chat_session, "assistant", err_msg)
        await ConversationStateService.save_session(chat_session)
        return ChatResponse(
            session_id=request.session_id,
            assistant_message=err_msg,
            intent="UNKNOWN",
            tool_status="Failed: LLM Unavailable",
            current_campaign_id=chat_session.current_campaign_id,
            current_ad_set_id=chat_session.current_ad_set_id,
            current_step=chat_session.current_step,
        )
    except GenerativeMalformedResponseError:
        err_msg = "I encountered an error understanding your request. Could you rephrase?"
        await ConversationStateService.append_message(chat_session, "assistant", err_msg)
        await ConversationStateService.save_session(chat_session)
        return ChatResponse(
            session_id=request.session_id,
            assistant_message=err_msg,
            intent="UNKNOWN",
            tool_status="Failed: LLM Malformed",
            current_campaign_id=chat_session.current_campaign_id,
            current_ad_set_id=chat_session.current_ad_set_id,
            current_step=chat_session.current_step,
        )

    tool_status = None
    payload = None
    assistant_reply = intent_result.reply or "Processing your request."

    try:
        if intent_result.intent == "CREATE_CAMPAIGN":
            name = intent_result.slots.name or "Untitled Campaign"
            reused = False
            if chat_session.current_campaign_id:
                existing_c = await session.get(Campaign, chat_session.current_campaign_id)
                if existing_c and existing_c.advertiser_id == user.advertiser_id and existing_c.name == name:
                    tool_status = "Success: Campaign Retained"
                    payload = {"campaign_id": existing_c.id, "reused": True}
                    reused = True

            if not reused:
                res = await create_draft_campaign(
                    user=user,
                    session=session,
                    name=name,
                    budget_daily=intent_result.slots.budget_daily,
                    budget_total=intent_result.slots.budget_total
                )
                chat_session.current_campaign_id = res.campaign_id
                chat_session.current_ad_set_id = None
                chat_session.current_step = "AD_SET"
                tool_status = "Success: Campaign Created"
                payload = {"campaign_id": res.campaign_id}

        elif intent_result.intent == "SELECT_CAMPAIGN":
            target_campaign_id = intent_result.slots.campaign_id
            if not target_campaign_id:
                raise ToolValidationError("Please provide a valid campaign ID to select.")
            res = await select_campaign(user=user, session=session, campaign_id=target_campaign_id)
            chat_session.current_campaign_id = res.campaign_id
            chat_session.current_ad_set_id = res.default_ad_set_id
            chat_session.current_step = "AD_SET" if not res.default_ad_set_id else "CREATIVE"
            tool_status = "Success: Campaign Selected"
            payload = {
                "campaign_id": res.campaign_id,
                "name": res.name,
                "status": res.status,
                "default_ad_set_id": res.default_ad_set_id,
                "ad_sets_count": res.ad_sets_count
            }

        elif intent_result.intent == "UPDATE_CAMPAIGN":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            if not campaign_id:
                raise ToolValidationError("Please select or create a campaign first.")
            res = await update_campaign(
                user=user,
                session=session,
                campaign_id=campaign_id,
                name=intent_result.slots.name,
                budget_daily=intent_result.slots.budget_daily,
                budget_total=intent_result.slots.budget_total
            )
            tool_status = "Success: Campaign Updated"
            payload = {"campaign_id": res.campaign_id, "updated_fields": res.updated_fields}

        elif intent_result.intent == "CREATE_AD_SET":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            if not campaign_id:
                raise ToolValidationError("Please select or create a campaign before creating an ad set.")
            name = intent_result.slots.ad_set_name or intent_result.slots.name or "Default Ad Set"

            reused = False
            if chat_session.current_ad_set_id:
                existing_as = await session.get(AdSet, chat_session.current_ad_set_id)
                if existing_as and existing_as.campaign_id == campaign_id and existing_as.name == name:
                    tool_status = "Success: Ad Set Retained"
                    payload = {"campaign_id": campaign_id, "ad_set_id": existing_as.id, "reused": True}
                    reused = True

            if not reused:
                res = await create_draft_ad_set(
                    user=user,
                    session=session,
                    campaign_id=campaign_id,
                    name=name,
                    daily_budget=intent_result.slots.budget_daily,
                    bid_amount=intent_result.slots.bid_amount,
                    bid_type=intent_result.slots.bid_type
                )
                chat_session.current_campaign_id = campaign_id
                chat_session.current_ad_set_id = res.ad_set_id
                chat_session.current_step = "CREATIVE"
                tool_status = "Success: Ad Set Created"
                payload = {"campaign_id": campaign_id, "ad_set_id": res.ad_set_id, "name": res.name}

        elif intent_result.intent == "SELECT_AD_SET":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            if not campaign_id:
                raise ToolValidationError("Please select or create a campaign first.")
            res = await select_ad_set(
                user=user,
                session=session,
                campaign_id=campaign_id,
                ad_set_id=intent_result.slots.ad_set_id,
                ad_set_name=intent_result.slots.ad_set_name or intent_result.slots.name
            )
            chat_session.current_ad_set_id = res.ad_set_id
            tool_status = "Success: Ad Set Selected"
            payload = {
                "campaign_id": campaign_id,
                "ad_set_id": res.ad_set_id,
                "name": res.name,
                "ads_count": res.ads_count
            }

        elif intent_result.intent == "UPDATE_AD_SET":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            ad_set_id = intent_result.slots.ad_set_id or chat_session.current_ad_set_id
            if not campaign_id:
                raise ToolValidationError("Please select or create a campaign first.")
            if not ad_set_id:
                raise ToolValidationError("Please select or create an ad set first.")
            res = await update_ad_set(
                user=user,
                session=session,
                campaign_id=campaign_id,
                ad_set_id=ad_set_id,
                name=intent_result.slots.ad_set_name or intent_result.slots.name,
                daily_budget=intent_result.slots.budget_daily,
                bid_amount=intent_result.slots.bid_amount,
                bid_type=intent_result.slots.bid_type,
                freq_cap_daily=intent_result.slots.freq_cap_daily,
                freq_cap_hourly=intent_result.slots.freq_cap_hourly,
                start_time=intent_result.slots.start_time,
                end_time=intent_result.slots.end_time
            )
            tool_status = "Success: Ad Set Updated"
            payload = {"campaign_id": campaign_id, "ad_set_id": res.ad_set_id, "updated_fields": res.updated_fields}

        elif intent_result.intent == "SET_BUDGET":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            if not campaign_id:
                raise ToolValidationError("Please create or select a campaign first.")
            
            if chat_session.current_ad_set_id and intent_result.slots.budget_total is None and chat_session.current_step in ["AD_SET", "CREATIVE"]:
                res = await update_ad_set(
                    user=user,
                    session=session,
                    campaign_id=campaign_id,
                    ad_set_id=chat_session.current_ad_set_id,
                    daily_budget=intent_result.slots.budget_daily
                )
                tool_status = "Success: Ad Set Budget Updated"
                payload = {"campaign_id": campaign_id, "ad_set_id": res.ad_set_id, "updated_fields": res.updated_fields}
            else:
                res = await update_campaign(
                    user=user,
                    session=session,
                    campaign_id=campaign_id,
                    budget_daily=intent_result.slots.budget_daily,
                    budget_total=intent_result.slots.budget_total
                )
                tool_status = "Success: Budget Updated"
                payload = {"campaign_id": res.campaign_id, "updated_fields": res.updated_fields}

        elif intent_result.intent in ["SET_GEO", "SET_SECTOR"]:
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            if not campaign_id:
                raise ToolValidationError("Please create or select a campaign first.")
            target_as_id = intent_result.slots.ad_set_id or chat_session.current_ad_set_id

            if target_as_id:
                query = select(TargetingRule).where(
                    TargetingRule.campaign_id == campaign_id,
                    TargetingRule.ad_set_id == target_as_id
                )
            else:
                query = select(TargetingRule).where(
                    TargetingRule.campaign_id == campaign_id,
                    TargetingRule.ad_set_id.is_(None)
                )
            db_rules = await session.execute(query)
            rules = db_rules.scalars().all()

            geo_inc, geo_exc = [], []
            sec_inc, sec_exc = [], []

            for r in rules:
                if r.rule_type == "geo":
                    entries = [GeoEntry(**e) for e in r.rule_value.get("entries", [])]
                    if r.is_include: geo_inc.extend(entries)
                    else: geo_exc.extend(entries)
                elif r.rule_type == "sector":
                    sectors = r.rule_value.get("sectors", [])
                    if r.is_include: sec_inc.extend(sectors)
                    else: sec_exc.extend(sectors)

            if intent_result.intent == "SET_GEO":
                has_geo = any([intent_result.slots.country, intent_result.slots.state, intent_result.slots.city])

                inferred_country = intent_result.slots.country
                if has_geo and not inferred_country:
                    existing_countries = {g.country for g in geo_inc + geo_exc if g.country}
                    if len(existing_countries) == 1:
                        inferred_country = list(existing_countries)[0]
                    else:
                        raise ToolValidationError("Please specify the country for this location.")

                if intent_result.slots.city and not intent_result.slots.state:
                    raise ToolValidationError("Please specify the state for this city.")

                if inferred_country:
                    c_clean = inferred_country.strip()
                    c_lookup = {
                        "india": "IN", "united states": "US", "usa": "US", "us": "US",
                        "united kingdom": "GB", "uk": "GB", "canada": "CA", "australia": "AU",
                        "germany": "DE", "france": "FR", "japan": "JP", "singapore": "SG"
                    }
                    if c_clean.lower() in c_lookup:
                        inferred_country = c_lookup[c_clean.lower()]
                    elif len(c_clean) == 2:
                        inferred_country = c_clean.upper()

                    try:
                        new_geo = GeoEntry(
                            country=inferred_country,
                            state=intent_result.slots.state,
                            city=intent_result.slots.city
                        )
                    except ValueError as ve:
                        raise ToolValidationError(str(ve))

                    is_exc = bool(intent_result.slots.is_exclude)

                    def geo_equal(a: GeoEntry, b: GeoEntry) -> bool:
                        def norm(s): return str(s).strip().lower() if s else ""
                        return (norm(a.country) == norm(b.country) and 
                                norm(a.state) == norm(b.state) and 
                                norm(a.city) == norm(b.city) and 
                                norm(a.pincode) == norm(b.pincode))

                    if is_exc:
                        geo_inc = [g for g in geo_inc if not geo_equal(g, new_geo)]
                        if not any(geo_equal(g, new_geo) for g in geo_exc):
                            geo_exc.append(new_geo)
                    else:
                        geo_exc = [g for g in geo_exc if not geo_equal(g, new_geo)]
                        if not any(geo_equal(g, new_geo) for g in geo_inc):
                            geo_inc.append(new_geo)

            if intent_result.intent == "SET_SECTOR" and intent_result.slots.sector:
                new_sec = intent_result.slots.sector
                is_exc = bool(intent_result.slots.is_exclude)
                if is_exc:
                    sec_inc = [s for s in sec_inc if s != new_sec]
                    if new_sec not in sec_exc:
                        sec_exc.append(new_sec)
                else:
                    sec_exc = [s for s in sec_exc if s != new_sec]
                    if new_sec not in sec_inc:
                        sec_inc.append(new_sec)

            ts = TargetingSet(
                geo_include=geo_inc,
                geo_exclude=geo_exc,
                sector_include=sec_inc,
                sector_exclude=sec_exc
            )

            t_res = await update_targeting(user=user, session=session, campaign_id=campaign_id, targeting=ts, ad_set_id=target_as_id)
            if not chat_session.current_ad_set_id and t_res.ad_set_id:
                chat_session.current_ad_set_id = t_res.ad_set_id
            tool_status = f"Success: {'Geo' if intent_result.intent == 'SET_GEO' else 'Sector'} Targeting Updated"
            payload = {"campaign_id": campaign_id, "ad_set_id": t_res.ad_set_id, "targeting": ts.model_dump()}

        elif intent_result.intent == "CREATE_AD":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            ad_set_id = intent_result.slots.ad_set_id or chat_session.current_ad_set_id
            if not campaign_id:
                raise ToolValidationError("Please create or select a campaign first.")
            if not ad_set_id:
                raise ToolValidationError("Please create or select an ad set before adding creatives.")

            title = intent_result.slots.title or intent_result.slots.name
            landing_url = intent_result.slots.landing_url
            if not title:
                raise ToolValidationError("Please provide a title or headline for the ad creative.")
            if not landing_url:
                raise ToolValidationError("Please provide a landing URL for the ad creative.")

            reused = False
            existing_cr_query = select(Creative).where(
                Creative.ad_set_id == ad_set_id,
                Creative.title == title,
                Creative.landing_url == landing_url,
                Creative.status != Status.DELETED
            )
            existing_cr_res = await session.execute(existing_cr_query)
            cr = existing_cr_res.scalars().first()
            if cr:
                tool_status = f"Success: Ad Retained ({cr.moderation_status.value if hasattr(cr.moderation_status, 'value') else str(cr.moderation_status)})"
                payload = {
                    "creative_id": cr.id,
                    "ad_set_id": cr.ad_set_id,
                    "campaign_id": cr.campaign_id,
                    "moderation_status": cr.moderation_status.value if hasattr(cr.moderation_status, "value") else str(cr.moderation_status),
                    "status": "ACTIVE" if cr.status == Status.ACTIVE else "PENDING",
                    "reused": True
                }
                reused = True

            if not reused:
                res = await create_ad(
                    user=user,
                    session=session,
                    campaign_id=campaign_id,
                    ad_set_id=ad_set_id,
                    title=title,
                    landing_url=landing_url,
                    creative_type=intent_result.slots.creative_type or "banner",
                    image_url=intent_result.slots.image_url,
                    video_url=intent_result.slots.video_url,
                    width=intent_result.slots.width or 300,
                    height=intent_result.slots.height or 250
                )
                tool_status = f"Success: Ad Created ({res.moderation_status})"
                payload = {
                    "creative_id": res.creative_id,
                    "ad_set_id": res.ad_set_id,
                    "campaign_id": res.campaign_id,
                    "moderation_status": res.moderation_status,
                    "status": res.status
                }
                chat_session.current_step = "REVIEW"

        elif intent_result.intent == "REVIEW_CAMPAIGN":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            if not campaign_id:
                raise ToolValidationError("Please create or select a campaign to review.")
            res = await review_campaign(user=user, session=session, campaign_id=campaign_id)
            chat_session.current_step = "REVIEW"
            tool_status = "Success: Campaign Reviewed"
            payload = res.model_dump()
            if res.can_activate:
                assistant_reply = (
                    f"Campaign '{res.campaign_name}' (ID: {res.campaign_id}) is ready for launch! "
                    f"Structure: {len(res.ad_sets)} Ad Set(s), {res.total_ads} Ad(s). "
                    f"Wallet balance: ₹{res.wallet_balance}. Say 'Launch Campaign' to activate."
                )
            else:
                reasons = "; ".join(res.missing_requirements)
                assistant_reply = (
                    f"Campaign '{res.campaign_name}' (ID: {res.campaign_id}) review: "
                    f"Cannot launch yet. Missing requirements: {reasons}."
                )

        elif intent_result.intent == "LAUNCH_CAMPAIGN":
            campaign_id = intent_result.slots.campaign_id or chat_session.current_campaign_id
            if not campaign_id:
                raise ToolValidationError("Please create or select a campaign first.")
            await activate_campaign(user=user, session=session, campaign_id=campaign_id)
            chat_session.current_step = "ACTIVE"
            tool_status = "Success: Campaign Activated"
            payload = {"campaign_id": campaign_id, "status": "ACTIVE"}

        elif intent_result.intent == "REQUEST_PAYMENT":
            if not intent_result.slots.amount:
                assistant_reply = "Please specify an amount to add to your wallet."
                tool_status = "Failed: Missing Amount"
            else:
                res = await request_payment(user=user, session=session, amount=intent_result.slots.amount)
                tool_status = "Success: Payment Order Created"
                payload = {
                    "order_id": res.order_id,
                    "amount": str(res.amount),
                    "currency": res.currency,
                    "qr_image_url": res.qr_image_url
                }

    except ToolValidationError as e:
        assistant_reply = str(e)
        tool_status = "Failed: Validation Error"
    except ToolAuthorizationError as e:
        assistant_reply = "You don't have permission to perform this action."
        tool_status = "Failed: Authorization Error"
    except ToolError as e:
        assistant_reply = "An internal error occurred while processing your request."
        tool_status = "Failed: Internal Tool Error"
    except Exception as e:
        logger.error(f"Unexpected error in chat orchestration: {str(e)}", exc_info=True)
        assistant_reply = "An unexpected error occurred."
        tool_status = "Failed: Unexpected Error"

    await ConversationStateService.append_message(chat_session, "assistant", assistant_reply)
    await ConversationStateService.save_session(chat_session)

    return ChatResponse(
        session_id=request.session_id,
        assistant_message=assistant_reply,
        intent=intent_result.intent,
        tool_status=tool_status,
        payload=payload,
        current_campaign_id=chat_session.current_campaign_id,
        current_ad_set_id=chat_session.current_ad_set_id,
        current_step=chat_session.current_step,
    )

