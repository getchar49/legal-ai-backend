from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_current_user
from app.core.database import conversations_collection
from app.core.utils import parse_object_id, serialize_conversation

router = APIRouter()

@router.get("/")
async def get_history(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    cursor = conversations_collection.find({"user_id": user_id}).sort("updated_at", -1)
    conversations = await cursor.to_list(length=200)
    return [serialize_conversation(conv, include_messages=True) for conv in conversations]

@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_history(current_user: dict = Depends(get_current_user)):
    user_id = current_user["id"]
    now = datetime.now(timezone.utc)
    new_doc = {
        "user_id": user_id,
        "messages": [],
        "created_at": now,
        "updated_at": now,
    }
    inserted = await conversations_collection.insert_one(new_doc)
    conversation = {**new_doc, "_id": inserted.inserted_id}
    return serialize_conversation(conversation, include_messages=True)

@router.get("/{conversation_id}")
async def get_history_detail(
    conversation_id: str, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]
    conversation_oid = parse_object_id(conversation_id, "conversation_id")
    conversation = await conversations_collection.find_one(
        {"_id": conversation_oid, "user_id": user_id}
    )
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return serialize_conversation(conversation, include_messages=True)

@router.delete("/{conversation_id}")
async def delete_history(
    conversation_id: str, current_user: dict = Depends(get_current_user)
):
    user_id = current_user["id"]
    conversation_oid = parse_object_id(conversation_id, "conversation_id")
    deleted = await conversations_collection.delete_one(
        {"_id": conversation_oid, "user_id": user_id}
    )
    if deleted.deleted_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return {"message": "Conversation deleted successfully", "id": conversation_id}