import pytest
import pytest_asyncio
from liteads.schemas.chat import ChatSession, ChatMessage
from liteads.ad_server.services.conversation_state import ConversationStateService
from liteads.common.cache import redis_client, CacheKeys

@pytest_asyncio.fixture(autouse=True)
async def clear_redis():
    # Connect fresh for each test to match function loop scope
    await redis_client.connect()
    if redis_client.connected:
        await redis_client.client.flushdb()
    yield
    if redis_client.connected:
        await redis_client.client.flushdb()
        await redis_client.close()

@pytest.mark.asyncio
async def test_create_session():
    # 1. CREATE SESSION
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    assert session.session_id is not None
    assert session.user_id == 1
    assert session.advertiser_id == 10
    assert session.created_at is not None
    
    # 9. TTL
    # Verify the state is written with an expiration
    key = CacheKeys.chat_session(session.session_id)
    ttl = await redis_client.ttl(key)
    assert ttl > 0
    assert ttl <= ConversationStateService.TTL_SECONDS

@pytest.mark.asyncio
async def test_get_session():
    # 2. GET SESSION
    session1 = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    session2 = await ConversationStateService.get_session(session1.session_id, user_id=1)
    
    assert session2 is not None
    assert session2.session_id == session1.session_id
    assert session2.user_id == 1
    assert session2.advertiser_id == 10

@pytest.mark.asyncio
async def test_user_isolation():
    # 3. USER ISOLATION
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    
    # User B attempts retrieval
    session2 = await ConversationStateService.get_session(session.session_id, user_id=2)
    assert session2 is None

@pytest.mark.asyncio
async def test_append_message():
    # 5. APPEND MESSAGE
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    
    await ConversationStateService.append_message(session, "user", "Create a Nike campaign")
    await ConversationStateService.append_message(session, "assistant", "What budget would you like?")
    
    retrieved = await ConversationStateService.get_session(session.session_id, user_id=1)
    assert retrieved is not None
    assert len(retrieved.history) == 2
    assert retrieved.history[0].role == "user"
    assert retrieved.history[0].content == "Create a Nike campaign"
    assert retrieved.history[1].role == "assistant"
    assert retrieved.history[1].content == "What budget would you like?"

@pytest.mark.asyncio
async def test_history_limit():
    # 6. HISTORY LIMIT
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    
    max_hist = ConversationStateService.MAX_HISTORY
    for i in range(max_hist + 5):
        await ConversationStateService.append_message(session, "user", f"Message {i}")
        
    retrieved = await ConversationStateService.get_session(session.session_id, user_id=1)
    assert retrieved is not None
    assert len(retrieved.history) == max_hist
    assert retrieved.history[0].content == "Message 5"
    assert retrieved.history[-1].content == f"Message {max_hist + 4}"

@pytest.mark.asyncio
async def test_current_campaign():
    # 7. CURRENT CAMPAIGN
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    session.current_campaign_id = 999
    await ConversationStateService.save_session(session)
    
    retrieved = await ConversationStateService.get_session(session.session_id, user_id=1)
    assert retrieved is not None
    assert retrieved.current_campaign_id == 999

@pytest.mark.asyncio
async def test_pending_action():
    # 8. PENDING ACTION
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    session.pending_action = "awaiting_payment"
    await ConversationStateService.save_session(session)
    
    retrieved = await ConversationStateService.get_session(session.session_id, user_id=1)
    assert retrieved is not None
    assert retrieved.pending_action == "awaiting_payment"

@pytest.mark.asyncio
async def test_delete_session():
    # 10. DELETE SESSION
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    await ConversationStateService.delete_session(session.session_id)
    
    retrieved = await ConversationStateService.get_session(session.session_id, user_id=1)
    assert retrieved is None

@pytest.mark.asyncio
async def test_serialization():
    # 11. SERIALIZATION
    session = await ConversationStateService.create_session(user_id=1, advertiser_id=10)
    # Just asserting it didn't raise during save, and retrieving works
    retrieved = await ConversationStateService.get_session(session.session_id, user_id=1)
    assert retrieved is not None
    assert retrieved.created_at is not None
