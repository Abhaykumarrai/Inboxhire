from lib.supabase_client import supabase
from lib.scorer import calculate_score

def find_current_role(experience: list) -> dict | None:
    for role in experience:
        if (role.get("to") or "").strip().lower() == "present":
            return role
    return experience[0] if experience else None

def build_candidate_fields(parsed: dict) -> dict:
    experience = parsed.get("experience") or []
    education = parsed.get("education") or []
    current_role = find_current_role(experience)

    return {
        "experience_json": experience,
        "education_json": education,
        "total_experience_years": parsed.get("total_exp_years"),
        "current_company": current_role.get("company") if current_role else None,
        "current_designation": current_role.get("title") if current_role else None,
        "college_university": education[0].get("institution") if education else None,
        "education_to_year": education[0].get("year") or None,
    }

def match_jobs_for_document(workspace_id, cv_document_id, sender_email, parsed, job_ids, cv_path=None):
    effective_email = sender_email or parsed.get("email")

    candidate_data = None
    if effective_email:
        candidate = supabase.table("candidates").select("id, name, phone").eq("workspace_id", workspace_id).eq("email", effective_email).maybe_single().execute()
        candidate_data = candidate.data if candidate else None

    if not candidate_data:
        candidate_data = supabase.table("candidates").insert({
            "workspace_id": workspace_id,
            "email": effective_email,
            "name": parsed.get("name"),
            "phone": parsed.get("phone"),
            "raw_cv_url": cv_path,
        }).execute().data[0]
    elif not candidate_data.get("name") or not candidate_data.get("phone"):
        # Existing candidate, but missing name/phone from an earlier incomplete write — backfill now, free
        updates = {}
        if not candidate_data.get("name") and parsed.get("name"):
            updates["name"] = parsed.get("name")
        if not candidate_data.get("phone") and parsed.get("phone"):
            updates["phone"] = parsed.get("phone")
        if updates:
            supabase.table("candidates").update(updates).eq("id", candidate_data["id"]).execute()
            candidate_data.update(updates)
    for job_id in job_ids:
        existing = supabase.table("applications").select("id").eq("job_id", job_id).eq("cv_document_id", cv_document_id).maybe_single().execute()
        if existing and existing.data:
            continue  # this exact CV already scored against this job

        application = supabase.table("applications").insert({
            "candidate_id": candidate_data["id"],
            "job_id": job_id,
            "cv_document_id": cv_document_id,
            "parse_status": "parsed",
        }).execute().data[0]

        supabase.table("parsed_profiles").insert({
            "application_id": application["id"],
            "skills": parsed.get("skills"),
            "experience_json": parsed.get("experience"),
            "education_json": parsed.get("education"),
            "total_exp_years": parsed.get("total_exp_years"),
            "location": parsed.get("location"),
            "linkedin_url": parsed.get("linkedin_url"),
            "raw_text": parsed.get("raw_text"),
        }).execute()

        score = calculate_score(parsed, job_id)
        supabase.table("scores").insert({"application_id": application["id"], **score}).execute()
