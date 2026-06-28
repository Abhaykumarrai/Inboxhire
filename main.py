from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os

from lib.cv_parser import parse_cv_file

app = FastAPI(
    title="InboxHire API",
    version="0.1.0",
    openapi_tags=[
        {"name": "Authentication", "description": "Signup, login, password management"},
        {"name": "Team Management", "description": "Invite and manage employees within a workspace"},
        {"name": "Billing & Plans", "description": "View plans, create Razorpay orders, verify payments"},
        {"name": "Gmail Connections", "description": "Connect, list, assign, and disconnect Gmail inboxes"},
        {"name": "Drive Connection", "description": "Connect Google Drive, choose a folder, and scan CVs"},
        {"name": "Jobs", "description": "Create and manage job postings, view ranked candidates"},
        {"name": "Applications", "description": "Per-candidate actions — stage, notes, score override, CV access, outreach email"},
        {"name": "Search & AI Agent", "description": "Conversational candidate search and agentic actions (drafting, etc.)"},
        {"name": "Voice", "description": "Text-to-speech for spoken search responses"},
        {"name": "Settings", "description": "Workspace scoring weights and preferences"},
        {"name": "CV Parsing", "description": "Internal endpoint used by the parsing pipeline"},
        {"name": "Background Jobs", "description": "Cron polling and Inngest — internal, not for direct manual use"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],  # add your deployed frontend URL later
    allow_methods=["*"],
    allow_headers=["*"],
)

from lib.oauth_routes import router as oauth_router
app.include_router(oauth_router, tags=["Gmail Connections"])

from lib.connection_routes import router as connection_router
app.include_router(connection_router, tags=["Gmail Connections"])

from lib.drive_oauth_routes import router as drive_oauth_router
from lib.drive_routes import router as drive_router

app.include_router(drive_oauth_router, tags=["Drive Connection"])
app.include_router(drive_router, tags=["Drive Connection"])

from lib.auth_routes import router as auth_router
app.include_router(auth_router, tags=["Authentication"])

from lib.team_routes import router as team_router
app.include_router(team_router, tags=["Team Management"])

from lib.billing_routes import router as billing_router
app.include_router(billing_router, tags=["Billing & Plans"])

from lib.job_routes import router as job_router
app.include_router(job_router, tags=["Jobs"])

from lib.application_routes import router as application_router
app.include_router(application_router, tags=["Applications"])

from lib.email_routes import router as email_router
app.include_router(email_router, tags=["Applications"])

from lib.search_routes import router as search_router
app.include_router(search_router, tags=["Search & AI Agent"])

from lib.agent_routes import router as agent_router
app.include_router(agent_router, tags=["Search & AI Agent"])

from lib.voice_routes import router as voice_router
app.include_router(voice_router, tags=["Voice"])

from lib.settings_routes import router as settings_router
app.include_router(settings_router, tags=["Settings"])

import inngest.fast_api
from lib.cron_routes import router as cron_router
from lib.inngest_app import inngest_client, parse_cv

app.include_router(cron_router, tags=["Background Jobs"])
inngest.fast_api.serve(app, inngest_client, [parse_cv])

PARSER_SECRET_KEY = os.environ.get("PARSER_SECRET_KEY", "dev-secret")

# ---------- API endpoint ----------

@app.post("/parse", tags=["CV Parsing"])
async def parse_cv(file: UploadFile = File(...), x_secret: str = Header(None)):
    if x_secret != PARSER_SECRET_KEY:
        raise HTTPException(status_code=401, detail="Invalid secret key")

    content = await file.read()
    result = parse_cv_file(content, file.filename)
    return JSONResponse(result)
