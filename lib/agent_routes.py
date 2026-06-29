import json
import anthropic
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_user
from lib.search_routes import run_candidate_search
from lib.email_service import send_candidate_email

router = APIRouter()
client = anthropic.Anthropic()

AGENT_SYSTEM_PROMPT = """You are a recruiting assistant. You can search the candidate database and draft outreach emails.

Tools available:
1. search_candidates - search the talent pool with a natural language query.
2. draft_email - prepare an email to one or more candidates from the MOST RECENT search_candidates results in THIS conversation. You must resolve who the user means (by name, email, or a tier keyword) against those results. Never invent a candidate, email address, ID, or system capability that wasn't explicitly given to you in a tool result.

If the user asks you to email someone but no search has been run yet in this conversation, OR the person they named isn't in the most recent search results: call search_candidates yourself first (using their name or description as the query) to try to find them, before giving up. If you still can't find them after searching, tell the user plainly that you couldn't locate that candidate in the database — do not apologize with a fabricated technical reason.

You cannot send email directly — draft_email only creates a draft for a human to confirm. After drafting, tell the user it's ready for their review.

When reporting search results back to the user, mention candidates by name along with their match percentage and a brief note on why they matched or what gaps they have — do not just report counts.

LANGUAGE: Always reply in the same language the user wrote their message in (Hindi, English, Hinglish, or whatever language they use). Do not switch languages unless the user switches first."""

TOOLS = [
    {
        "name": "search_candidates",
        "description": "Search the full candidate database using a natural language query (skills, experience, location, match threshold).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "draft_email",
        "description": "Prepare an email draft to one or more candidates from the most recent search results. Does not send.",
        "input_schema": {
            "type": "object",
            "properties": {
                "candidate_names_or_emails": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Names or emails to target, OR one of: all_exact_matches, all_strong_matches, all_below_threshold, all_results",
                },
                "subject": {"type": "string"},
                "body_html": {"type": "string"},
            },
            "required": ["candidate_names_or_emails", "subject", "body_html"],
        },
    },
]

class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str

def get_or_create_conversation(conversation_id, user):
    if conversation_id:
        convo = supabase.table("agent_conversations").select("*").eq("id", conversation_id).eq("workspace_id", user["workspace_id"]).maybe_single().execute()
        if convo and convo.data:
            return convo.data
        # conversation_id was provided but doesn't exist (stale/invalid) — fall through and start a fresh one instead of crashing

    return supabase.table("agent_conversations").insert({
        "workspace_id": user["workspace_id"], "user_id": user["user_id"], "messages": [], "last_search_results": [],
    }).execute().data[0]

def flatten_results(search_result: dict) -> list:
    flat = []
    for tier_key, tier_name in (("exact_matches", "exact"), ("strong_matches", "strong"), ("below_threshold", "below_threshold")):
        for c in search_result.get(tier_key, []):
            flat.append({
                "cv_document_id": c["cv_document_id"], "name": c.get("name"),
                "email": c.get("candidate_email"), "match_percentage": c.get("match_percentage"), "tier": tier_name,
                "location": c.get("location"), "total_exp_years": c.get("total_exp_years"), "note": c.get("note"),
            })
    return flat

TIER_KEYWORDS = {"all_exact_matches": "exact", "all_strong_matches": "strong", "all_below_threshold": "below_threshold"}

def resolve_recipients(names_or_emails: list, last_results: list) -> list:
    resolved = {}
    for entry in names_or_emails:
        key = entry.strip().lower()
        if key in TIER_KEYWORDS or key == "all_results":
            tier = TIER_KEYWORDS.get(key)
            for c in last_results:
                if tier is None or c["tier"] == tier:
                    resolved[c["cv_document_id"]] = c
            continue
        for c in last_results:
            if c.get("email") and c["email"].lower() == key:
                resolved[c["cv_document_id"]] = c
                break
            if c.get("name") and key in c["name"].lower():
                resolved[c["cv_document_id"]] = c
                break
    return list(resolved.values())

