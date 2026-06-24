import base64
import logging
import inngest
import inngest.fast_api
from lib.supabase_client import supabase
from lib.cv_parser import parse_cv_file
from lib.matching import match_jobs_for_document

inngest_client = inngest.Inngest(app_id="inboxhire", logger=logging.getLogger("uvicorn"))

@inngest_client.create_function(
    fn_id="parse-cv",
    trigger=inngest.TriggerEvent(event="cv/received"),
)
async def parse_cv(ctx: inngest.Context) -> None:
    workspace_id = ctx.event.data["workspace_id"]
    connection_id = ctx.event.data["connection_id"]
    email_id = ctx.event.data["email_id"]
    cv_path = ctx.event.data["cv_path"]
    file_hash = ctx.event.data["file_hash"]
    sender_email = ctx.event.data["sender_email"]
    job_ids = ctx.event.data["job_ids"]

    async def _download():
        file_bytes = supabase.storage.from_("cvs").download(cv_path)
        return base64.b64encode(file_bytes).decode("utf-8")
    file_base64 = await ctx.step.run("download-cv", _download)

    async def _parse():
        file_bytes = base64.b64decode(file_base64)
        filename = cv_path.split("/")[-1]
        return parse_cv_file(file_bytes, filename)
    parsed = await ctx.step.run("parse-cv", _parse)

    async def _save_document():
        existing = supabase.table("cv_documents").select("id").eq("workspace_id", workspace_id).eq("file_hash", file_hash).maybe_single().execute()
        if existing and existing.data:
            return existing.data["id"]
        doc = supabase.table("cv_documents").insert({
            "workspace_id": workspace_id, "file_hash": file_hash, "storage_path": cv_path,
            "parsed_json": parsed, "raw_text": parsed.get("raw_text"), "candidate_email": sender_email,
        }).execute().data[0]
        return doc["id"]
    cv_document_id = await ctx.step.run("save-document", _save_document)

    async def _match_and_backfill():
        match_jobs_for_document(workspace_id, cv_document_id, sender_email, parsed, job_ids, cv_path)
        supabase.table("processed_messages").update({"cv_document_id": cv_document_id}).eq("connection_id", connection_id).eq("email_id", email_id).execute()
    await ctx.step.run("match-and-backfill", _match_and_backfill)
