import hashlib
import base64
import re
import io
from datetime import datetime, timedelta
from fastapi import APIRouter
from google.oauth2.credentials import Credentials
from google.oauth2.credentials import Credentials as DriveCreds
from googleapiclient.discovery import build
from googleapiclient.discovery import build as build_drive_service
from googleapiclient.http import MediaIoBaseDownload
import inngest
from lib.supabase_client import supabase
from lib.inngest_app import inngest_client
from lib.matching import match_jobs_for_document

router = APIRouter()

def has_credits(workspace_id: str) -> bool:
    ws = supabase.table("workspaces").select("ai_credits_remaining").eq("id", workspace_id).single().execute().data
    return (ws.get("ai_credits_remaining") or 0) > 0

@router.get("/api/cron/poll-gmail")
def poll_gmail():
    connections = supabase.table("gmail_connections").select("*").eq("status", "connected").execute().data
    print(f">>> Found {len(connections)} connected Gmail account(s)")
    for conn in connections:
        poll_gmail_connection(conn)
    return {"ok": True}

def poll_gmail_connection(conn):
    jobs = supabase.table("jobs").select("*").eq("gmail_connection_id", conn["id"]).eq("status", "active").execute().data
    if not jobs:
        print(f">>> No active jobs for connection {conn['gmail_email']} — skipping")
        return

    from_dates = [j["scan_from_date"] for j in jobs if j.get("scan_from_date")]
    to_dates = [j["scan_to_date"] for j in jobs if j.get("scan_to_date")]
    date_filter = ""
    if from_dates and to_dates:
        from_str = min(from_dates).replace("-", "/")
        to_obj = datetime.strptime(max(to_dates), "%Y-%m-%d").date() + timedelta(days=1)
        date_filter = f" after:{from_str} before:{to_obj.strftime('%Y/%m/%d')}"

    creds = Credentials(**conn["gmail_token"])
    service = build("gmail", "v1", credentials=creds)
    search_query = f"has:attachment{date_filter}"
    res = service.users().messages().list(userId="me", q=search_query, maxResults=50).execute()
    messages = res.get("messages", [])
    print(f">>> '{conn['gmail_email']}' — search '{search_query}' — found {len(messages)} message(s)")

    job_ids = [j["id"] for j in jobs]
    for m in messages:
        process_message(service, m["id"], conn, job_ids)

def process_message(service, message_id, conn, job_ids):
    already = supabase.table("processed_messages").select("cv_document_id").eq("connection_id", conn["id"]).eq("email_id", message_id).maybe_single().execute()
    if already and already.data:
        print(f">>> Message {message_id} already processed — skipping re-parse")
        if already.data["cv_document_id"]:
            doc = supabase.table("cv_documents").select("parsed_json, candidate_email, storage_path").eq("id", already.data["cv_document_id"]).single().execute().data
            match_jobs_for_document(conn["workspace_id"], already.data["cv_document_id"], doc["candidate_email"], doc["parsed_json"], job_ids, doc["storage_path"])
        return

    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = msg["payload"].get("headers", [])
    from_header = next((h["value"] for h in headers if h["name"] == "From"), "")
    m = re.search(r"<(.+)>", from_header)
    sender_email = m.group(1) if m else from_header

    cv_document_id = None
    for part in msg["payload"].get("parts", []) or []:
        filename = part.get("filename")
        if not filename or not re.search(r"\.(pdf|docx|doc)$", filename.lower()):
            continue

        attachment_id = part["body"]["attachmentId"]
        attachment = service.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute()
        data = attachment["data"]
        padded = data + "=" * (-len(data) % 4)
        file_bytes = base64.urlsafe_b64decode(padded)
        file_hash = hashlib.sha256(file_bytes).hexdigest()

        existing_doc = supabase.table("cv_documents").select("id").eq("workspace_id", conn["workspace_id"]).eq("file_hash", file_hash).maybe_single().execute()

        if existing_doc and existing_doc.data:
            cv_document_id = existing_doc.data["id"]
            print(f">>> Duplicate CV (hash match) — reusing document {cv_document_id}, no AI call")
        else:
            if not has_credits(conn["workspace_id"]):
                print(">>> No AI credits remaining — skipping new CV")
                break
            path = f"{conn['workspace_id']}/{file_hash}_{filename}"
            supabase.storage.from_("cvs").upload(path, file_bytes, {"content-type": part.get("mimeType", "application/octet-stream"), "upsert": "true"})

            inngest_client.send_sync(inngest.Event(
                name="cv/received",
                data={
                    "workspace_id": conn["workspace_id"],
                    "connection_id": conn["id"],
                    "email_id": message_id,
                    "cv_path": path,
                    "file_hash": file_hash,
                    "sender_email": sender_email,
                    "job_ids": job_ids,
                },
            ))
            cv_document_id = None  # Inngest will create the cv_document async; processed_messages gets backfilled next poll
        break

    supabase.table("processed_messages").insert({
        "connection_id": conn["id"], "email_id": message_id, "cv_document_id": cv_document_id,
    }).execute()