@router.post("/api/agent/chat")
def agent_chat(data: ChatRequest, user: dict = Depends(get_current_user)):
    conversation = get_or_create_conversation(data.conversation_id, user)
    messages = list(conversation.get("messages") or [])
    messages.append({"role": "user", "content": data.message})
    last_results = conversation.get("last_search_results") or []
    pending_draft = None
    final_text = ""

    for _ in range(5):
        response = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=1024,
            system=AGENT_SYSTEM_PROMPT, tools=TOOLS, messages=messages,
        )
        assistant_content = [block.model_dump() for block in response.content]
        messages.append({"role": "assistant", "content": assistant_content})

        tool_uses = [b for b in assistant_content if b["type"] == "tool_use"]
        if not tool_uses:
            final_text = "".join(b["text"] for b in assistant_content if b["type"] == "text")
            break

        tool_results = []
        for tool_use in tool_uses:
            if tool_use["name"] == "search_candidates":
                result = run_candidate_search(tool_use["input"]["query"], user["workspace_id"])
                last_results = flatten_results(result)
                candidate_summaries = [
                    {"name": c.get("name"), "match_percentage": c.get("match_percentage"),
                     "tier": c.get("tier"), "location": c.get("location"),
                     "total_exp_years": c.get("total_exp_years"), "note": c.get("note")}
                    for c in last_results[:10]
                ]
                tool_results.append({
                    "type": "tool_result", "tool_use_id": tool_use["id"],
                    "content": json.dumps({
                        "message": result["message"],
                        "counts": {"exact": len(result["exact_matches"]), "strong": len(result["strong_matches"]), "below_threshold": len(result["below_threshold"])},
                        "candidates": candidate_summaries,
                    }),
                })
            elif tool_use["name"] == "draft_email":
                recipients = resolve_recipients(tool_use["input"]["candidate_names_or_emails"], last_results)
                if not recipients:
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tool_use["id"], "is_error": True,
                        "content": "No matching candidates found in the most recent search results.",
                    })
                else:
                    draft = supabase.table("email_drafts").insert({
                        "workspace_id": user["workspace_id"], "conversation_id": conversation["id"],
                        "created_by_user_id": user["user_id"], "recipients": recipients,
                        "subject": tool_use["input"]["subject"], "body_html": tool_use["input"]["body_html"],
                        "status": "pending",
                    }).execute().data[0]
                    pending_draft = draft
                    tool_results.append({
                        "type": "tool_result", "tool_use_id": tool_use["id"],
                        "content": f"Draft {draft['id']} created for {len(recipients)} recipient(s), awaiting human confirmation.",
                    })
        messages.append({"role": "user", "content": tool_results})

    supabase.table("agent_conversations").update({"messages": messages, "last_search_results": last_results}).eq("id", conversation["id"]).execute()
    return {"conversation_id": conversation["id"], "reply": final_text, "pending_draft": pending_draft}


@router.post("/api/agent/drafts/{draft_id}/confirm")
def confirm_draft(draft_id: str, user: dict = Depends(get_current_user)):
    draft = supabase.table("email_drafts").select("*").eq("id", draft_id).eq("workspace_id", user["workspace_id"]).single().execute().data
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft["status"] != "pending":
        raise HTTPException(status_code=400, detail=f"Draft already {draft['status']}")

    workspace = supabase.table("workspaces").select("emails_sent_this_cycle, emails_limit").eq("id", user["workspace_id"]).single().execute().data
    remaining = (workspace.get("emails_limit") or 0) - (workspace.get("emails_sent_this_cycle") or 0)
    recipients = draft["recipients"]
    if len(recipients) > remaining:
        raise HTTPException(status_code=400, detail=f"Email quota exceeded. {remaining} remaining, {len(recipients)} required.")

    sender = supabase.table("users").select("email").eq("id", user["user_id"]).single().execute().data
    sent, failed = [], []
    for r in recipients:
        if not r.get("email"):
            failed.append(r); continue
        try:
            send_candidate_email(r["email"], draft["subject"], draft["body_html"], reply_to=sender["email"] if sender else None)
            sent.append(r)
        except Exception:
            failed.append(r)

    supabase.table("workspaces").update({"emails_sent_this_cycle": (workspace.get("emails_sent_this_cycle") or 0) + len(sent)}).eq("id", user["workspace_id"]).execute()
    supabase.table("email_drafts").update({"status": "sent"}).eq("id", draft_id).execute()
    return {"sent": len(sent), "failed": len(failed)}


@router.post("/api/agent/drafts/{draft_id}/cancel")
def cancel_draft(draft_id: str, user: dict = Depends(get_current_user)):
    supabase.table("email_drafts").update({"status": "cancelled"}).eq("id", draft_id).eq("workspace_id", user["workspace_id"]).execute()
    return {"message": "Draft cancelled"}
