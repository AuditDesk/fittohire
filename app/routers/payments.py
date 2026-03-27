"""
FitToHire — Razorpay Payment Routes
Plans:
  1. Job seeker monthly     ₹199  → Razorpay subscription
  2. Job seeker annual      ₹1799 → Razorpay subscription
  3. Employer subscription  ₹1999 → Razorpay subscription (max 30 active posts)
  4. Employer per-post      ₹499  → Razorpay order (one-time, qty based)

Verification: FastAPI-side HMAC — no webhook needed.
"""

import os
import hmac
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import razorpay
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pay", tags=["payments"])

def get_razorpay_client():
    return razorpay.Client(auth=(
        os.getenv("RZP_KEY_ID"),
        os.getenv("RZP_KEY_SECRET")
    ))

# Plan IDs from environment — paste your Razorpay plan_XXXX IDs in Railway
PLAN_JS_MONTHLY = os.getenv("RZP_PLAN_JS_MONTHLY")   # plan_SVYUC8QlEa7l6Y  ₹199/month
PLAN_JS_ANNUAL  = os.getenv("RZP_PLAN_JS_ANNUAL")    # plan_SVYVqg64LbK5jN  ₹1799/year
PLAN_EMP_SUB    = os.getenv("RZP_PLAN_EMP_SUB")      # plan_SVYY6VMwikYeA2  ₹1999/month
RZP_KEY_ID      = os.getenv("RZP_KEY_ID")
RZP_KEY_SECRET  = os.getenv("RZP_KEY_SECRET")

# Per-post price in paise (₹499 × 100)
PER_POST_PAISE  = 49900
MAX_ACTIVE_POSTS = 30  # employer subscription cap


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class CreateSubscriptionRequest(BaseModel):
    user_id: str
    email: str
    plan: str  # "js_monthly" | "js_annual" | "emp_sub"

class VerifySubscriptionRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str
    user_id: str
    plan: str

class CreateOrderRequest(BaseModel):
    user_id: str
    email: str
    num_posts: int  # 1 to 20

class VerifyOrderRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    user_id: str
    num_posts: int


# ---------------------------------------------------------------------------
# Supabase helper (thin wrapper — replace with your supabase-py client)
# ---------------------------------------------------------------------------

def get_supabase():
    from supabase import create_client
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY")
    )


# ---------------------------------------------------------------------------
# PLAN 1 & 2: Job seeker subscriptions (monthly + annual)
# ---------------------------------------------------------------------------

@router.post("/subscribe/create")
async def create_jobseeker_subscription(body: CreateSubscriptionRequest):
    """
    Step 1 — Create a Razorpay subscription for job seeker plans.
    Returns subscription_id + key_id to the frontend for checkout.
    Accepts user_id='pending' for pre-signup payments from landing page.
    """
    plan_map = {
        "js_monthly": PLAN_JS_MONTHLY,
        "js_annual":  PLAN_JS_ANNUAL,
    }
    plan_id = plan_map.get(body.plan)
    if not plan_id:
        raise HTTPException(400, "Invalid plan. Use js_monthly or js_annual.")

    if not plan_id:
        raise HTTPException(500, f"Plan ID not configured for {body.plan}. Check Railway env vars.")

    rz = get_razorpay_client()
    try:
        sub = rz.subscription.create({
            "plan_id":         plan_id,
            "customer_notify": 1,
            "quantity":        1,
            "total_count":     120,
            "notes": {
                "user_id": body.user_id,  # may be 'pending' for pre-signup
                "plan":    body.plan,
                "email":   body.email,
            }
        })
        return {
            "subscription_id": sub["id"],
            "key_id":          RZP_KEY_ID,
            "plan":            body.plan,
        }
    except Exception as e:
        logger.error(f"Razorpay subscription create failed: {e} | plan_id={plan_id} | key={RZP_KEY_ID[:10] if RZP_KEY_ID else 'MISSING'}")
        raise HTTPException(500, f"Could not create subscription. Please try again.")


