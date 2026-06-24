from datetime import date, timedelta
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user

router = APIRouter()

TEST_WORKSPACE_ID = "d935b95d-33a8-4777-b715-0db49378ac5e"  # same one as before

def get_max_jobs(workspace_id: str) -> int:
    workspace = supabase.table("workspaces").select("plan_id").eq("id", workspace_id).single().execute().data
    if not workspace.get("plan_id"):
        return 1
    plan = supabase.table("plans").select("max_jobs").eq("id", workspace["plan_id"]).single().execute().data
    return plan["max_jobs"]

class CreateJobRequest(BaseModel):
    title: str
    designation: str | None = None
    company_name: str | None = None
    offer_designation: str | None = None
    required_skills: list[str] = []
    nice_skills: list[str] = []
    exp_min: int = 0
    exp_max: int = 99
    education: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    email_filter: str | None = None
    gmail_connection_id: str
    scan_from_date: date | None = None
    scan_to_date: date | None = None

@router.post("/api/jobs")
def create_job(data: CreateJobRequest, user: dict = Depends(get_current_user)):
    workspace_id = user["workspace_id"]
    max_jobs = get_max_jobs(workspace_id)
    current_count = len(supabase.table("jobs").select("id").eq("workspace_id", workspace_id).execute().data)
    if current_count >= max_jobs:
        raise HTTPException(status_code=400, detail=f"Job limit reached ({max_jobs}). Upgrade to create more.")

    connection = supabase.table("gmail_connections").select("*").eq("id", data.gmail_connection_id).eq("workspace_id", workspace_id).single().execute().data
    if not connection:
        raise HTTPException(status_code=404, detail="Gmail connection not found")
    if user["role"] != "admin" and connection.get("assigned_user_id") != user["user_id"]:
        raise HTTPException(status_code=403, detail="This Gmail connection isn't assigned to you")

    yesterday = date.today() - timedelta(days=1)
    job_data = data.model_dump(exclude={"scan_from_date", "scan_to_date"})
    job_data["scan_from_date"] = (data.scan_from_date or yesterday).isoformat()
    job_data["scan_to_date"] = (data.scan_to_date or yesterday).isoformat()
    job_data["workspace_id"] = workspace_id
    job_data["created_by_user_id"] = user["user_id"]
    job_data["status"] = "active"

    return supabase.table("jobs").insert(job_data).execute().data[0]

@router.get("/api/jobs")
def list_jobs():
    jobs = (
        supabase.table("jobs")
        .select("*")
        .eq("workspace_id", TEST_WORKSPACE_ID)
        .execute()
        .data
    )
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
