import json
import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_current_user
from app.core.config import (
    CHAT_EXTERNAL_CHANNEL,
    CHAT_EXTERNAL_MODEL,
    CHAT_EXTERNAL_URL,
    CHAT_EXTERNAL_STREAM_URL,
    CHAT_EXTERNAL_TIMEOUT,
    CHAT_EXTERNAL_USE_STREAM,
    LLM_MODEL,
)
from app.core.database import conversations_collection
from app.core.llm import llm_client
from app.core.utils import parse_object_id
from app.schemas.chat import ChatRequest

router = APIRouter()


def format_sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_full_assistant_text(reasoning_text: str, assistant_text: str) -> str:
    if reasoning_text.strip():
        return f"<think>{reasoning_text.strip()}</think>\n{assistant_text.strip()}".strip()
    return assistant_text.strip()


async def get_or_create_conversation(payload: ChatRequest, user_id: str) -> tuple[ObjectId, dict, datetime]:
    now = datetime.now(timezone.utc)
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
        return conversation_oid, conversation, now

    new_doc = {
        "user_id": user_id,
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    inserted = await conversations_collection.insert_one(new_doc)
    conversation_oid = inserted.inserted_id
    conversation = {**new_doc, "_id": conversation_oid}
    return conversation_oid, conversation, now


def build_messages_with_user(conversation: dict, user_message: str) -> list[dict[str, str]]:
    existing_messages = conversation.get("messages", [])
    messages_list = [
        {"role": msg.get("role", "user"), "content": msg.get("content", "")}
        for msg in existing_messages
        if msg.get("content")
    ]
    messages_list.append({"role": "user", "content": user_message})
    return messages_list


async def persist_messages(
    conversation_oid: ObjectId,
    user_id: str,
    user_message: str,
    assistant_message: str,
    user_message_time: datetime,
) -> None:
    user_message_doc = {
        "role": "user",
        "content": user_message,
        "created_at": user_message_time,
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


def build_external_payload(messages: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "messages": [
            {
                "type": "message",
                "role": message["role"],
                "content": message["content"],
            }
            for message in messages
        ],
        "model": CHAT_EXTERNAL_MODEL,
        "metadata": {"channel": CHAT_EXTERNAL_CHANNEL},
    }


async def stream_external_events(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    if not CHAT_EXTERNAL_STREAM_URL:
        raise ValueError("CHAT_EXTERNAL_STREAM_URL is not configured")
    timeout = httpx.Timeout(CHAT_EXTERNAL_TIMEOUT)
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", CHAT_EXTERNAL_STREAM_URL, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload_raw = line[5:].strip()
                if not payload_raw:
                    continue
                try:
                    event = json.loads(payload_raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event


async def call_external_non_stream(payload: dict[str, Any]) -> str:
    if not CHAT_EXTERNAL_URL:
        raise ValueError("CHAT_EXTERNAL_URL is not configured")
    timeout = httpx.Timeout(CHAT_EXTERNAL_TIMEOUT)
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            CHAT_EXTERNAL_URL,
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, dict):
        return ""
    message = data.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content.strip()
    return ""


def chunk_text(text: str, size: int = 10) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [stripped[idx : idx + size] for idx in range(0, len(stripped), size)]


@router.post("/")
async def chat(payload: ChatRequest, current_user: dict = Depends(get_current_user)):
    if not payload.message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty",
        )

    user_id = current_user["id"]
    conversation_oid, conversation, now = await get_or_create_conversation(payload, user_id)
    messages_list = build_messages_with_user(conversation, payload.message)
    external_payload = build_external_payload(messages_list)

    async def stream_event_generator():
        content_parts: list[str] = []
        final_content = ""
        try:
            if CHAT_EXTERNAL_USE_STREAM:
                async for event in stream_external_events(external_payload):
                    event_type = event.get("type")
                    if event_type == "token":
                        content = event.get("content", "")
                        if content:
                            content_parts.append(content)
                            yield format_sse({"type": "delta", "content": content})
                    elif event_type == "done":
                        final_message = event.get("message") or {}
                        done_content = final_message.get("content", "")
                        if done_content:
                            final_content = done_content.strip()
            else:
                final_content = await call_external_non_stream(external_payload)
                for chunk in chunk_text(final_content):
                    content_parts.append(chunk)
                    yield format_sse({"type": "delta", "content": chunk})
                    await asyncio.sleep(0.1)
        except Exception as exc:
            yield format_sse({"type": "error", "message": f"External chat error: {exc}"})
            return

        assistant_text = final_content or "".join(content_parts).strip()
        await persist_messages(
            conversation_oid=conversation_oid,
            user_id=user_id,
            user_message=payload.message,
            assistant_message=assistant_text,
            user_message_time=now,
        )
        yield format_sse({"type": "done", "conversation_id": str(conversation_oid)})

    if payload.stream:
        return StreamingResponse(
            stream_event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    content_parts: list[str] = []
    final_content = ""
    try:
        if CHAT_EXTERNAL_USE_STREAM:
            async for event in stream_external_events(external_payload):
                event_type = event.get("type")
                if event_type == "token":
                    content = event.get("content", "")
                    if content:
                        content_parts.append(content)
                elif event_type == "done":
                    final_message = event.get("message") or {}
                    done_content = final_message.get("content", "")
                    if done_content:
                        final_content = done_content.strip()
        else:
            final_content = await call_external_non_stream(external_payload)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"External chat error: {exc}",
        ) from exc

    assistant_text = final_content or "".join(content_parts).strip()
    await persist_messages(
        conversation_oid=conversation_oid,
        user_id=user_id,
        user_message=payload.message,
        assistant_message=assistant_text,
        user_message_time=now,
    )
    return {
        "conversation_id": str(conversation_oid),
        "content": assistant_text,
        "reasoning_content": "",
        "full_content": assistant_text,
    }


@router.post("/legacy")
async def chat_legacy(payload: ChatRequest, current_user: dict = Depends(get_current_user)):
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
    conversation_oid, conversation, now = await get_or_create_conversation(payload, user_id)
    messages_list = build_messages_with_user(conversation, payload.message)

    async def legacy_event_generator():
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
            user_message_time=now,
        )
        yield format_sse({"type": "done", "conversation_id": str(conversation_oid)})

    if payload.stream:
        return StreamingResponse(
            legacy_event_generator(),
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
        user_message_time=now,
    )
    return {
        "conversation_id": str(conversation_oid),
        "content": assistant_text,
        "reasoning_content": reasoning_text,
        "full_content": full_assistant_text,
    }