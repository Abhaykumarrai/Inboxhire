import re
import calendar
from datetime import date
from lib.supabase_client import supabase
from lib.skill_matching import normalize, skill_matches, find_matching_cv_skill
from lib.scoring_weights import get_scoring_weights

EDUCATION_KEYWORDS = {
    "btech": ["btech"], "be": ["be"], "mtech": ["mtech"], "mba": ["mba"],
    "bca": ["bca"], "mca": ["mca"], "bsc": ["bsc"], "msc": ["msc"],
    "bcom": ["bcom"], "phd": ["phd"],
}
MONTH_MAP = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}

def parse_month_year(s: str):
    if not s:
        return None
    s = s.strip()
    if s.lower() in ("present", "current"):
        today = date.today()
        return (today.year, today.month)
    parts = s.split()
    if len(parts) == 2:
        month_str, year_str = parts
        month = MONTH_MAP.get(month_str[:3].lower())
        try:
            year = int(year_str)
        except ValueError:
            return None
        if month:
            return (year, month)
    return None

def duration_years(from_str: str, to_str: str) -> float:
    start = parse_month_year(from_str)
    end = parse_month_year(to_str)
    if not start or not end:
        return 0.0
    months = (end[0] - start[0]) * 12 + (end[1] - start[1])
    return round(max(months, 0) / 12, 1)

def compute_skill_years(experience: list) -> dict:
    skill_years = {}
    for role in experience:
        years = duration_years(role.get("from", ""), role.get("to", ""))
        for skill in role.get("skills_used") or []:
            skill_years[skill] = round(skill_years.get(skill, 0) + years, 1)
    return skill_years

def compute_role_fit(role: dict, must_have: list, nice_to_have: list) -> int:
    role_skills_normalized = [normalize(s) for s in (role.get("skills_used") or [])]
    all_required = must_have + nice_to_have
    if not all_required:
        return 0
    matched = sum(1 for req in all_required if skill_matches(req, role_skills_normalized))
    return round((matched / len(all_required)) * 100)

def proficiency_score(years: float) -> int:
    return min(100, round(50 + years * 10))


