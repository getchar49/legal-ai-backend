import json
import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_current_user
from app.core.agent_catalog import (
    DEFAULT_AGENT_ID,
    list_public_agents,
    resolve_agent_profile,
)
from app.core.config import (
    CHAT_EXTERNAL_URL,
    CHAT_EXTERNAL_STREAM_URL,
    CHAT_EXTERNAL_TIMEOUT,
    CHAT_EXTERNAL_USE_STREAM,
    CHAT_EXTERNAL_MODEL,
    LLM_MODEL,
)
from app.core.database import conversations_collection
from app.core.document_catalog import document_catalog
from app.core.llm import llm_client
from app.core.utils import parse_object_id
from app.schemas.chat import ChatAgentListResponse, ChatRequest

router = APIRouter()
http_client = httpx.AsyncClient(timeout=httpx.Timeout(CHAT_EXTERNAL_TIMEOUT))


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
    """Giữ lại để dùng cho legacy LLM. External API mới chỉ cần message hiện tại."""
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
    citations: list[dict[str, Any]] | None = None,
    external_response_id: str | None = None,
) -> None:
    user_message_doc = {
        "role": "user",
        "content": user_message,
        "created_at": user_message_time,
    }
    assistant_message_doc: dict[str, Any] = {
        "role": "assistant",
        "content": assistant_message,
        "created_at": datetime.now(timezone.utc),
    }
    if citations:
        assistant_message_doc["citations"] = citations
    if external_response_id:
        assistant_message_doc["external_response_id"] = external_response_id

    update_set: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}
    if external_response_id:
        # Lưu lại response_id cuối cùng để turn sau truyền previous_response_id.
        update_set["last_response_id"] = external_response_id

    await conversations_collection.update_one(
        {"_id": conversation_oid, "user_id": user_id},
        {
            "$push": {"messages": {"$each": [user_message_doc, assistant_message_doc]}},
            "$set": update_set,
        },
    )


def build_external_payload(
    user_message: str,
    agent_id: str | None,
    *,
    conversation: dict | None = None,
) -> dict[str, Any]:
    """Format theo external API v2: messages là string, có previous_response_id."""
    agent_profile = resolve_agent_profile(agent_id)
    previous_response_id = ""
    conv_id = ""
    if conversation:
        previous_response_id = conversation.get("last_response_id") or ""
        conv_oid = conversation.get("_id")
        if conv_oid is not None:
            conv_id = str(conv_oid)
    return {
        "conversation_id": conv_id,
        "messages": user_message,
        "previous_response_id": previous_response_id,
        # Tạm off update, dùng agent id là default
        "agent_id": "default",
        #"agent_id": agent_profile.external_agent_id,
        "model": CHAT_EXTERNAL_MODEL,
        "metadata": {},
    }


