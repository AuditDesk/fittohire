"""
FitToHire — Dashboard Routes
Job seeker dashboard: subscription, stats, session history, profile link,
employer interaction notifications.
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
async def jobseeker_dashboard(request: Request, msg: str = ""):
    user = await get_current_user(request)

    if not user:
        return RedirectResponse("/auth/login?next=/dashboard", status_code=302)

    if user.get("role") == "employer":
        return RedirectResponse("/employer/dashboard", status_code=302)

    supabase = get_supabase()
    user_id = user["id"]

    # Subscription
    subscription = None
    plan = None
    try:
        r = supabase.table("jobseeker_subscriptions") \
            .select("*").eq("user_id", user_id) \
            .eq("status", "active") \
            .order("created_at", desc=True).limit(1).execute()
        if r.data:
            s = r.data[0]
            plan = s.get("plan", "js_monthly")
            subscription = {
                "plan":      plan,
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

    # Employer interaction notifications
    # Annual subscribers see employer name + action
    # Monthly subscribers see count only
    employer_notifications = []
    if plan == "js_annual":
        try:
            notifs = supabase.table("employer_interactions") \
                .select("interaction, employer_name, created_at") \
                .eq("candidate_id", user_id) \
                .in_("interaction", ["shortlist", "like_profile"]) \
                .order("created_at", desc=True) \
                .limit(10).execute()
            for n in (notifs.data or []):
                action_label = "shortlisted you" if n["interaction"] == "shortlist" else "liked your profile"
                employer_name = n.get("employer_name") or "An employer"
                created = n.get("created_at", "")
                try:
                    dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    days_ago = (datetime.now(timezone.utc) - dt).days
                    when = "today" if days_ago == 0 else f"{days_ago}d ago"
                except Exception:
                    when = ""
                employer_notifications.append({
                    "text":   f"{employer_name} {action_label}",
                    "when":   when,
                    "action": n["interaction"],
                })
        except Exception as e:
            logger.error(f"Notification fetch error: {e}")

    # View/interaction count — all subscribers see total count
    view_count = 0
    try:
        views = supabase.table("employer_interactions") \
            .select("id", count="exact") \
            .eq("candidate_id", user_id) \
            .execute()
        view_count = views.count or 0
    except Exception as e:
        logger.error(f"View count error: {e}")

    return templates.TemplateResponse(
        request=request,
        name="dashboard/jobseeker.html",
        context={
            "msg":                    msg,
            "user":                   user,
            "subscription":           subscription,
            "sessions":               sessions,
            "stats":                  stats,
            "profile":                profile,
            "employer_notifications": employer_notifications,
            "view_count":             view_count,
        }
    )
