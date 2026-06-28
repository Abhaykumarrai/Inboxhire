from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user, require_admin
from lib.scoring_weights import DEFAULT_WEIGHTS, get_scoring_weights

router = APIRouter()

@router.get("/api/settings/scoring-weights")
def get_weights(user: dict = Depends(get_current_user)):
    return get_scoring_weights(user["workspace_id"])


class UpdateWeightsRequest(BaseModel):
    skills_weight: int
    experience_weight: int
    education_weight: int
    location_weight: int
    profile_weight: int
    recency_weight: int

@router.put("/api/settings/scoring-weights")
def update_weights(data: UpdateWeightsRequest, admin: dict = Depends(require_admin)):
    values = data.model_dump()
    if any(w < 0 for w in values.values()):
        raise HTTPException(status_code=400, detail="Weights cannot be negative.")
    total = sum(values.values())
    if total != 100:
        raise HTTPException(status_code=400, detail=f"Weights must sum to exactly 100 (currently {total}).")

    supabase.table("scoring_weights").upsert(
        {"workspace_id": admin["workspace_id"], **values}, on_conflict="workspace_id"
    ).execute()
    return {"message": "Scoring weights updated. New candidate scores will use these immediately — existing candidates are not retroactively rescored."}


@router.post("/api/settings/scoring-weights/reset")
def reset_weights(admin: dict = Depends(require_admin)):
    supabase.table("scoring_weights").delete().eq("workspace_id", admin["workspace_id"]).execute()
    return {"message": "Scoring weights reset to defaults", "weights": DEFAULT_WEIGHTS}