@router.get("/api/cron/poll-drive")
def poll_drive():
    connections = supabase.table("drive_connections").select("*").eq("status", "connected").execute().data
    print(f">>> Found {len(connections)} connected Drive account(s)")
    for conn in connections:
        if conn.get("folder_id"):
            poll_drive_connection(conn)
        else:
            print(f">>> Drive connection {conn['drive_email']} has no folder selected yet — skipping")
    return {"ok": True}

def poll_drive_connection(conn):
    creds = DriveCreds(**conn["drive_token"])
    service = build_drive_service("drive", "v3", credentials=creds)

    jobs = supabase.table("jobs").select("id").eq("workspace_id", conn["workspace_id"]).eq("status", "active").execute().data
    job_ids = [j["id"] for j in jobs]
    if not job_ids:
        print(">>> No active jobs in this workspace — skipping Drive scan")
        return

    results = service.files().list(
        q=f"'{conn['folder_id']}' in parents and trashed=false and (mimeType='application/pdf' or mimeType='application/vnd.openxmlformats-officedocument.wordprocessingml.document')",
        fields="files(id, name, mimeType)", pageSize=100,
    ).execute()
    files = results.get("files", [])
    print(f">>> Drive folder '{conn['folder_name']}' — found {len(files)} CV file(s)")

    for f in files:
        process_drive_file(service, f, conn, job_ids)

def process_drive_file(service, file_meta, conn, job_ids):
    request = service.files().get_media(fileId=file_meta["id"])
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    file_bytes = buffer.getvalue()

    file_hash = hashlib.sha256(file_bytes).hexdigest()
    existing_doc = supabase.table("cv_documents").select("id, candidate_email, parsed_json").eq("workspace_id", conn["workspace_id"]).eq("file_hash", file_hash).maybe_single().execute()

    if existing_doc and existing_doc.data:
        doc = existing_doc.data
        match_jobs_for_document(conn["workspace_id"], doc["id"], doc.get("candidate_email"), doc["parsed_json"], job_ids)
        return

    try:
        supabase.table("drive_processing_locks").insert({"workspace_id": conn["workspace_id"], "file_hash": file_hash}).execute()
    except Exception:
        print(f">>> File {file_hash[:8]} already being processed by another poll — skipping duplicate")
        return

    if not has_credits(conn["workspace_id"]):
        print(">>> No AI credits remaining — skipping new Drive CV")
        return

    path = f"{conn['workspace_id']}/{file_hash}_{file_meta['name']}"
    supabase.storage.from_("cvs").upload(path, file_bytes, {"content-type": file_meta["mimeType"], "upsert": "true"})

    inngest_client.send_sync(inngest.Event(
        name="cv/received",
        data={
            "workspace_id": conn["workspace_id"], "connection_id": conn["id"], "email_id": f"drive_{file_meta['id']}",
            "cv_path": path, "file_hash": file_hash, "sender_email": None, "job_ids": job_ids,
        },
    ))
