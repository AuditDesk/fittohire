"""
FitToHire — Dashboard Routes
Job seeker dashboard: subscription, stats, session history, profile link.
Protected — redirects to /auth/login if not authenticated.
"""

import os
import logging
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.routers.auth import get_current_user, get_supabase

logger = logging.getLogger(__name__)
router = APIRouter(tags=["dashboard"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def days_until(iso_date: str) -> int:
    try:
        end = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return max(0, (end - datetime.now(timezone.utc)).days)
    except Exception:
        return 0


def fmt_date(iso_date: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        return dt.strftime("%d %b %Y")
    except Exception:
        return ""


@router.get("/dashboard")
async def jobseeker_dashboard(request: Request):
    user = await get_current_user(request)

    if not user:
        return RedirectResponse("/auth/login?next=/dashboard", status_code=302)

    if user.get("role") == "employer":
        return RedirectResponse("/employer/dashboard", status_code=302)

    supabase = get_supabase()
    user_id = user["id"]

    # Subscription
    subscription = None
    try:
        r = supabase.table("jobseeker_subscriptions") \
            .select("*").eq("user_id", user_id) \
            .order("created_at", desc=True).limit(1).execute()
        if r.data:
            s = r.data[0]
            subscription = {
                "plan":      s.get("plan", "monthly"),
                "status":    s.get("status", "expired"),
                "days_left": days_until(s.get("current_period_end", "")),
            }
    except Exception as e:
        logger.warning(f"Subscription fetch failed: {e}")

    # Recent sessions (last 5)
    sessions = []
    try:
        r = supabase.table("interview_sessions") \
            .select("id,profession,score,question_count,badge_label,completed_at") \
            .eq("user_id", user_id) \
            .order("completed_at", desc=True).limit(5).execute()
        for s in (r.data or []):
            sessions.append({
                "id":             s.get("id"),
                "profession":     s.get("profession", "General"),
                "score":          s.get("score", 0),
                "question_count": s.get("question_count", 10),
                "badge_label":    s.get("badge_label"),
                "completed_at":   fmt_date(s.get("completed_at", "")),
            })
    except Exception as e:
        logger.warning(f"Sessions fetch failed: {e}")

    # Aggregate stats
    stats = {"total_sessions": 0, "avg_score": 0, "best_score": 0, "badges": 0}
    try:
        r = supabase.table("interview_sessions") \
            .select("score,badge_label").eq("user_id", user_id).execute()
        if r.data:
            scores = [s["score"] for s in r.data if s.get("score") is not None]
            stats = {
                "total_sessions": len(scores),
                "avg_score":      round(sum(scores) / len(scores)) if scores else 0,
                "best_score":     max(scores) if scores else 0,
                "badges":         sum(1 for s in r.data if s.get("badge_label")),
            }
    except Exception as e:
        logger.warning(f"Stats fetch failed: {e}")

    # Profile
    profile = None
    try:
        r = supabase.table("jobseeker_profiles") \
            .select("public_slug,profession") \
            .eq("user_id", user_id).limit(1).execute()
        if r.data:
            profile = r.data[0]
    except Exception as e:
        logger.warning(f"Profile fetch failed: {e}")

    return templates.TemplateResponse(
        request=request,
        name="dashboard/jobseeker.html",
        context={
            "user":         user,
            "subscription": subscription,
            "sessions":     sessions,
            "stats":        stats,
            "profile":      profile,
        }
    )
