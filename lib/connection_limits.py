from lib.supabase_client import supabase

def get_source_limits(workspace_id: str) -> dict:
    workspace = supabase.table("workspaces").select("plan_id").eq("id", workspace_id).single().execute().data
    if not workspace.get("plan_id"):
        return {"max_gmail": 1, "max_drive": 0, "max_api": 0, "combined_cap": None}
    plan = (
        supabase.table("plans")
        .select("max_gmail_connections, max_drive_connections, max_api_connections, combined_gmail_drive_cap")
        .eq("id", workspace["plan_id"]).single().execute().data
    )
    return {
        "max_gmail": plan["max_gmail_connections"], "max_drive": plan["max_drive_connections"],
        "max_api": plan.get("max_api_connections", 0), "combined_cap": plan.get("combined_gmail_drive_cap"),
    }

def count_connected(workspace_id: str) -> dict:
    gmail_count = len(supabase.table("gmail_connections").select("id").eq("workspace_id", workspace_id).eq("status", "connected").execute().data)
    drive_row = supabase.table("drive_connections").select("id").eq("workspace_id", workspace_id).eq("status", "connected").maybe_single().execute()
    return {"gmail": gmail_count, "drive": 1 if (drive_row and drive_row.data) else 0}
