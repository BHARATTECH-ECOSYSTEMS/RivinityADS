from datetime import datetime
from typing import Optional
import uuid

from liteads.common.cache import redis_client, CacheKeys
from liteads.schemas.chat import ChatSession, ChatMessage

class ConversationStateService:
    """Service for managing temporary conversational state using Redis."""
    
    TTL_SECONDS = 86400  # 24 hours
    MAX_HISTORY = 50

    @classmethod
    async def create_session(cls, user_id: int, advertiser_id: Optional[int] = None) -> ChatSession:
        """Create a new conversational session for a user."""
        session = ChatSession(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            advertiser_id=advertiser_id,
            history=[],
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        await cls.save_session(session)
        return session

    @classmethod
    async def get_session(cls, session_id: str, user_id: int) -> Optional[ChatSession]:
        """Retrieve a session, verifying user ownership."""
        key = CacheKeys.chat_session(session_id)
        data = await redis_client.get_json(key)
        if not data:
            return None
            
        try:
            session = ChatSession(**data)
        except Exception:
            # Handle corrupt/invalid state by returning None
            return None
            
        # User Isolation Guarantee
        if session.user_id != user_id:
            return None
            
        return session

    @classmethod
    async def save_session(cls, session: ChatSession) -> bool:
        """Save session state to Redis with TTL and history limits."""
        session.updated_at = datetime.utcnow()
        
        # Enforce history limit
        if len(session.history) > cls.MAX_HISTORY:
            # Keep the most recent MAX_HISTORY messages
            session.history = session.history[-cls.MAX_HISTORY:]
            
        key = CacheKeys.chat_session(session.session_id)
        # Use mode='json' so that datetime is serialized to string safely for json_dumps
        return await redis_client.set_json(key, session.model_dump(mode='json'), ttl=cls.TTL_SECONDS)

    @classmethod
    async def delete_session(cls, session_id: str) -> int:
        """Delete a conversation session."""
        key = CacheKeys.chat_session(session_id)
        return await redis_client.delete(key)

    @classmethod
    async def append_message(cls, session: ChatSession, role: str, content: str) -> bool:
        """Append a message to the session's history and save."""
        message = ChatMessage(role=role, content=content)
        session.history.append(message)
        return await cls.save_session(session)
