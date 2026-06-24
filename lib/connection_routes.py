from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user, get_current_workspace_id, require_admin

router = APIRouter()

@router.get("/api/gmail-connections")
def list_connections(user: dict = Depends(get_current_user)):
    query = supabase.table("gmail_connections").select("id, gmail_email, status, assigned_user_id, created_at").eq("workspace_id", user["workspace_id"])
    if user["role"] != "admin":
        query = query.eq("assigned_user_id", user["user_id"])
    return query.execute().data

class AssignConnectionRequest(BaseModel):
    user_id: str

@router.patch("/api/gmail-connections/{connection_id}/assign")
def assign_connection(connection_id: str, data: AssignConnectionRequest, admin: dict = Depends(require_admin)):
    target = supabase.table("users").select("id, workspace_id").eq("id", data.user_id).single().execute().data
    if not target or target["workspace_id"] != admin["workspace_id"]:
        raise HTTPException(status_code=403, detail="Cannot assign to a user outside your workspace")
    supabase.table("gmail_connections").update({"assigned_user_id": data.user_id}).eq("id", connection_id).eq("workspace_id", admin["workspace_id"]).execute()
    return {"message": "Connection assigned."}

@router.delete("/api/gmail-connections/{connection_id}")
def disconnect(connection_id: str, workspace_id: str = Depends(get_current_workspace_id)):
    supabase.table("gmail_connections").update({"status": "disconnected"}).eq("id", connection_id).eq("workspace_id", workspace_id).execute()
    return {"message": "Disconnected"}
