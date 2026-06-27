import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from lib.supabase_client import supabase
from lib.auth_utils import decode_token_value
from lib.connection_limits import get_source_limits, count_connected

router = APIRouter()

def build_drive_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
        redirect_uri=os.environ["GOOGLE_DRIVE_REDIRECT_URI"],
        autogenerate_code_verifier=False,
    )

@router.get("/api/drive/connect")
def drive_connect(token: str):
    workspace_id = decode_token_value(token)["workspace_id"]

    existing = supabase.table("drive_connections").select("id").eq("workspace_id", workspace_id).maybe_single().execute()
    if existing and existing.data:
        raise HTTPException(status_code=400, detail="A Drive connection already exists. Disconnect it first to connect a different account.")

    limits = get_source_limits(workspace_id)
    counts = count_connected(workspace_id)

    if limits["combined_cap"] is not None:
        if counts["gmail"] + counts["drive"] >= limits["combined_cap"]:
            raise HTTPException(status_code=400, detail="Your plan allows Gmail OR Drive, not both. Disconnect Gmail first, or upgrade.")
    elif limits["max_drive"] < 1:
        raise HTTPException(status_code=400, detail="Google Drive isn't included in your current plan. Upgrade to connect Drive.")

    flow = build_drive_flow()
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=workspace_id)
    return RedirectResponse(auth_url)

@router.get("/api/drive/callback")
def drive_callback(code: str | None = None, error: str | None = None, state: str | None = None):
    if error:
        return PlainTextResponse(f"Google returned an error: {error}", status_code=400)
    if not code or not state:
        return PlainTextResponse("Missing authorization code or state.", status_code=400)

    workspace_id = state
    flow = build_drive_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    drive_service = build("drive", "v3", credentials=creds)
    about = drive_service.about().get(fields="user").execute()
    email = about["user"]["emailAddress"]

    token_dict = {
        "token": creds.token, "refresh_token": creds.refresh_token, "token_uri": creds.token_uri,
        "client_id": creds.client_id, "client_secret": creds.client_secret, "scopes": creds.scopes,
    }

    supabase.table("drive_connections").insert({
        "workspace_id": workspace_id, "drive_email": email, "drive_token": token_dict, "status": "connected",
    }).execute()

    return PlainTextResponse(f"Google Drive connected: {email}. Next, choose a folder to scan.")
