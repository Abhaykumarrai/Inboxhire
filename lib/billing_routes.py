import os
import hmac
import hashlib
from datetime import datetime, timedelta, timezone
import razorpay
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from lib.supabase_client import supabase
from lib.auth_utils import get_current_workspace_id

router = APIRouter()
razorpay_client = razorpay.Client(auth=(os.environ["RAZORPAY_KEY_ID"], os.environ["RAZORPAY_KEY_SECRET"]))

@router.get("/api/billing/plans")
def list_plans():
    return supabase.table("plans").select("*").order("price_inr").execute().data

@router.get("/api/billing/current")
def current_plan(workspace_id: str = Depends(get_current_workspace_id)):
    workspace = supabase.table("workspaces").select(
        "plan_id, subscription_status, ai_credits_remaining, ai_credits_used, emails_limit, emails_sent_this_cycle, billing_cycle_start, billing_cycle_end"
    ).eq("id", workspace_id).single().execute().data
    plan = supabase.table("plans").select("*").eq("id", workspace["plan_id"]).single().execute().data if workspace.get("plan_id") else None
    return {**workspace, "plan": plan}


class CreateOrderRequest(BaseModel):
    plan_id: str

@router.post("/api/billing/create-order")
def create_order(data: CreateOrderRequest, workspace_id: str = Depends(get_current_workspace_id)):
    plan = supabase.table("plans").select("*").eq("id", data.plan_id).single().execute().data
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    amount_paise = plan["price_inr"] * 100

    order = razorpay_client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "notes": {"workspace_id": workspace_id, "plan_id": data.plan_id},
    })

    supabase.table("payments").insert({
        "workspace_id": workspace_id,
        "plan_id": data.plan_id,
        "razorpay_order_id": order["id"],
        "amount_inr": plan["price_inr"],
        "status": "created",
    }).execute()

    return {
        "order_id": order["id"],
        "amount": amount_paise,
        "currency": "INR",
        "key_id": os.environ["RAZORPAY_KEY_ID"],
        "plan_name": plan["name"],
    }


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str

@router.post("/api/billing/verify-payment")
def verify_payment(data: VerifyPaymentRequest, workspace_id: str = Depends(get_current_workspace_id)):
    body = f"{data.razorpay_order_id}|{data.razorpay_payment_id}"
    expected_signature = hmac.new(
        os.environ["RAZORPAY_KEY_SECRET"].encode(), body.encode(), hashlib.sha256
    ).hexdigest()

    if expected_signature != data.razorpay_signature:
        raise HTTPException(status_code=400, detail="Payment signature verification failed")

    payment_row = (
        supabase.table("payments").select("*").eq("razorpay_order_id", data.razorpay_order_id).single().execute().data
    )
    plan = supabase.table("plans").select("*").eq("id", payment_row["plan_id"]).single().execute().data

    now = datetime.now(timezone.utc)
    supabase.table("workspaces").update({
        "plan_id": plan["id"],
        "subscription_status": "active",
        "ai_credits_remaining": plan["ai_credits_included"],
        "ai_credits_used": 0,
        "emails_remaining": plan["emails_included"],
        "billing_cycle_start": now.isoformat(),
        "billing_cycle_end": (now + timedelta(days=30)).isoformat(),
    }).eq("id", workspace_id).execute()

    supabase.table("payments").update({
        "razorpay_payment_id": data.razorpay_payment_id,
        "status": "paid",
    }).eq("razorpay_order_id", data.razorpay_order_id).execute()

    return {"message": "Payment verified and plan activated", "plan": plan["name"]}
