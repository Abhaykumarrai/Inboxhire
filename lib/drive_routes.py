from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user

router = APIRouter()

def get_drive_connection(workspace_id: str):
    conn = supabase.table("drive_connections").select("*").eq("workspace_id", workspace_id).maybe_single().execute()
    if not conn or not conn.data:
        raise HTTPException(status_code=404, detail="No Drive connection found for this workspace")
    return conn.data

@router.get("/api/drive/connection")
def get_connection_status(user: dict = Depends(get_current_user)):
    conn = supabase.table("drive_connections").select("id, drive_email, folder_id, folder_name, status").eq("workspace_id", user["workspace_id"]).maybe_single().execute()
    return conn.data if conn else None

@router.get("/api/drive/folders")
def list_folders(user: dict = Depends(get_current_user)):
    conn = get_drive_connection(user["workspace_id"])
    creds = Credentials(**conn["drive_token"])
    service = build("drive", "v3", credentials=creds)

    results = service.files().list(
        q="mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id, name)", pageSize=100,
    ).execute()
    return results.get("files", [])


class SetFolderRequest(BaseModel):
    folder_id: str

@router.patch("/api/drive/folder")
def set_folder(data: SetFolderRequest, user: dict = Depends(get_current_user)):
    conn = get_drive_connection(user["workspace_id"])
    creds = Credentials(**conn["drive_token"])
    service = build("drive", "v3", credentials=creds)

    folder = service.files().get(fileId=data.folder_id, fields="id, name").execute()
    supabase.table("drive_connections").update({
        "folder_id": folder["id"], "folder_name": folder["name"],
    }).eq("workspace_id", user["workspace_id"]).execute()

    return {"message": f"Folder set to '{folder['name']}'. Scanning won't start until the next poll cycle."}


@router.delete("/api/drive/connection")
def disconnect_drive(user: dict = Depends(get_current_user)):
    supabase.table("drive_connections").update({"status": "disconnected"}).eq("workspace_id", user["workspace_id"]).execute()
    return {"message": "Drive disconnected"}
