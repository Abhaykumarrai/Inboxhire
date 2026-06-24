import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, PlainTextResponse
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from lib.supabase_client import supabase
from lib.auth_utils import decode_token_value

router = APIRouter()

def build_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        redirect_uri=os.environ["GOOGLE_REDIRECT_URI"],
    )

def get_plan_limit(workspace_id: str) -> int:
    workspace = supabase.table("workspaces").select("plan_id").eq("id", workspace_id).single().execute().data
    if not workspace.get("plan_id"):
        return 1  # no active plan yet — allow exactly one connection for trial/testing
    plan = supabase.table("plans").select("max_gmail_connections").eq("id", workspace["plan_id"]).single().execute().data
    return plan["max_gmail_connections"]

@router.get("/api/auth/gmail/connect")
def gmail_connect(token: str):
    user = decode_token_value(token)
    workspace_id = user["workspace_id"]

    current_count = len(
        supabase.table("gmail_connections")
        .select("id")
        .eq("workspace_id", workspace_id)
        .eq("status", "connected")
        .execute()
        .data
    )
    max_allowed = get_plan_limit(workspace_id)

    if current_count >= max_allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Gmail connection limit reached ({max_allowed} for your plan). Upgrade to connect more inboxes.",
        )

    flow = build_flow()
    auth_url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=workspace_id)
    return RedirectResponse(auth_url)

@router.get("/api/auth/gmail/callback")
def gmail_callback(code: str | None = None, error: str | None = None, state: str | None = None):
    if error:
        return PlainTextResponse(f"Google returned an error: {error}", status_code=400)
    if not code or not state:
        return PlainTextResponse("Missing authorization code or state.", status_code=400)

    workspace_id = state

    flow = build_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials

    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    email = profile["emailAddress"]

    token_dict = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }

    existing = (
        supabase.table("gmail_connections")
        .select("id")
        .eq("workspace_id", workspace_id)
        .eq("gmail_email", email)
        .maybe_single()
        .execute()
    )

    if existing and existing.data:
        supabase.table("gmail_connections").update({
            "gmail_token": token_dict,
            "status": "connected",
        }).eq("id", existing.data["id"]).execute()
    else:
        supabase.table("gmail_connections").insert({
            "workspace_id": workspace_id,
            "gmail_email": email,
            "gmail_token": token_dict,
            "status": "connected",
        }).execute()

    return PlainTextResponse(f"Gmail connected: {email}. You can close this tab — scanning will start on the next poll.")
