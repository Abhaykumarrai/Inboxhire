from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user
from lib.email_service import send_candidate_email

router = APIRouter()

class BulkEmailRequest(BaseModel):
    application_ids: list[str]
    subject: str
    body_html: str

@router.post("/api/applications/email")
def send_bulk_email(data: BulkEmailRequest, user: dict = Depends(get_current_user)):
    workspace = supabase.table("workspaces").select("emails_sent_this_cycle, emails_limit").eq("id", user["workspace_id"]).single().execute().data
    remaining = (workspace.get("emails_limit") or 0) - (workspace.get("emails_sent_this_cycle") or 0)

    if len(data.application_ids) > remaining:
        raise HTTPException(status_code=400, detail=f"Email quota exceeded. {remaining} emails remaining this cycle, {len(data.application_ids)} requested.")

    sender = supabase.table("users").select("email").eq("id", user["user_id"]).single().execute().data
    sender_email = sender["email"] if sender else None

    sent, failed = [], []
    for app_id in data.application_ids:
        application = (
            supabase.table("applications")
            .select("id, candidates(email), jobs(workspace_id)")
            .eq("id", app_id)
            .single()
            .execute()
            .data
        )
        if not application or application["jobs"]["workspace_id"] != user["workspace_id"]:
            failed.append(app_id)
            continue
        try:
            send_candidate_email(application["candidates"]["email"], data.subject, data.body_html, reply_to=sender_email)
            sent.append(app_id)
        except Exception:
            failed.append(app_id)

    supabase.table("workspaces").update({
        "emails_sent_this_cycle": (workspace.get("emails_sent_this_cycle") or 0) + len(sent)
    }).eq("id", user["workspace_id"]).execute()

    return {"sent": len(sent), "failed": len(failed), "failed_ids": failed}
