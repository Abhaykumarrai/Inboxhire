import re

def normalize(s: str) -> str:
    return re.sub(r'[^a-z0-9]', '', s.lower())

def skill_matches(required: str, cv_skills_normalized: list) -> bool:
    req_norm = normalize(required)
    if not req_norm:
        return False
    return any(req_norm in cv_skill or cv_skill in req_norm for cv_skill in cv_skills_normalized)

def find_matching_cv_skill(required: str, cv_skills: list):
    req_norm = normalize(required)
    for skill in cv_skills:
        skill_norm = normalize(skill)
        if req_norm in skill_norm or skill_norm in req_norm:
            return skill
    return None

def build_requirements(parsed: dict, job_spec: dict) -> list:
    """job_spec: {exp_min, exp_max, education, required_skills, nice_skills}"""
    requirements = []
    cv_skills = parsed.get("skills") or []

    exp = parsed.get("total_exp_years") or 0
    exp_min = job_spec.get("exp_min") or 0
    exp_max = job_spec.get("exp_max") or 99
    if exp_min <= exp <= exp_max:
        exp_status = "matched"
    elif exp < exp_min:
        exp_status = "partial" if (exp_min - exp) <= 2 else "gap"
    else:
        exp_status = "partial" if (exp - exp_max) <= 2 else "gap"
    requirements.append({"category": "experience", "label": f"{exp_min}-{exp_max} years", "candidate_value": f"{exp} yrs", "status": exp_status})

    req_edu = job_spec.get("education") or ""
    if req_edu:
        education_list = parsed.get("education") or []
        cv_degree = education_list[0].get("degree", "") if education_list else ""
        matched = normalize(req_edu) in normalize(cv_degree) or (cv_degree and normalize(cv_degree) in normalize(req_edu))
        status = "matched" if matched else ("partial" if cv_degree else "gap")
        requirements.append({"category": "education", "label": req_edu, "candidate_value": cv_degree or "Not found", "status": status})

    for skill in job_spec.get("required_skills") or []:
        matched_skill = find_matching_cv_skill(skill, cv_skills)
        requirements.append({"category": "must_have", "label": skill, "candidate_value": matched_skill, "status": "matched" if matched_skill else "gap"})

    for skill in job_spec.get("nice_skills") or []:
        matched_skill = find_matching_cv_skill(skill, cv_skills)
        requirements.append({"category": "nice_to_have", "label": skill, "candidate_value": matched_skill, "status": "matched" if matched_skill else "gap"})

    return requirements

def compute_match_percentage(requirements: list) -> int:
    if not requirements:
        return 0
    matched = sum(1 for r in requirements if r["status"] == "matched")
    partial = sum(1 for r in requirements if r["status"] == "partial")
    return round(((matched + 0.5 * partial) / len(requirements)) * 100)

def summarize_requirements(requirements: list) -> dict:
    matched = [r["label"] for r in requirements if r["status"] == "matched"]
    partial = [r["label"] for r in requirements if r["status"] == "partial"]
    gaps = [r["label"] for r in requirements if r["status"] == "gap"]
    return {"matched": matched, "partial": partial, "gaps": gaps}
