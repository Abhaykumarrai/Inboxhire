import secrets
import bcrypt
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_workspace_id, require_admin
from lib.email_service import send_credentials_email

router = APIRouter()

class InviteEmployeeRequest(BaseModel):
    name: str
    email: str

@router.post("/api/team/invite")
def invite_employee(data: InviteEmployeeRequest, admin: dict = Depends(require_admin)):
    existing = supabase.table("users").select("id").eq("email", data.email).maybe_single().execute()
    if existing and existing.data:
        raise HTTPException(status_code=400, detail="A user with this email already exists.")

    temp_password = secrets.token_urlsafe(10)
    password_hash = bcrypt.hashpw(temp_password.encode(), bcrypt.gensalt()).decode()

    user = supabase.table("users").insert({
        "workspace_id": admin["workspace_id"],
        "name": data.name,
        "email": data.email,
        "password_hash": password_hash,
        "role": "employee",
        "must_change_password": True,
    }).execute().data[0]

    send_credentials_email(data.email, data.name, temp_password)
    return {"message": "Employee invited.", "user_id": user["id"]}

@router.get("/api/team")
def list_team(workspace_id: str = Depends(get_current_workspace_id)):
    return supabase.table("users").select("id, name, email, role, created_at").eq("workspace_id", workspace_id).execute().data

@router.delete("/api/team/{user_id}")
def remove_employee(user_id: str, admin: dict = Depends(require_admin)):
    supabase.table("users").delete().eq("id", user_id).eq("workspace_id", admin["workspace_id"]).execute()
    return {"message": "Employee removed."}
