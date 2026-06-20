from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from fastapi.responses import JSONResponse
import pdfplumber, docx, os, json
from datetime import date
import anthropic

app = FastAPI()
client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from environment

PARSER_SECRET_KEY = os.environ.get("PARSER_SECRET_KEY", "dev-secret")

# ---------- Text extraction (unchanged, still free) ----------

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

# ---------- AI extraction ----------

EXTRACTION_PROMPT = """You are a resume/CV information extractor. Today's date is {today}.
You will be given raw text extracted from a candidate's resume or profile (this may be a PDF resume or a scraped LinkedIn-style profile). Extract the following fields and return STRICT JSON only — no markdown formatting, no code fences, no explanation, just the JSON object:

{{
  "name": "",
  "email": "",
  "phone": "",
  "skills": [],
  "experience": [{{"title": "", "company": "", "from": "", "to": ""}}],
  "education": [{{"degree": "", "institution": "", "year": ""}}],
  "total_exp_years": 0,
  "location": "",
  "linkedin_url": ""
}}

Rules:
- "phone" must be a real phone number from the text. Tracking IDs, request IDs, or other long numeric strings are NOT phone numbers — leave phone empty if no genuine phone number is present.
- "to" should be "Present" for ongoing roles.
- total_exp_years should be your best numeric estimate of total professional experience, accounting for overlapping or sequential roles, using today's date for any "Present" role.
- If a field isn't present in the text, use an empty string or empty array. Never invent information.
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

# ---------- API endpoint ----------

@app.post("/parse")
async def parse_cv(file: UploadFile = File(...), x_secret: str = Header(None)):
    if x_secret != PARSER_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid secret key")

    tmp_path = f"./{file.filename}"
    content = await file.read()
    with open(tmp_path, "wb") as f:
        f.write(content)

    raw_text = extract_text(tmp_path, file.filename)
    os.remove(tmp_path)

    try:
        result = extract_with_ai(raw_text)
    except Exception as e:
        result = {"error": f"AI extraction failed: {str(e)}"}

    result["raw_text"] = raw_text
    return JSONResponse(result)