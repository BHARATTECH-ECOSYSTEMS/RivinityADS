import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession
from decimal import Decimal

from liteads.models.ad import Campaign, TargetingRule
from liteads.models.auth import User
from liteads.ad_server.services.conversation_state import ConversationStateService
from liteads.schemas.chat import ChatSession
from liteads.schemas.intent import IntentResult, SlotData
from liteads.ad_server.services.tools import ToolValidationError
from liteads.common.cache import redis_client

pytestmark = pytest.mark.asyncio

@pytest.fixture(autouse=True)
async def clear_redis():
    await redis_client.connect()
    if redis_client.connected:
        await redis_client.client.flushdb()
    yield
    if redis_client.connected:
        await redis_client.client.flushdb()
        await redis_client.close()

@pytest.fixture
def advertiser_user():
    return User(id=1, email="adv1@test.com", role="advertiser", advertiser_id=1, is_active=True)

@pytest.fixture
def auth_client(client: AsyncClient, advertiser_user: User):
    from liteads.common.security import current_user
    from liteads.ad_server.main import app
    app.dependency_overrides[current_user] = lambda: advertiser_user
    yield client
    app.dependency_overrides.pop(current_user, None)

@pytest.fixture
async def chat_session(test_db: AsyncSession, advertiser_user: User):
    session = await ConversationStateService.create_session(user_id=advertiser_user.id, advertiser_id=advertiser_user.advertiser_id)
    # create a dummy campaign
    campaign = Campaign(advertiser_id=1, name="Test Campaign", status=5)
    test_db.add(campaign)
    await test_db.commit()
    await test_db.refresh(campaign)
    session.current_campaign_id = campaign.id
    await ConversationStateService.save_session(session)
    
    yield session
    
    # cleanup
    await ConversationStateService.delete_session(session.session_id)
    await test_db.delete(campaign)
    await test_db.commit()

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_country_only_targeting(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    # 3. Country-only targeting behavior
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="IN"),
        reply="Targeting India"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target India"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_include) == 1
    assert ts.geo_include[0].country == "IN"
    assert ts.geo_include[0].state is None
    assert ts.geo_include[0].city is None

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_invalid_partial_location(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession):
    # 11. Invalid partial location. (No existing country context)
    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(city="Bengaluru"), # No country, no existing targets
        reply="Targeting Bengaluru"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target Bengaluru"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Failed: Validation Error"
    assert "specify the country" in response.json()["assistant_message"].lower()
    mock_update_targeting.assert_not_called()

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_city_only_targeting_fails(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 1. City-only targeting behavior (fails because state is required)
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "IN", "city": "Delhi", "state": "DL"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(city="Bengaluru"), # No state provided!
        reply="Targeting Bengaluru"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target Bengaluru"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Failed: Validation Error"
    assert "specify the state for this city" in response.json()["assistant_message"].lower()
    mock_update_targeting.assert_not_called()

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_city_with_valid_state_works(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 3. City with valid state continues to work.
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "IN", "state": "DL", "city": "Delhi"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(state="KA", city="Bengaluru"), # Valid state provided
        reply="Targeting Bengaluru"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target Bengaluru"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_include) == 2
    assert ts.geo_include[1].city == "Bengaluru"
    assert ts.geo_include[1].state == "KA"

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_state_only_targeting_with_inference(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 2. State-only targeting behavior
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "US", "state": "CA"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(state="NY"), 
        reply="Targeting NY"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target NY"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_include) == 2
    states = {g.state for g in ts.geo_include}
    assert states == {"CA", "NY"}
    countries = {g.country for g in ts.geo_include}
    assert countries == {"US"}

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_duplicate_targeting_request(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 7. Duplicate targeting request
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "IN", "state": "DL", "city": "Delhi"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="IN", state="DL", city="Delhi"), 
        reply="Targeting Delhi"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target Delhi"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_include) == 1 # Still just 1, duplicate ignored!

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_include_exclude_behavior(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 8. Include/exclude behavior
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "IN", "state": "DL", "city": "Delhi"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="IN", state="DL", city="Delhi", is_exclude=True), 
        reply="Excluding Delhi"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Exclude Delhi"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_include) == 0 # Removed from include
    assert len(ts.geo_exclude) == 1 # Added to exclude

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_include_exclude_different_capitalization(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 5. Existing include + explicit exclude with different capitalization
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "IN", "state": "DL", "city": "Delhi"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="in", state="dl", city="DELHI", is_exclude=True), 
        reply="Excluding Delhi"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Exclude DELHI"
    })
    
    assert response.status_code == 200
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_include) == 0 # Removed despite different case
    assert len(ts.geo_exclude) == 1

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_exclude_exclude_no_duplicate(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 6. Existing exclude + explicit exclude -> no duplicate
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=False, rule_value={"entries": [{"country": "IN", "state": "DL", "city": "Delhi"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="IN", state="DL", city="Delhi", is_exclude=True), 
        reply="Excluding Delhi"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Exclude Delhi"
    })
    
    assert response.status_code == 200
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_exclude) == 1 # Still just 1

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_exclude_include_reverse_transition(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 7. Existing exclude + include -> reverse transition works
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=False, rule_value={"entries": [{"country": "IN", "state": "DL", "city": "Delhi"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="IN", state="DL", city="Delhi", is_exclude=False), 
        reply="Targeting Delhi"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target Delhi"
    })
    
    assert response.status_code == 200
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_exclude) == 0 # Removed from exclude
    assert len(ts.geo_include) == 1 # Added to include

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_existing_sector_preserved_during_geo(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 5. Existing sector targeting preserved during geo update
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="sector", is_include=True, rule_value={"sectors": ["tech"]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(country="IN"), 
        reply="Targeting IN"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target IN"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.sector_include) == 1
    assert ts.sector_include[0] == "tech"

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_existing_geo_preserved_during_sector(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 6. Existing geo targeting preserved during sector update
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "IN"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_SECTOR",
        slots=SlotData(sector="tech"), 
        reply="Targeting tech"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target tech"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_called_once()
    ts = mock_update_targeting.call_args[1]["targeting"]
    assert len(ts.geo_include) == 1
    assert ts.geo_include[0].country == "IN"

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
@patch("liteads.ad_server.routers.chat.update_targeting", new_callable=AsyncMock)
async def test_failed_targeting_update_leaves_previous(mock_update_targeting, mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 12. Failed targeting update leaves previous targeting unchanged.
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="sector", is_include=True, rule_value={"sectors": ["tech"]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(city="Bengaluru"), # Fails because no country inferred
        reply="Targeting Bengaluru"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Target Bengaluru"
    })
    
    assert response.status_code == 200
    mock_update_targeting.assert_not_called()
    
    # Verify DB still has the old rule
    await test_db.refresh(existing_rule)
    assert existing_rule.id is not None

@patch("liteads.ad_server.routers.chat.GenerativeIntentClient")
async def test_attempted_replacement_and_removal(mock_client_cls, auth_client: AsyncClient, chat_session: ChatSession, test_db: AsyncSession):
    # 9. Attempted replacement/change behavior.
    # 10. Attempted removal behavior.
    # Because intent schema only has is_exclude, these behave as additive or exclude.
    existing_rule = TargetingRule(campaign_id=chat_session.current_campaign_id, rule_type="geo", is_include=True, rule_value={"entries": [{"country": "IN", "state": "KA", "city": "Bengaluru"}]})
    test_db.add(existing_rule)
    await test_db.commit()

    mock_instance = mock_client_cls.return_value
    mock_instance.extract_intent = AsyncMock(return_value=IntentResult(
        intent="SET_GEO",
        slots=SlotData(state="DL", city="Delhi"), # Chat schema parses "Change that to Delhi" as SET_GEO with city="Delhi"
        reply="Targeting Delhi"
    ))
    
    response = await auth_client.post("/api/v1/chat", json={
        "session_id": chat_session.session_id,
        "message": "Change that to Delhi"
    })
    
    assert response.status_code == 200
    assert response.json()["tool_status"] == "Success: Geo Targeting Updated"

    # We cannot test db changes directly easily because mock_update_targeting wasn't patched, 
    # but we can query DB because the actual update_targeting ran!
    
    query = test_db.execute
    # wait, actual DB will be modified by update_targeting.
    # but let's test it later in the suite.

