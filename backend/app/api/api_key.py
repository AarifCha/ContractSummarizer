from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.api_keys import delete_user_api_key, get_user_api_key, save_user_api_key
from app.core.deps import current_user

router = APIRouter(prefix="/api-key", tags=["api-key"])


class SaveApiKeyRequest(BaseModel):
    api_key: str = Field(min_length=1)


@router.get("")
def get_api_key_status(user=Depends(current_user)):
    api_key = get_user_api_key(user["id"])
    if not api_key:
        return {"has_key": False, "masked_key": None}
    masked = "********" if len(api_key) <= 8 else f"{api_key[:4]}...{api_key[-4:]}"
    return {"has_key": True, "masked_key": masked}


@router.post("")
def upsert_api_key(payload: SaveApiKeyRequest, user=Depends(current_user)):
    trimmed = payload.api_key.strip()
    if not trimmed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API key cannot be empty")
    save_user_api_key(user["id"], trimmed)
    masked = "********" if len(trimmed) <= 8 else f"{trimmed[:4]}...{trimmed[-4:]}"
    return {"ok": True, "masked_key": masked}


@router.delete("")
def remove_api_key(user=Depends(current_user)):
    delete_user_api_key(user["id"])
    return {"ok": True}
