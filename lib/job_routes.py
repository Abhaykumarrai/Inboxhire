from datetime import date, datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user
from lib.scorer import calculate_score
from lib.cron_routes import scan_gmail_job, scan_drive_job

router = APIRouter()

def get_max_jobs(workspace_id: str) -> int:
    workspace = supabase.table("workspaces").select("plan_id").eq("id", workspace_id).single().execute().data
    if not workspace.get("plan_id"):
        return 1
    plan = supabase.table("plans").select("max_jobs").eq("id", workspace["plan_id"]).single().execute().data
    return plan["max_jobs"]

def rescore_existing_applications(job_id: str):
    applications = supabase.table("applications").select("id, cv_document_id").eq("job_id", job_id).execute().data
    for app in applications:
        if not app.get("cv_document_id"):
            continue
        doc = supabase.table("cv_documents").select("parsed_json").eq("id", app["cv_document_id"]).single().execute().data
        if not doc or not doc.get("parsed_json"):
            continue
        score = calculate_score(doc["parsed_json"], job_id)
        existing = supabase.table("scores").select("id").eq("application_id", app["id"]).maybe_single().execute()
        if existing and existing.data:
            supabase.table("scores").update(score).eq("application_id", app["id"]).execute()
        else:
            supabase.table("scores").insert({"application_id": app["id"], **score}).execute()

class CreateJobRequest(BaseModel):
    title: str
    required_skills: list[str] = []
    nice_skills: list[str] = []
    exp_min: int = 0
    exp_max: int = 99
    education: str | None = None
    location: str | None = None
    email_filter: str = "CV"
    source_type: Literal["gmail", "drive", "api"]
    source_connection_id: str | None = None
    scan_from_date: date | None = None
    scan_to_date: date | None = None

class UpdateJobRequest(BaseModel):
    title: str | None = None
    required_skills: list[str] | None = None
    nice_skills: list[str] | None = None
    exp_min: int | None = None
    exp_max: int | None = None
    education: str | None = None
    location: str | None = None
    scan_from_date: date | None = None
    scan_to_date: date | None = None

@router.post("/api/jobs")
def create_job(data: CreateJobRequest, user: dict = Depends(get_current_user)):
    workspace_id = user["workspace_id"]
    max_jobs = get_max_jobs(workspace_id)
    if len(supabase.table("jobs").select("id").eq("workspace_id", workspace_id).execute().data) >= max_jobs:
        raise HTTPException(status_code=400, detail=f"Job limit reached ({max_jobs}). Upgrade to create more.")
    if data.exp_min > data.exp_max:
        raise HTTPException(status_code=400, detail="exp_min cannot be greater than exp_max")

    yesterday = date.today() - timedelta(days=1)
    scan_from = data.scan_from_date or yesterday
    scan_to = data.scan_to_date or yesterday
    if scan_from > scan_to:
        raise HTTPException(status_code=400, detail="scan_from_date cannot be after scan_to_date")

    job_data = data.model_dump(exclude={"scan_from_date", "scan_to_date", "source_connection_id"})
    job_data.update({
        "scan_from_date": scan_from.isoformat(), "scan_to_date": scan_to.isoformat(),
        "workspace_id": workspace_id, "created_by_user_id": user["user_id"], "status": "active",
        "gmail_connection_id": None, "drive_connection_id": None,
    })

    if data.source_type == "gmail":
        if not data.source_connection_id:
            raise HTTPException(status_code=400, detail="source_connection_id is required for source_type 'gmail'")
        conn = supabase.table("gmail_connections").select("*").eq("id", data.source_connection_id).eq("workspace_id", workspace_id).single().execute().data
        if not conn:
            raise HTTPException(status_code=404, detail="Gmail connection not found")
        if user["role"] != "admin" and conn.get("assigned_user_id") != user["user_id"]:
            raise HTTPException(status_code=403, detail="This Gmail connection isn't assigned to you")
        job_data["gmail_connection_id"] = data.source_connection_id

    elif data.source_type == "drive":
        conn = supabase.table("drive_connections").select("*").eq("workspace_id", workspace_id).eq("status", "connected").maybe_single().execute()
        if not conn or not conn.data:
            raise HTTPException(status_code=404, detail="No connected Drive account found")
        if not conn.data.get("folder_id"):
            raise HTTPException(status_code=400, detail="Drive is connected but no folder has been chosen yet")
        job_data["drive_connection_id"] = conn.data["id"]

    elif data.source_type == "api":
        api_conn = supabase.table("api_connections").select("id").eq("workspace_id", workspace_id).maybe_single().execute()
        if not api_conn or not api_conn.data:
            raise HTTPException(status_code=400, detail="API connection isn't set up yet. This source is coming soon.")
        # Note: job will be created, but no ingestion logic exists for this source yet — that's next phase.

    return supabase.table("jobs").insert(job_data).execute().data[0]

@router.get("/api/sources/available")
def list_available_sources(user: dict = Depends(get_current_user)):
    gmail = supabase.table("gmail_connections").select("id, gmail_email, status, assigned_user_id").eq("workspace_id", user["workspace_id"]).eq("status", "connected").execute().data
    if user["role"] != "admin":
        gmail = [g for g in gmail if g.get("assigned_user_id") == user["user_id"]]

    drive_row = supabase.table("drive_connections").select("id, drive_email, folder_name, status").eq("workspace_id", user["workspace_id"]).eq("status", "connected").maybe_single().execute()
    drive = drive_row.data if drive_row and drive_row.data and drive_row.data.get("folder_name") else None

    api_row = supabase.table("api_connections").select("id, status").eq("workspace_id", user["workspace_id"]).maybe_single().execute()

    return {"gmail": gmail, "drive": drive, "api": api_row.data if api_row else None}

