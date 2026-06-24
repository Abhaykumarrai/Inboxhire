import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user

router = APIRouter()

def get_application_with_job(application_id: str):
    application = (
        supabase.table("applications")
        .select("*, jobs(workspace_id, created_by_user_id)")
        .eq("id", application_id)
        .single()
        .execute()
        .data
    )
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return application

def check_access(application: dict, user: dict):
    job = application.get("jobs")
    if not job or job["workspace_id"] != user["workspace_id"]:
        raise HTTPException(status_code=403, detail="Not authorized for this application")
    if user["role"] != "admin" and job.get("created_by_user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="Not authorized for this application")


class UpdateStageRequest(BaseModel):
    stage: str  # new | shortlisted | in_progress | rejected

@router.patch("/api/applications/{application_id}/stage")
def update_stage(application_id: str, data: UpdateStageRequest, user: dict = Depends(get_current_user)):
    application = get_application_with_job(application_id)
    check_access(application, user)

    valid_stages = {"new", "shortlisted", "in_progress", "rejected"}
    if data.stage not in valid_stages:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {', '.join(valid_stages)}")

    supabase.table("applications").update({"stage": data.stage}).eq("id", application_id).execute()
    return {"message": f"Stage updated to {data.stage}"}


class UpdateNoteRequest(BaseModel):
    note: str

@router.patch("/api/applications/{application_id}/note")
def update_note(application_id: str, data: UpdateNoteRequest, user: dict = Depends(get_current_user)):
    application = get_application_with_job(application_id)
    check_access(application, user)

    supabase.table("applications").update({"recruiter_note": data.note}).eq("id", application_id).execute()
    return {"message": "Note saved"}


class OverrideScoreRequest(BaseModel):
    override_score: int
    override_note: str | None = None

@router.patch("/api/applications/{application_id}/score")
def override_score(application_id: str, data: OverrideScoreRequest, user: dict = Depends(get_current_user)):
    application = get_application_with_job(application_id)
    check_access(application, user)

    if not (0 <= data.override_score <= 100):
        raise HTTPException(status_code=400, detail="Score must be between 0 and 100")

    supabase.table("scores").update({
        "overridden": True,
        "override_score": data.override_score,
        "override_note": data.override_note,
    }).eq("application_id", application_id).execute()
    return {"message": "Score overridden"}


@router.get("/api/applications/{application_id}/cv")
def get_cv_link(application_id: str, user: dict = Depends(get_current_user)):
    application = get_application_with_job(application_id)
    check_access(application, user)

    cv_document_id = application.get("cv_document_id")
    if not cv_document_id:
        raise HTTPException(status_code=404, detail="No CV on file for this application")

    doc = supabase.table("cv_documents").select("storage_path").eq("id", cv_document_id).single().execute().data
    if not doc or not doc.get("storage_path"):
        raise HTTPException(status_code=404, detail="CV file not found in storage")

    signed = supabase.storage.from_("cvs").create_signed_url(doc["storage_path"], 300)
    print(f">>> Raw signed URL response: {signed}")  # temporary — shows the real shape in your terminal

    raw_url = signed.get("signedURL") or signed.get("signedUrl") or signed.get("signed_url") or signed.get("signedurl")
    if not raw_url:
        raise HTTPException(status_code=500, detail=f"Could not generate file link: {signed}")

    if raw_url.startswith("http"):
        url = raw_url
    elif raw_url.startswith("/storage"):
        url = f"{os.environ['SUPABASE_URL'].rstrip('/')}{raw_url}"
    else:
        url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/storage/v1{raw_url}"

    return {"url": url, "expires_in_seconds": 300}
