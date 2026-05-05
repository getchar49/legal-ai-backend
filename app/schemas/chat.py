# app/schemas/chat.py
from pydantic import BaseModel

from app.schemas.documents import Citation


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    stream: bool = True
    agent_id: str = "fast"


class ChatAgentOption(BaseModel):
    agent_id: str
    name: str
    description: str
    inference_mode: str
    is_default: bool


class ChatAgentListResponse(BaseModel):
    default_agent_id: str
    items: list[ChatAgentOption]


__all__ = [
    "ChatRequest",
    "ChatAgentOption",
    "ChatAgentListResponse",
    "Citation",
]
