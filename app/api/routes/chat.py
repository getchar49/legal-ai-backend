import json
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest
from app.api.dependencies import get_current_user
from app.core.database import conversations_collection
from app.core.utils import parse_object_id
from app.core.llm import llm_client
from app.core.config import LLM_MODEL

router = APIRouter()

@router.post("/")
async def chat(payload: ChatRequest, current_user: dict = Depends(get_current_user)):
    if not payload.message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty",
        )

    if not llm_client:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="LLM_API_KEY is not configured",
        )

    user_id = current_user["id"]
    now = datetime.now(timezone.utc)

    conversation = None
    conversation_oid = None
    
    if payload.conversation_id:
        conversation_oid = parse_object_id(payload.conversation_id, "conversation_id")
        conversation = await conversations_collection.find_one(
            {"_id": conversation_oid, "user_id": user_id}
        )
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
    else:
        new_doc = {
            "user_id": user_id,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        inserted = await conversations_collection.insert_one(new_doc)
        conversation_oid = inserted.inserted_id
        conversation = {**new_doc, "_id": conversation_oid}

    existing_messages = conversation.get("messages", [])
    messages_list = [
        {"role": msg.get("role", "user"), "content": msg.get("content", "")}
        for msg in existing_messages
        if msg.get("content")
    ]
    messages_list.append({"role": "user", "content": payload.message})

    def format_sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    def build_full_assistant_text(reasoning_text: str, assistant_text: str) -> str:
        if reasoning_text.strip():
            return f"<think>{reasoning_text.strip()}</think>\n{assistant_text.strip()}".strip()
        return assistant_text.strip()

    async def persist_messages(
        conversation_oid: ObjectId,
        user_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        user_message_doc = {
            "role": "user",
            "content": user_message,
            "created_at": now,
        }
        assistant_message_doc = {
            "role": "assistant",
            "content": assistant_message,
            "created_at": datetime.now(timezone.utc),
        }
        await conversations_collection.update_one(
            {"_id": conversation_oid, "user_id": user_id},
            {
                "$push": {"messages": {"$each": [user_message_doc, assistant_message_doc]}},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    async def event_generator():
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            completion = await llm_client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages_list,
                temperature=0.2,
                top_p=0.7,
                max_tokens=8192,
                extra_body={"chat_template_kwargs": {"thinking": True}},
                stream=True,
            )
            async for chunk in completion:
                if not getattr(chunk, "choices", None):
                    continue

                delta = chunk.choices[0].delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
                    yield format_sse({"type": "reasoning", "content": reasoning})

                content = delta.content
                if content:
                    content_parts.append(content)
                    yield format_sse({"type": "delta", "content": content})
        except Exception as exc:
            yield format_sse({"type": "error", "message": f"LLM stream error: {exc}"})
            return

        reasoning_text = "".join(reasoning_parts).strip()
        assistant_text = "".join(content_parts).strip()
        full_assistant_text = build_full_assistant_text(reasoning_text, assistant_text)
        await persist_messages(
            conversation_oid=conversation_oid,
            user_id=user_id,
            user_message=payload.message,
            assistant_message=full_assistant_text,
        )
        yield format_sse({"type": "done", "conversation_id": str(conversation_oid)})

    if payload.stream:
        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        completion = await llm_client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages_list,
            temperature=0.2,
            top_p=0.7,
            max_tokens=8192,
            extra_body={"chat_template_kwargs": {"thinking": True}},
            stream=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM error: {exc}",
        ) from exc

    message = completion.choices[0].message
    reasoning_text = (getattr(message, "reasoning_content", None) or "").strip()
    assistant_text = (message.content or "").strip()
    full_assistant_text = build_full_assistant_text(reasoning_text, assistant_text)

    await persist_messages(
        conversation_oid=conversation_oid,
        user_id=user_id,
        user_message=payload.message,
        assistant_message=full_assistant_text,
    )
    return {
        "conversation_id": str(conversation_oid),
        "content": assistant_text,
        "reasoning_content": reasoning_text,
        "full_content": full_assistant_text,
    }