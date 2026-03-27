"""
FitToHire — Public Profile Routes
/p/{slug} — public proof profile, no login required
"""

import os
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["profile"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def get_supabase():
    from supabase import create_client
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY")
    )


def initials(name: str = "", email: str = "") -> str:
    if name and len(name.strip()) >= 2:
        parts = name.strip().split()
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else parts[0][1])).upper()
    return email[:2].upper() if email else "?"


@router.get("/p/{slug}")
async def public_profile(request: Request, slug: str):
    """
    Public proof profile — visible to everyone, no login required.
    Shows best score per profession, badges, contact info if opted in.
    Employers with subscription can view answers link.
    """
    supabase = get_supabase()

    # Get current user (if logged in) to determine employer status
    from app.routers.auth import get_current_user
    current_user = await get_current_user(request)
    is_logged_in = current_user is not None

    # Check if logged-in user is a subscribed employer
    is_employer_subscriber = False
    if current_user and current_user.get("role") == "employer":
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            emp_sub = supabase.table("employer_subscriptions") \
                .select("id") \
                .eq("employer_id", current_user["id"]) \
                .eq("status", "active") \
                .gt("current_period_end", now) \
                .limit(1).execute()
            is_employer_subscriber = bool(emp_sub.data)
        except Exception as e:
            logger.error(f"Employer sub check error: {e}")

    # Look up profile by slug
    profile_data = None
    try:
        result = supabase.table("jobseeker_profiles") \
            .select("*, users(id, email, full_name, role)") \
            .eq("public_slug", slug) \
            .limit(1).execute()
        if result.data:
            profile_data = result.data[0]
    except Exception as e:
        logger.error(f"Profile lookup error: {e}")

    if not profile_data:
        return templates.TemplateResponse(
            request=request,
            name="profile/public.html",
            context={"profile": None, "slug": slug,
                     "profession_scores": [], "badges": [],
                     "is_logged_in": is_logged_in,
                     "is_employer_subscriber": is_employer_subscriber}
        )

    user_data = profile_data.get("users", {}) or {}
    user_id = user_data.get("id") or profile_data.get("user_id")
    email = user_data.get("email", "")
    name = user_data.get("full_name") or profile_data.get("full_name", "")

    # Check if profile is private
    is_private = profile_data.get("profile_visibility", "public") == "private"

    # Get best score per profession from completed sessions
    profession_scores = []
    best_overall = 0
    total_sessions = 0
    try:
        sessions = supabase.table("interview_sessions") \
            .select("profession, score, id") \
            .eq("user_id", user_id) \
            .eq("status", "completed") \
            .gte("score", 60) \
            .order("score", desc=True) \
            .execute()

        # Group by profession, keep best score
        seen = {}
        for s in (sessions.data or []):
            prof = s["profession"]
            score = s["score"] or 0
            if prof not in seen or score > seen[prof]["best_score"]:
                seen[prof] = {
                    "profession": prof,
                    "best_score": score,
                    "best_session_id": s["id"],
                    "session_count": 0,
                }

        # Count sessions per profession
        all_sessions = supabase.table("interview_sessions") \
            .select("profession", count="exact") \
            .eq("user_id", user_id) \
            .eq("status", "completed") \
            .execute()
        total_sessions = all_sessions.count or 0

        for s in (all_sessions.data or []):
            if s["profession"] in seen:
                seen[s["profession"]]["session_count"] = \
                    seen[s["profession"]].get("session_count", 0) + 1

        profession_scores = sorted(seen.values(), key=lambda x: x["best_score"], reverse=True)
        best_overall = profession_scores[0]["best_score"] if profession_scores else 0

    except Exception as e:
        logger.error(f"Sessions fetch error: {e}")

    # Get badges
    badges = []
    try:
        b = supabase.table("badges") \
            .select("icon, label, profession, score") \
            .eq("user_id", user_id) \
            .order("score", desc=True) \
            .limit(12).execute()
        badges = b.data or []
    except Exception as e:
        logger.error(f"Badges fetch error: {e}")

    # Build profile object
    profile = {
        "user_id":         user_id,
        "name":            name,
        "email":           email if profile_data.get("show_contact") else None,
        "email_prefix":    email.split("@")[0].title() if email else "Candidate",
        "initials":        initials(name, email),
        "profession":      profile_data.get("profession", ""),
        "location":        profile_data.get("location", ""),
        "bio":             profile_data.get("bio", ""),
        "best_overall":    best_overall,
        "total_sessions":  total_sessions,
        "badge_count":     len(badges),
        "profession_count": len(profession_scores),
        "show_contact":    profile_data.get("show_contact", False),
        "is_private":      is_private,
    }

    return templates.TemplateResponse(
        request=request,
        name="profile/public.html",
        context={
            "profile":               profile,
            "slug":                  slug,
            "profession_scores":     profession_scores,
            "badges":                badges,
            "is_logged_in":          is_logged_in,
            "is_employer_subscriber": is_employer_subscriber,
        }
    )


@router.get("/profile/setup")
async def profile_setup(request: Request):
    """Redirect to dashboard for now — profile setup page to be built"""
    return RedirectResponse("/dashboard")
