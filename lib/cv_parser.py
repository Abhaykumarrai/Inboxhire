import pdfplumber, docx, os, json
from datetime import date
import anthropic

client = anthropic.Anthropic()

def extract_text(path: str, filename: str) -> str:
    if filename.lower().endswith(".pdf"):
        text = ""
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""
        return text.strip()
    elif filename.lower().endswith((".docx", ".doc")):
        doc = docx.Document(path)
        return "\n".join([p.text for p in doc.paragraphs]).strip()
    return ""

EXTRACTION_PROMPT = """You are a resume/CV information extractor. Today's date is {today}.
Extract the following fields and return STRICT JSON only — no markdown, no code fences, no explanation:

{{
  "name": "",
  "email": "",
  "phone": "",
  "location": "",
  "linkedin_url": "",
  "total_exp_years": 0,
  "summary": "",
  "skills": [],
  "experience": [{{"title": "", "company": "", "from": "", "to": "", "skills_used": [], "highlight": ""}}],
  "education": [{{"degree": "", "institution": "", "year": ""}}]
}}

Rules:
- "phone": a real phone number only. Tracking/request IDs are NOT phone numbers.
- "skills": bare skill names with NO version numbers.
- "summary": ONE sentence, max 25 words.
- Each experience entry's "skills_used": ONLY skills from the main "skills" list actually used in that role — max 6.
- Each experience entry's "highlight": ONE short achievement, max 12 words. Empty if none stated.
- "to" should be "Present" for ongoing roles.
- education "year" = graduation year.
- total_exp_years: best numeric estimate, using today's date for "Present" roles.
- Missing fields: empty string/array. Never invent information.
"""
def extract_with_ai(raw_text: str) -> dict:
    prompt = EXTRACTION_PROMPT.format(today=date.today().isoformat())
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": raw_text}]
    )
    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)

def parse_cv_file(file_bytes: bytes, filename: str) -> dict:
    tmp_path = f"./{filename}"
    with open(tmp_path, "wb") as f:
        f.write(file_bytes)
    raw_text = extract_text(tmp_path, filename)
    os.remove(tmp_path)
    try:
        result = extract_with_ai(raw_text)
    except Exception as e:
        result = {"error": f"AI extraction failed: {str(e)}"}
    result["raw_text"] = raw_text
    return result