async def stream_external_events(payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    if not CHAT_EXTERNAL_STREAM_URL:
        raise ValueError("CHAT_EXTERNAL_STREAM_URL is not configured")

    headers = {"accept": "text/event-stream", "content-type": "application/json"}

    async with http_client.stream(
        "POST", CHAT_EXTERNAL_STREAM_URL, headers=headers, json=payload
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload_raw = line[5:].strip()
            if not payload_raw or payload_raw == "[DONE]":
                continue
            try:
                event = json.loads(payload_raw)
                if isinstance(event, dict):
                    yield event
            except json.JSONDecodeError:
                continue


async def call_external_non_stream(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str | None]:
    """Fallback non-stream: gọi /chat (không stream) để lấy full response.

    Trả về (content, annotations, response_id).
    """
    if not CHAT_EXTERNAL_URL:
        raise ValueError("CHAT_EXTERNAL_URL is not configured")
    timeout = httpx.Timeout(CHAT_EXTERNAL_TIMEOUT)
    headers = {"accept": "application/json", "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(CHAT_EXTERNAL_URL, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    if not isinstance(data, dict):
        return "", [], None

    # External v2 trả {"id":..., "output":{"role":"assistant","content":"..."}, "annotations":[...]}
    response_id = data.get("id") if isinstance(data.get("id"), str) else None
    output = data.get("output")
    if isinstance(output, dict):
        content = output.get("content", "")
    else:
        # Fallback: schema cũ {"message":{"content":"..."}}
        message = data.get("message")
        content = message.get("content", "") if isinstance(message, dict) else ""
    if not isinstance(content, str):
        content = ""

    annotations = data.get("annotations") or []
    if not isinstance(annotations, list):
        annotations = []
    return content.strip(), annotations, response_id


def chunk_text(text: str, size: int = 10) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [stripped[idx : idx + size] for idx in range(0, len(stripped), size)]


def build_citations(
    annotations: list[Any] | None,
    *,
    base_path: str = "",
) -> list[dict[str, Any]]:
    """Resolve annotations từ external API thành citation có sẵn download_url."""
    if not annotations:
        return []
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ann in annotations:
        if not isinstance(ann, dict):
            continue
        cid_raw = ann.get("id")
        if not isinstance(cid_raw, str) or not cid_raw.strip():
            continue
        cid = document_catalog.normalize_id(cid_raw)
        if cid in seen:
            continue
        seen.add(cid)

        title = ann.get("title")
        source_url = ann.get("url")
        # External API có thể trả "null" dạng string cho url khi không có nguồn.
        if isinstance(source_url, str) and source_url.strip().lower() in {"", "null", "none"}:
            source_url = None

        entry = document_catalog.resolve(cid)
        citation: dict[str, Any] = {
            "id": cid,
            "title": title if isinstance(title, str) else None,
            "source_url": source_url,
            "available": entry is not None and entry.available,
            "category": entry.category if entry else None,
            "filename": entry.filename if entry else None,
            "media_type": entry.media_type if entry else None,
            "download_url": (
                f"{base_path}/api/documents/file?citation_id={quote(cid, safe='')}"
                if entry and entry.available
                else None
            ),
        }
        citations.append(citation)
    return citations


@router.get("/agents", response_model=ChatAgentListResponse)
async def get_available_agents(_current_user: dict = Depends(get_current_user)):
    return {
        "default_agent_id": DEFAULT_AGENT_ID,
        "items": list_public_agents(),
    }


@router.post("/")
async def chat(
    payload: ChatRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    if not payload.message.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message cannot be empty",
        )

    user_id = current_user["id"]
    conversation_oid, conversation, now = await get_or_create_conversation(payload, user_id)
    external_payload = build_external_payload(
        payload.message, payload.agent_id, conversation=conversation
    )
    #print(external_payload)
    base_path = request.scope.get("root_path", "") or ""

    async def stream_event_generator():
        content_parts: list[str] = []
        final_content = ""
        final_annotations: list[Any] = []
        external_response_id: str | None = None

        try:
            yield format_sse({"type": "conversation", "conversation_id": str(conversation_oid)})

            if CHAT_EXTERNAL_USE_STREAM:
                async for event in stream_external_events(external_payload):
                    #print(event.get("type"))
                    #print(event.get("content"))
                    event_type = event.get("type")
                    if event_type == "response.created":
                        resp = event.get("response") or {}
                        rid = resp.get("id")
                        if isinstance(rid, str):
                            external_response_id = rid

                    elif event_type == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if isinstance(delta, str) and delta:
                            content_parts.append(delta)
                            yield format_sse({"type": "delta", "content": delta})

                    elif event_type == "response.completed":
                        resp = event.get("response") or {}
                        rid = resp.get("id")
                        if isinstance(rid, str):
                            external_response_id = rid
                        output = resp.get("output")
                        if isinstance(output, dict):
                            content = output.get("content")
                            if isinstance(content, str) and content.strip():
                                final_content = content
                        anns = resp.get("annotations")
                        if isinstance(anns, list):
                            final_annotations = anns

                    elif event_type in {"response.error", "error"}:
                        err = event.get("error") or event.get("message") or "unknown error"
                        yield format_sse({"type": "error", "message": str(err)})
            else:
                final_content, final_annotations, external_response_id = await call_external_non_stream(
                    external_payload
                )
                for chunk in chunk_text(final_content):
                    content_parts.append(chunk)
                    yield format_sse({"type": "delta", "content": chunk})
                    await asyncio.sleep(0.02)

        except asyncio.CancelledError:
            assistant_text = final_content or "".join(content_parts).strip()
            if assistant_text:
                citations = build_citations(final_annotations, base_path=base_path)
                await persist_messages(
                    conversation_oid=conversation_oid,
                    user_id=user_id,
                    user_message=payload.message,
                    assistant_message=assistant_text,
                    user_message_time=now,
                    citations=citations,
                    external_response_id=external_response_id,
                )
            raise

        except Exception as exc:
            yield format_sse({"type": "error", "message": f"External chat error: {exc}"})
            assistant_text = final_content or "".join(content_parts).strip()
            if assistant_text:
                citations = build_citations(final_annotations, base_path=base_path)
                await persist_messages(
                    conversation_oid=conversation_oid,
                    user_id=user_id,
                    user_message=payload.message,
                    assistant_message=assistant_text,
                    user_message_time=now,
                    citations=citations,
                    external_response_id=external_response_id,
                )
            return

        assistant_text = final_content or "".join(content_parts).strip()
        citations = build_citations(final_annotations, base_path=base_path)

        await persist_messages(
            conversation_oid=conversation_oid,
            user_id=user_id,
            user_message=payload.message,
            assistant_message=assistant_text,
            user_message_time=now,
            citations=citations,
            external_response_id=external_response_id,
        )
        if citations:
            yield format_sse({"type": "citations", "items": citations})
        yield format_sse(
            {
                "type": "done",
                "conversation_id": str(conversation_oid),
                "response_id": external_response_id,
            }
        )

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
    final_annotations: list[Any] = []
    external_response_id: str | None = None
    try:
        if CHAT_EXTERNAL_USE_STREAM:
            async for event in stream_external_events(external_payload):
                event_type = event.get("type")
                if event_type == "response.created":
                    resp = event.get("response") or {}
                    rid = resp.get("id")
                    if isinstance(rid, str):
                        external_response_id = rid
                elif event_type == "response.output_text.delta":
                    delta = event.get("delta", "")
                    if isinstance(delta, str) and delta:
                        content_parts.append(delta)
                elif event_type == "response.completed":
                    resp = event.get("response") or {}
                    rid = resp.get("id")
                    if isinstance(rid, str):
                        external_response_id = rid
                    output = resp.get("output")
                    if isinstance(output, dict):
                        content = output.get("content")
                        if isinstance(content, str) and content.strip():
                            final_content = content
                    anns = resp.get("annotations")
                    if isinstance(anns, list):
                        final_annotations = anns
        else:
            final_content, final_annotations, external_response_id = await call_external_non_stream(
                external_payload
            )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"External chat error: {exc}",
        ) from exc

    assistant_text = final_content or "".join(content_parts).strip()
    citations = build_citations(final_annotations, base_path=base_path)
    await persist_messages(
        conversation_oid=conversation_oid,
        user_id=user_id,
        user_message=payload.message,
        assistant_message=assistant_text,
        user_message_time=now,
        citations=citations,
        external_response_id=external_response_id,
    )
    return {
        "conversation_id": str(conversation_oid),
        "response_id": external_response_id,
        "content": assistant_text,
        "reasoning_content": "",
        "full_content": assistant_text,
        "citations": citations,
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
    legacy_thinking_enabled = resolve_agent_profile(payload.agent_id).legacy_thinking

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
                extra_body={"chat_template_kwargs": {"thinking": legacy_thinking_enabled}},
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
            extra_body={"chat_template_kwargs": {"thinking": legacy_thinking_enabled}},
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
