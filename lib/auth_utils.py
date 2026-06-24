import os
import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Header, HTTPException, Depends

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = "HS256"

def create_token(user_id: str, workspace_id: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "workspace_id": workspace_id,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token_value(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_current_user(authorization: str = Header(None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    return decode_token_value(authorization.split(" ")[1])

def get_current_workspace_id(user: dict = Depends(get_current_user)) -> str:
    return user["workspace_id"]

def require_admin(user: dict = Depends(get_current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
