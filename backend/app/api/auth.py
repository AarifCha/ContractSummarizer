from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.core.auth import create_session, delete_session, hash_password, verify_password
from app.core.db import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class AuthRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class AuthResponse(BaseModel):
    token: str
    email: EmailStr


@router.post("/register", response_model=AuthResponse)
def register(payload: AuthRequest):
    with get_db() as conn:
        exists = conn.execute("SELECT id FROM users WHERE email = ?", (payload.email.lower(),)).fetchone()
        if exists:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

        cursor = conn.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, datetime('now'))",
            (payload.email.lower(), hash_password(payload.password)),
        )
        conn.commit()
        user_id = cursor.lastrowid

    token = create_session(user_id)
    return {"token": token, "email": payload.email.lower()}


@router.post("/login", response_model=AuthResponse)
def login(payload: AuthRequest):
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash FROM users WHERE email = ?",
            (payload.email.lower(),),
        ).fetchone()
    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_session(row["id"])
    return {"token": token, "email": row["email"]}


class LogoutRequest(BaseModel):
    token: str


@router.post("/logout")
def logout(payload: LogoutRequest):
    delete_session(payload.token)
    return {"ok": True}
