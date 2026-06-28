from lib.supabase_client import supabase

DEFAULT_WEIGHTS = {
    "skills_weight": 35, "experience_weight": 25, "education_weight": 15,
    "location_weight": 10, "profile_weight": 10, "recency_weight": 5,
}

def get_scoring_weights(workspace_id: str) -> dict:
    row = supabase.table("scoring_weights").select("*").eq("workspace_id", workspace_id).maybe_single().execute()
    if row and row.data:
        return {k: row.data[k] for k in DEFAULT_WEIGHTS}
    return DEFAULT_WEIGHTS.copy()