def calculate_score(parsed: dict, job_id: str) -> dict:
    job = supabase.table("jobs").select("*").eq("id", job_id).single().execute().data
    weights = get_scoring_weights(job["workspace_id"])

    requirements = []
    cv_skills = parsed.get("skills") or []

    # --- Experience ---
    exp = parsed.get("total_exp_years") or 0
    exp_min = job.get("exp_min") or 0
    exp_max = job.get("exp_max") or 99
    exp_weight = weights["experience_weight"]
    under_penalty_per_year = (5 / 30) * exp_weight
    over_penalty_per_year = (2 / 30) * exp_weight
    over_floor = (10 / 30) * exp_weight

    if exp_min <= exp <= exp_max:
        exp_status, exp_score = "matched", exp_weight
    elif exp < exp_min:
        gap = exp_min - exp
        exp_status = "partial" if gap <= 2 else "gap"
        exp_score = max(0, exp_weight - gap * under_penalty_per_year)
    else:
        over = exp - exp_max
        exp_status = "partial" if over <= 2 else "gap"
        exp_score = max(over_floor, exp_weight - over * over_penalty_per_year)
    requirements.append({
        "category": "experience", "label": f"{exp_min}-{exp_max} years",
        "candidate_value": f"{exp} yrs", "status": exp_status,
    })

    # --- Education ---
    req_edu_raw = job.get("education") or ""
    education_list = parsed.get("education") or []
    cv_degree = education_list[0].get("degree", "") if education_list else ""
    cv_degree_norm = normalize(cv_degree)
    req_edu_norm = normalize(req_edu_raw)
    edu_weight = weights["education_weight"]

    if req_edu_raw:
        matched_edu = any(
            any(v in req_edu_norm for v in variants) and any(v in cv_degree_norm for v in variants)
            for variants in EDUCATION_KEYWORDS.values()
        )
        if matched_edu:
            edu_status, edu_score = "matched", edu_weight
        elif cv_degree:
            edu_status, edu_score = "partial", round(edu_weight * (8 / 15))
        else:
            edu_status, edu_score = "gap", round(edu_weight * (4 / 15))
        requirements.append({
            "category": "education", "label": req_edu_raw,
            "candidate_value": cv_degree or "Not found", "status": edu_status,
        })
    else:
        edu_score = round(edu_weight * (8 / 15))

    # --- Location ---
    req_location = job.get("location") or ""
    location_weight = weights["location_weight"]
    if req_location:
        candidate_location = parsed.get("location") or ""
        if not candidate_location:
            loc_status, loc_score = "partial", round(location_weight * 0.5)
        elif req_location.lower() in candidate_location.lower() or candidate_location.lower() in req_location.lower():
            loc_status, loc_score = "matched", location_weight
        else:
            loc_status, loc_score = "gap", 0
        requirements.append({
            "category": "location", "label": req_location,
            "candidate_value": candidate_location or "Not specified", "status": loc_status,
        })
    else:
        loc_score = location_weight

    # --- Must-have skills ---
    must_have = job.get("required_skills") or []
    skills_weight = weights["skills_weight"]
    must_slice = skills_weight * 0.8
    nice_slice = skills_weight * 0.2

    must_matched_count = 0
    for skill in must_have:
        matched_skill = find_matching_cv_skill(skill, cv_skills)
        if matched_skill:
            must_matched_count += 1
        requirements.append({
            "category": "must_have", "label": skill,
            "candidate_value": matched_skill, "status": "matched" if matched_skill else "gap",
        })
    must_score = (must_matched_count / len(must_have)) * must_slice if must_have else must_slice

    # --- Nice-to-have skills ---
    nice_to_have = job.get("nice_skills") or []
    nice_matched_count = 0
    for skill in nice_to_have:
        matched_skill = find_matching_cv_skill(skill, cv_skills)
        if matched_skill:
            nice_matched_count += 1
        requirements.append({
            "category": "nice_to_have", "label": skill,
            "candidate_value": matched_skill, "status": "matched" if matched_skill else "gap",
        })
    nice_score = (nice_matched_count / len(nice_to_have)) * nice_slice if nice_to_have else 0
    skills_score = round(must_score + nice_score)

    # --- Profile completeness ---
    fields = ["name", "email", "phone", "skills", "experience"]
    profile_weight = weights["profile_weight"]
    profile_score = round((sum(1 for f in fields if parsed.get(f)) / len(fields)) * profile_weight)

    recency_score = weights["recency_weight"]

    total = skills_score + round(exp_score) + edu_score + round(loc_score) + profile_score + recency_score

    matched_count = sum(1 for r in requirements if r["status"] == "matched")
    partial_count = sum(1 for r in requirements if r["status"] == "partial")
    gap_count = sum(1 for r in requirements if r["status"] == "gap")
    total_reqs = len(requirements) or 1
    match_percentage = round(((matched_count + 0.5 * partial_count) / total_reqs) * 100)

    highlight_terms = list(
        {r["label"] for r in requirements if r["status"] in ("matched", "partial")} |
        {r["candidate_value"] for r in requirements if r.get("candidate_value") and r["status"] in ("matched", "partial")}
    )

    experience = parsed.get("experience") or []
    skill_years = compute_skill_years(experience)
    skill_proficiency = {
        skill: {"years": years, "score": proficiency_score(years), "jd_match": skill_matches(skill, [normalize(s) for s in cv_skills])}
        for skill, years in skill_years.items()
    }
    experience_breakdown = [
        {**role, "fit_percentage": compute_role_fit(role, must_have, nice_to_have)}
        for role in experience
    ]
    highlights = [r["highlight"] for r in experience if r.get("highlight")][:3]

    breakdown_json = {
        "match_percentage": match_percentage, "matched_count": matched_count,
        "partial_count": partial_count, "gap_count": gap_count,
        "requirements": requirements, "highlight_terms": highlight_terms,
        "summary": parsed.get("summary", ""), "skill_proficiency": skill_proficiency,
        "experience_breakdown": experience_breakdown, "highlights": highlights,
        "weights_used": weights,
    }

    return {
        "skills_score": skills_score, "exp_score": round(exp_score), "edu_score": edu_score,
        "location_score": round(loc_score), "profile_score": profile_score,
        "recency_score": recency_score, "total": total, "breakdown_json": breakdown_json,
    }
