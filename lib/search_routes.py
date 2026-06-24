import json
import anthropic
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user
from lib.skill_matching import build_requirements, compute_match_percentage, summarize_requirements

router = APIRouter()
client = anthropic.Anthropic()

SEARCH_PARSE_PROMPT = """Convert the recruiter's natural language candidate search into a strict JSON filter. Return ONLY JSON, no markdown, no explanation.

{
  "required_skills": [],
  "nice_skills": [],
  "exp_min": 0,
  "exp_max": 99,
  "education": "",
  "location": "",
  "min_match_percentage": 0,
  "max_results": 20
}

Rules:
- required_skills: bare skill names mentioned as must-haves (e.g. "Java developer" -> ["Java"]).
- exp_min/exp_max: from phrases like "4 years experience" -> exp_min=4, exp_max=99. A range like "4-6 years" sets both.
- location: city/region mentioned, empty string if none mentioned.
- min_match_percentage: from phrases like "80% above match" -> 80. Default 0 if not mentioned.
- max_results: default 20 unless a specific number is mentioned (e.g. "top 5" -> 5).
- Never invent values not implied by the query.
"""

def parse_search_query(query: str) -> dict:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=SEARCH_PARSE_PROMPT,
        messages=[{"role": "user", "content": query}],
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


class SearchRequest(BaseModel):
    query: str

LOWER_FLOOR = 40  # below this, a candidate is just too irrelevant to show at all

def evaluate_location(filter_location: str, candidate_location: str) -> str:
    if not filter_location:
        return "not_requested"
    if not candidate_location:
        return "unknown"
    if filter_location.lower() in candidate_location.lower():
        return "matched"
    return "mismatch"

def build_candidate_note(summary: dict, location_status: str, filter_location: str) -> str:
    parts = []
    if not summary["gaps"] and not summary["partial"]:
        parts.append("meets all listed requirements")
    else:
        if summary["gaps"]:
            parts.append(f"missing {', '.join(summary['gaps'])}")
        if summary["partial"]:
            parts.append(f"partial match on {', '.join(summary['partial'])}")
    if location_status == "unknown" and filter_location:
        parts.append(f"location not specified on resume (you asked for {filter_location})")
    elif location_status == "matched" and filter_location:
        parts.append(f"confirmed in {filter_location}")
    return "; ".join(parts) if parts else "strong overall match"


def run_candidate_search(query: str, workspace_id: str) -> dict:
    filters = parse_search_query(query)
    min_threshold = filters.get("min_match_percentage") or 0

    docs = supabase.table("cv_documents").select("*").eq("workspace_id", workspace_id).execute().data

    results = []
    for doc in docs:
        parsed = doc.get("parsed_json") or {}
        requirements = build_requirements(parsed, filters)
        match_pct = compute_match_percentage(requirements)
        summary = summarize_requirements(requirements)

        location_status = evaluate_location(filters.get("location"), parsed.get("location"))
        if location_status == "mismatch":
            continue
        if match_pct < LOWER_FLOOR:
            continue

        if match_pct == 100 and location_status in ("matched", "not_requested"):
            tier = "exact"
        elif match_pct >= min_threshold:
            tier = "strong"
        else:
            tier = "below_threshold"

        results.append({
            "cv_document_id": doc["id"],
            "candidate_email": doc.get("candidate_email"),
            "name": parsed.get("name"),
            "location": parsed.get("location"),
            "total_exp_years": parsed.get("total_exp_years"),
            "match_percentage": match_pct,
            "tier": tier,
            "note": build_candidate_note(summary, location_status, filters.get("location")),
            "requirements": requirements,
        })

    results.sort(key=lambda r: r["match_percentage"], reverse=True)

    exact = [r for r in results if r["tier"] == "exact"]
    strong = [r for r in results if r["tier"] == "strong"]
    below = [r for r in results if r["tier"] == "below_threshold"]

    parts = []
    if exact:
        parts.append(f"{len(exact)} exact match{'es' if len(exact) != 1 else ''} meeting every requirement")
    if strong:
        parts.append(f"{len(strong)} strong match{'es' if len(strong) != 1 else ''} ({min_threshold}%+) with minor gaps worth reviewing")
    if below:
        parts.append(f"{len(below)} more below your {min_threshold}% threshold but close enough to consider")
    message = ("Found " + ", and ".join(parts) + ".") if parts else "No candidates matched closely enough to show."

    max_results = filters.get("max_results") or 20
    return {
        "filters_used": filters,
        "message": message,
        "exact_matches": exact[:max_results],
        "strong_matches": strong[:max_results],
        "below_threshold": below[:max_results],
    }


@router.post("/api/search/candidates")
def search_candidates(data: SearchRequest, user: dict = Depends(get_current_user)):
    return run_candidate_search(data.query, user["workspace_id"])
