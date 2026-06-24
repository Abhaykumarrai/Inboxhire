import secrets
import bcrypt
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.email_service import send_credentials_email, send_password_reset_email
from lib.auth_utils import create_token, get_current_user

router = APIRouter()

class SignupRequest(BaseModel):
    owner_name: str
    email: str
    organization: str
    industry: str
    location: str
    employee_count: str

@router.post("/api/auth/signup")
def signup(data: SignupRequest):
    existing = supabase.table("users").select("id").eq("email", data.email).maybe_single().execute()
    if existing and existing.data:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    workspace = supabase.table("workspaces").insert({
        "name": data.organization,
        "organization": data.organization,
        "industry": data.industry,
        "location": data.location,
        "employee_count": data.employee_count,
        "plan": "starter",
    }).execute().data[0]

    temp_password = secrets.token_urlsafe(10)
    password_hash = bcrypt.hashpw(temp_password.encode(), bcrypt.gensalt()).decode()

    supabase.table("users").insert({
        "workspace_id": workspace["id"],
        "name": data.owner_name,
        "email": data.email,
        "password_hash": password_hash,
        "role": "admin",
        "must_change_password": True,
    }).execute()

    send_credentials_email(data.email, data.owner_name, temp_password)
    return {"message": "Account created. Check your email for login credentials.", "workspace_id": workspace["id"]}


class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/api/auth/login")
def login(data: LoginRequest):
    result = supabase.table("users").select("*").eq("email", data.email).maybe_single().execute()
    user = result.data if result else None

    if not user or not user.get("password_hash") or not bcrypt.checkpw(data.password.encode(), user["password_hash"].encode()):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_token(user["id"], user["workspace_id"], user["role"])
    return {
        "token": token,
        "must_change_password": user.get("must_change_password", False),
        "role": user["role"],
        "name": user.get("name"),
    }


class ChangePasswordRequest(BaseModel):
    new_password: str

@router.post("/api/auth/change-password")
def change_password(data: ChangePasswordRequest, user: dict = Depends(get_current_user)):
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    new_hash = bcrypt.hashpw(data.new_password.encode(), bcrypt.gensalt()).decode()
    supabase.table("users").update({"password_hash": new_hash, "must_change_password": False}).eq("id", user["user_id"]).execute()
    return {"message": "Password changed successfully"}


class ForgotPasswordRequest(BaseModel):
    email: str

@router.post("/api/auth/forgot-password")
def forgot_password(data: ForgotPasswordRequest):
    result = supabase.table("users").select("id, name").eq("email", data.email).maybe_single().execute()
    user = result.data if result else None
    if not user:
        return {"message": "If an account exists with this email, a reset link has been sent."}

    reset_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    supabase.table("users").update({"reset_token": reset_token, "reset_token_expires_at": expires_at}).eq("id", user["id"]).execute()
    send_password_reset_email(data.email, user.get("name", ""), reset_token)
    return {"message": "If an account exists with this email, a reset link has been sent."}


class ResetPasswordRequest(BaseModel):
    reset_token: str
    new_password: str

@router.post("/api/auth/reset-password")
def reset_password(data: ResetPasswordRequest):
    result = supabase.table("users").select("id, reset_token_expires_at").eq("reset_token", data.reset_token).maybe_single().execute()
    user = result.data if result else None
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    if datetime.now(timezone.utc) > datetime.fromisoformat(user["reset_token_expires_at"]):
        raise HTTPException(status_code=400, detail="Reset token has expired")
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    new_hash = bcrypt.hashpw(data.new_password.encode(), bcrypt.gensalt()).decode()
    supabase.table("users").update({
        "password_hash": new_hash, "must_change_password": False, "reset_token": None, "reset_token_expires_at": None,
    }).eq("id", user["id"]).execute()
    return {"message": "Password reset successfully. You can now log in."}
