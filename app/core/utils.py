from bson import ObjectId
from fastapi import HTTPException, status

def parse_object_id(id_value: str, field_name: str) -> ObjectId:
    try:
        return ObjectId(id_value)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name}",
        ) from exc

def serialize_conversation(conv: dict, include_messages: bool = True) -> dict:
    data = {
        "id": str(conv["_id"]),
        "user_id": conv.get("user_id"),
        "created_at": conv.get("created_at"),
        "updated_at": conv.get("updated_at"),
        "message_count": len(conv.get("messages", [])),
    }
    if include_messages:
        data["messages"] = conv.get("messages", [])
        data["last_message"] = conv.get("messages", [])[-1] if conv.get("messages") else None
    return data