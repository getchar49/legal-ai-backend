# app/schemas/chat.py
from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    stream: bool = True