@router.get("/api/jobs")
def list_jobs(user: dict = Depends(get_current_user)):
    jobs = (
        supabase.table("jobs")
        .select("*")
        .eq("workspace_id", user["workspace_id"])
        .execute()
        .data
    )
    for job in jobs:
        applications = (
            supabase.table("applications")
            .select("stage, scores(total)")
            .eq("job_id", job["id"])
            .execute()
            .data
        )
        job["total_found"] = len(applications)
        job["shortlisted"] = sum(1 for a in applications if a.get("stage") == "shortlisted")
        job["new_found"] = sum(1 for a in applications if a.get("stage") == "new")
    return jobs

@router.get("/api/jobs/{job_id}/candidates")
def get_candidates(job_id: str, min_score: int = 0):
    applications = (
        supabase.table("applications")
        .select("id, stage, received_at, recruiter_note, candidates(name, email, phone, raw_cv_url), parsed_profiles(skills, experience_json, education_json, total_exp_years, location), scores(total, skills_score, exp_score, edu_score, profile_score, recency_score, breakdown_json)")
        .eq("job_id", job_id)
        .execute()
        .data
    )

    filtered = [
        app for app in applications
        if app.get("scores") and app["scores"].get("total", 0) >= min_score
    ]
    filtered.sort(key=lambda a: a["scores"]["total"] if a.get("scores") else 0, reverse=True)
    return filtered

@router.post("/api/jobs/{job_id}/scan")
def trigger_scan(job_id: str, user: dict = Depends(get_current_user)):
    job = supabase.table("jobs").select("*").eq("id", job_id).eq("workspace_id", user["workspace_id"]).single().execute().data
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user["role"] != "admin" and job.get("created_by_user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="You can only scan jobs you created")
    if job["status"] == "closed":
        raise HTTPException(status_code=400, detail="This job is closed. Reopen it before scanning.")

    if job.get("last_scanned_at"):
        last_scanned = datetime.fromisoformat(job["last_scanned_at"])
        if (datetime.now(timezone.utc) - last_scanned).total_seconds() < 30:
            raise HTTPException(status_code=429, detail="This job was just scanned. Please wait a moment before trying again.")

    if job.get("gmail_connection_id"):
        scan_gmail_job(job)
    elif job.get("drive_connection_id"):
        scan_drive_job(job)
    else:
        raise HTTPException(status_code=400, detail="This job has no scan source configured.")

    return {"message": "Scan triggered successfully", "job_id": job_id}

@router.patch("/api/jobs/{job_id}")
def update_job(job_id: str, data: UpdateJobRequest, user: dict = Depends(get_current_user)):
    job = supabase.table("jobs").select("*").eq("id", job_id).eq("workspace_id", user["workspace_id"]).single().execute().data
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user["role"] != "admin" and job.get("created_by_user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="You can only edit jobs you created")

    updates = data.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    new_exp_min = updates.get("exp_min", job["exp_min"])
    new_exp_max = updates.get("exp_max", job["exp_max"])
    if new_exp_min > new_exp_max:
        raise HTTPException(status_code=400, detail="exp_min cannot be greater than exp_max")

    if "scan_from_date" in updates:
        updates["scan_from_date"] = updates["scan_from_date"].isoformat()
    if "scan_to_date" in updates:
        updates["scan_to_date"] = updates["scan_to_date"].isoformat()

    new_from = updates.get("scan_from_date", job.get("scan_from_date"))
    new_to = updates.get("scan_to_date", job.get("scan_to_date"))
    if new_from and new_to and str(new_from) > str(new_to):
        raise HTTPException(status_code=400, detail="scan_from_date cannot be after scan_to_date")

    updated_job = supabase.table("jobs").update(updates).eq("id", job_id).execute().data[0]

    SCORE_AFFECTING_FIELDS = {"required_skills", "nice_skills", "exp_min", "exp_max", "education", "location"}
    if SCORE_AFFECTING_FIELDS & updates.keys():
        rescore_existing_applications(job_id)

    return updated_job

@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: str, user: dict = Depends(get_current_user)):
    job = supabase.table("jobs").select("id, created_by_user_id").eq("id", job_id).eq("workspace_id", user["workspace_id"]).single().execute().data
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user["role"] != "admin" and job.get("created_by_user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="You can only delete jobs you created")

    supabase.table("jobs").delete().eq("id", job_id).execute()
    return {"message": "Job deleted"}

@router.patch("/api/jobs/{job_id}/close")
def close_job(job_id: str, user: dict = Depends(get_current_user)):
    job = supabase.table("jobs").select("id, created_by_user_id").eq("id", job_id).eq("workspace_id", user["workspace_id"]).single().execute().data
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user["role"] != "admin" and job.get("created_by_user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="You can only close jobs you created")
    supabase.table("jobs").update({"status": "closed"}).eq("id", job_id).execute()
    return {"message": "Job marked inactive. No further scanning — manual or automatic — will occur."}

@router.patch("/api/jobs/{job_id}/reopen")
def reopen_job(job_id: str, user: dict = Depends(get_current_user)):
    job = supabase.table("jobs").select("id, created_by_user_id").eq("id", job_id).eq("workspace_id", user["workspace_id"]).single().execute().data
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if user["role"] != "admin" and job.get("created_by_user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="You can only reopen jobs you created")
    supabase.table("jobs").update({"status": "active"}).eq("id", job_id).execute()
    return {"message": "Job reopened and active again"}
