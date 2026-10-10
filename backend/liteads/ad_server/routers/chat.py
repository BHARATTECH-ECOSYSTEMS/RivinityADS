from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from liteads.common.database import get_session
from liteads.common.security import current_user, verified_user
from liteads.models.auth import User
from liteads.models.ad import TargetingRule
from liteads.schemas.targeting import TargetingSet, GeoEntry
from liteads.ad_server.services.conversation_state import ConversationStateService
from liteads.ad_server.services.generative_intent_client import (
    GenerativeIntentClient,
    GenerativeUnavailableError,
    GenerativeMalformedResponseError
)
from liteads.ad_server.services.tools import (
    create_draft_campaign,
    update_campaign,
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
        return ChatResponse(session_id=request.session_id, assistant_message=err_msg, intent="UNKNOWN", tool_status="Failed: LLM Unavailable")
    except GenerativeMalformedResponseError:
        err_msg = "I encountered an error understanding your request. Could you rephrase?"
        await ConversationStateService.append_message(chat_session, "assistant", err_msg)
        await ConversationStateService.save_session(chat_session)
        return ChatResponse(session_id=request.session_id, assistant_message=err_msg, intent="UNKNOWN", tool_status="Failed: LLM Malformed")

    tool_status = None
    payload = None
    assistant_reply = intent_result.reply or "Processing your request."

    try:
        if intent_result.intent == "CREATE_CAMPAIGN":
            res = await create_draft_campaign(
                user=user,
                session=session,
                name=intent_result.slots.name,
                budget_daily=intent_result.slots.budget_daily,
                budget_total=intent_result.slots.budget_total
            )
            chat_session.current_campaign_id = res.campaign_id
            tool_status = "Success: Campaign Created"
            payload = {"campaign_id": res.campaign_id}

        elif intent_result.intent in ["SET_BUDGET", "SET_GEO", "SET_SECTOR", "LAUNCH_CAMPAIGN"]:
            if not chat_session.current_campaign_id:
                assistant_reply = "Please create or select a campaign first."
                tool_status = "Failed: No Campaign Selected"
            else:
                if intent_result.intent == "SET_BUDGET":
                    await update_campaign(
                        user=user,
                        session=session,
                        campaign_id=chat_session.current_campaign_id,
                        budget_daily=intent_result.slots.budget_daily,
                        budget_total=intent_result.slots.budget_total
                    )
                    tool_status = "Success: Budget Updated"
                
                elif intent_result.intent in ["SET_GEO", "SET_SECTOR"]:
                    query = select(TargetingRule).where(TargetingRule.campaign_id == chat_session.current_campaign_id)
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
                        
                        # Infer country from existing targeting if missing
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
                    
                    await update_targeting(user=user, session=session, campaign_id=chat_session.current_campaign_id, targeting=ts)
                    tool_status = f"Success: {'Geo' if intent_result.intent == 'SET_GEO' else 'Sector'} Targeting Updated"
                
                elif intent_result.intent == "LAUNCH_CAMPAIGN":
                    await activate_campaign(user=user, session=session, campaign_id=chat_session.current_campaign_id)
                    tool_status = "Success: Campaign Activated"
                    
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
        assistant_reply = "An unexpected error occurred."
        tool_status = "Failed: Unexpected Error"

    await ConversationStateService.append_message(chat_session, "assistant", assistant_reply)
    await ConversationStateService.save_session(chat_session)

    return ChatResponse(
        session_id=request.session_id,
        assistant_message=assistant_reply,
        intent=intent_result.intent,
        tool_status=tool_status,
        payload=payload
    )