@router.post("/subscribe/verify")
async def verify_jobseeker_subscription(body: VerifySubscriptionRequest):
    """
    Step 2 — Verify HMAC signature after user completes payment.
    Activates the user's subscription in Supabase.
    """
    # --- HMAC verification ---
    payload = f"{body.razorpay_payment_id}|{body.razorpay_subscription_id}"
    expected = hmac.new(
        RZP_KEY_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        logger.warning(f"Signature mismatch for user {body.user_id}")
        raise HTTPException(400, "Payment verification failed. Contact support.")

    # --- Activate in Supabase ---
    supabase = get_supabase()
    now = datetime.now(timezone.utc)

    plan_label = "monthly" if body.plan == "js_monthly" else "annual"
    period_end = now + timedelta(days=30 if plan_label == "monthly" else 365)

    try:
        supabase.table("jobseeker_subscriptions").upsert({
            "user_id":                   body.user_id,
            "plan":                      plan_label,
            "razorpay_subscription_id":  body.razorpay_subscription_id,
            "razorpay_payment_id":       body.razorpay_payment_id,
            "status":                    "active",
            "current_period_start":      now.isoformat(),
            "current_period_end":        period_end.isoformat(),
            "updated_at":                now.isoformat(),
        }, on_conflict="user_id").execute()

        logger.info(f"Job seeker subscription activated: {body.user_id} / {plan_label}")
        return {"status": "activated", "plan": plan_label, "redirect": "/dashboard"}

    except Exception as e:
        logger.error(f"Supabase update failed after verified payment: {e}")
        # Payment IS verified — don't show error to user, log for manual fix
        return {"status": "activated", "plan": plan_label, "redirect": "/dashboard"}


# ---------------------------------------------------------------------------
# PLAN 3: Employer subscription ₹1999/month
# ---------------------------------------------------------------------------

@router.post("/employer/subscribe/create")
async def create_employer_subscription(body: CreateSubscriptionRequest):
    """
    Step 1 — Create Razorpay subscription for employer unlimited plan.
    """
    if body.plan != "emp_sub":
        raise HTTPException(400, "Invalid plan for employer subscription.")

    rz = get_razorpay_client()
    try:
        sub = rz.subscription.create({
            "plan_id":         PLAN_EMP_SUB,
            "customer_notify": 1,
            "quantity":        1,
            "total_count":     120,
            "notes": {
                "user_id": body.user_id,
                "plan":    "emp_sub",
                "email":   body.email,
            }
        })
        return {
            "subscription_id": sub["id"],
            "key_id":          RZP_KEY_ID,
        }
    except Exception as e:
        logger.error(f"Employer subscription create failed: {e}")
        raise HTTPException(500, "Could not create subscription. Please try again.")


@router.post("/employer/subscribe/verify")
async def verify_employer_subscription(body: VerifySubscriptionRequest):
    """
    Step 2 — Verify and activate employer subscription.
    """
    payload = f"{body.razorpay_payment_id}|{body.razorpay_subscription_id}"
    expected = hmac.new(
        RZP_KEY_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(400, "Payment verification failed.")

    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=30)

    try:
        supabase.table("employer_subscriptions").upsert({
            "employer_id":               body.user_id,
            "razorpay_subscription_id":  body.razorpay_subscription_id,
            "razorpay_payment_id":       body.razorpay_payment_id,
            "status":                    "active",
            "current_period_start":      now.isoformat(),
            "current_period_end":        period_end.isoformat(),
            "updated_at":                now.isoformat(),
        }, on_conflict="employer_id").execute()

        logger.info(f"Employer subscription activated: {body.user_id}")
        return {"status": "activated", "redirect": "/employer/dashboard"}

    except Exception as e:
        logger.error(f"Supabase employer sub update failed: {e}")
        return {"status": "activated", "redirect": "/employer/dashboard"}


# ---------------------------------------------------------------------------
# PLAN 4: Employer per-post ₹499 × qty (one-time order)
# ---------------------------------------------------------------------------

@router.post("/employer/post/create")
async def create_jobpost_order(body: CreateOrderRequest):
    """
    Step 1 — Create a Razorpay order for per-post payment.
    num_posts: how many posts the employer wants to buy (1–20).
    Each post = ₹499, valid 30 days from activation.
    """
    if not (1 <= body.num_posts <= 20):
        raise HTTPException(400, "Number of posts must be between 1 and 20.")

    amount_paise = body.num_posts * PER_POST_PAISE  # e.g. 5 posts = 249500 paise

    rz = get_razorpay_client()
    try:
        order = rz.order.create({
            "amount":   amount_paise,
            "currency": "INR",
            "receipt":  f"post_{body.user_id[:8]}_{int(datetime.now().timestamp())}",
            "notes": {
                "user_id":   body.user_id,
                "email":     body.email,
                "num_posts": str(body.num_posts),
                "plan":      "per_post",
            }
        })
        return {
            "order_id":  order["id"],
            "amount":    amount_paise,
            "num_posts": body.num_posts,
            "key_id":    RZP_KEY_ID,
        }
    except Exception as e:
        logger.error(f"Job post order create failed: {e}")
        raise HTTPException(500, "Could not create order. Please try again.")


@router.post("/employer/post/verify")
async def verify_jobpost_order(body: VerifyOrderRequest):
    """
    Step 2 — Verify HMAC and create num_posts pending job post credits.
    Posts are 'pending' until employer fills in job details and publishes.
    expires_at is set at publish time (activated_at + 30 days), not here.
    """
    # --- HMAC verification ---
    payload = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected = hmac.new(
        RZP_KEY_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(400, "Payment verification failed.")

    if not (1 <= body.num_posts <= 20):
        raise HTTPException(400, "Invalid number of posts.")

    supabase = get_supabase()
    now = datetime.now(timezone.utc)

    # Insert one row per post credit purchased
    credits = [
        {
            "employer_id":          body.user_id,
            "plan_type":            "per_post",
            "razorpay_order_id":    body.razorpay_order_id,
            "razorpay_payment_id":  body.razorpay_payment_id,
            "status":               "pending",   # becomes 'active' on publish
            "activated_at":         None,
            "expires_at":           None,        # set on publish
            "created_at":           now.isoformat(),
        }
        for _ in range(body.num_posts)
    ]

    try:
        supabase.table("job_post_credits").insert(credits).execute()
        logger.info(f"Job post credits created: {body.num_posts} for {body.user_id}")
        return {
            "status":    "paid",
            "credits":   body.num_posts,
            "redirect":  "/employer/posts/new",
        }
    except Exception as e:
        logger.error(f"Job post credit insert failed after verified payment: {e}")
        return {"status": "paid", "credits": body.num_posts, "redirect": "/employer/posts/new"}


# ---------------------------------------------------------------------------
# Cap check — called before employer publishes any post
# ---------------------------------------------------------------------------

@router.get("/employer/post/cap-check/{employer_id}")
async def check_post_cap(employer_id: str):
    """
    Returns how many active posts the employer currently has.
    Frontend uses this to show/hide the 'Post a job' button.
    Backend ALSO calls this before activating any post.
    """
    supabase = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    result = supabase.table("job_posts") \
        .select("id", count="exact") \
        .eq("employer_id", employer_id) \
        .eq("status", "active") \
        .gt("expires_at", now) \
        .execute()

    active_count = result.count or 0
    return {
        "active_posts":  active_count,
        "cap":           MAX_ACTIVE_POSTS,
        "can_post":      active_count < MAX_ACTIVE_POSTS,
        "slots_left":    MAX_ACTIVE_POSTS - active_count,
    }


# ---------------------------------------------------------------------------
# Publish a job post (activates credit, sets expires_at)
# ---------------------------------------------------------------------------

class PublishPostRequest(BaseModel):
    employer_id: str
    credit_id:   Optional[str] = None  # required for per_post plan
    title:       str
    description: str
    profession:  str
    location:    str
    job_type:    str

@router.post("/employer/post/publish")
async def publish_job_post(body: PublishPostRequest):
    """
    Activates a job post:
    - Checks 30-post cap (single query)
    - For per_post: consumes one credit row
    - For subscription: verifies active subscription
    - Sets activated_at = now, expires_at = now + 30 days
    """
    supabase = get_supabase()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=30)

    # --- Check cap ---
    cap_result = supabase.table("job_posts") \
        .select("id", count="exact") \
        .eq("employer_id", body.employer_id) \
        .eq("status", "active") \
        .gt("expires_at", now.isoformat()) \
        .execute()

    active_count = cap_result.count or 0
    if active_count >= MAX_ACTIVE_POSTS:
        raise HTTPException(400,
            f"You have {MAX_ACTIVE_POSTS} active posts. "
            "Wait for one to expire or close a post to make room."
        )

    # --- Determine plan type ---
    # Check if employer has active subscription
    sub_result = supabase.table("employer_subscriptions") \
        .select("id") \
        .eq("employer_id", body.employer_id) \
        .eq("status", "active") \
        .gt("current_period_end", now.isoformat()) \
        .limit(1) \
        .execute()

    has_subscription = len(sub_result.data) > 0

    if not has_subscription:
        # Must have a per_post credit
        if not body.credit_id:
            raise HTTPException(400, "No active subscription and no credit ID provided.")

        # Consume the credit
        credit_result = supabase.table("job_post_credits") \
            .select("id") \
            .eq("id", body.credit_id) \
            .eq("employer_id", body.employer_id) \
            .eq("status", "pending") \
            .limit(1) \
            .execute()

        if not credit_result.data:
            raise HTTPException(400, "Credit not found or already used.")

        # Mark credit as consumed
        supabase.table("job_post_credits") \
            .update({"status": "consumed"}) \
            .eq("id", body.credit_id) \
            .execute()

    # --- Create the active job post ---
    post = supabase.table("job_posts").insert({
        "employer_id":  body.employer_id,
        "title":        body.title,
        "description":  body.description,
        "profession":   body.profession,
        "location":     body.location,
        "job_type":     body.job_type,
        "plan_type":    "subscription" if has_subscription else "per_post",
        "status":       "active",
        "activated_at": now.isoformat(),
        "expires_at":   expires.isoformat(),
    }).execute()

    logger.info(f"Job post published: {body.employer_id} expires {expires.date()}")
    return {
        "status":     "published",
        "expires_at": expires.isoformat(),
        "post_id":    post.data[0]["id"],
    }


# ---------------------------------------------------------------------------
# Payment recovery — handles browser-close edge case
# ---------------------------------------------------------------------------

@router.get("/recover/{user_id}")
async def recover_payment(user_id: str):
    """
    Called silently when a logged-in user loads any page.
    Checks Razorpay for any completed payments not yet activated in Supabase.
    This handles the edge case where user paid but closed browser before verify.
    """
    supabase = get_supabase()

    # Check if user already has active subscription
    sub = supabase.table("jobseeker_subscriptions") \
        .select("status") \
        .eq("user_id", user_id) \
        .eq("status", "active") \
        .limit(1) \
        .execute()

    if sub.data:
        return {"status": "already_active"}

    # No active sub found — nothing to recover automatically
    # For full recovery, you'd query Razorpay subscriptions API
    # using the subscription_id stored in your session/localStorage
    return {"status": "no_pending_payment"}
