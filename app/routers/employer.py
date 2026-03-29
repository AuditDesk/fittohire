"""
FitToHire — Employer Routes
/employer/dashboard    — main employer dashboard
/employer/candidates   — browse and search candidates
/employer/shortlist    — saved candidates
/employer/jobs/new     — post a job
/employer/jobs         — manage job posts
"""

import os
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/employer", tags=["employer"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

PROFESSIONS = [
    "Chartered Accountant","Software Developer","Financial Analyst",
    "HR Manager","Data Analyst","Product Manager","Tax Consultant",
    "DevOps Engineer","Business Analyst","Sales Executive","Digital Marketer",
    "Operations Manager","Investment Banker","Data Scientist","Project Manager",
    "Supply Chain Manager","Recruiter","UI/UX Designer","Content Writer",
    "Legal / Compliance","Cybersecurity Analyst","Customer Success",
    "Brand Manager","Quality Analyst","Hospital Administrator","Pharmacist",
    "Teacher / Trainer","Civil Engineer","Mechanical Engineer",
    "Electrical Engineer","Bank Relationship Manager","Credit Analyst",
    "Real Estate Agent","Admin Executive","Counsellor","Journalist",
    "PR Manager","Logistics Manager","Warehouse Manager","Accountant",
    "CPA","MBA Graduate","Branch Manager","Academic Coordinator",
    "Video Editor","Graphic Designer","Social Media Manager",
    "Fleet Coordinator","Property Manager",
]


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


async def get_employer_user(request: Request):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        return None
    return user


async def check_employer_subscription(user_id: str, supabase) -> bool:
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = supabase.table("employer_subscriptions") \
            .select("id") \
            .eq("employer_id", user_id) \
            .eq("status", "active") \
            .gt("current_period_end", now) \
            .limit(1).execute()
        return bool(result.data)
    except Exception as e:
        logger.error(f"Employer sub check error: {e}")
        return False


@router.get("/dashboard")
async def employer_dashboard(request: Request):
    user = await get_employer_user(request)
    if not user:
        return RedirectResponse("/auth/login?next=/employer/dashboard")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)

    # Get employer profile
    employer = {}
    try:
        ep = supabase.table("employer_profiles") \
            .select("*").eq("user_id", user["id"]).limit(1).execute()
        if ep.data:
            employer = ep.data[0]
    except Exception as e:
        logger.error(f"Employer profile fetch error: {e}")

    # Platform stats
    stats = {"total_candidates": 0, "actively_looking": 0, "shortlisted": 0, "active_posts": 0}
    try:
        # Total candidates with public profiles
        total = supabase.table("jobseeker_profiles") \
            .select("id", count="exact") \
            .eq("profile_visibility", "public") \
            .execute()
        stats["total_candidates"] = total.count or 0

        # Actively looking
        active = supabase.table("jobseeker_profiles") \
            .select("id", count="exact") \
            .eq("profile_visibility", "public") \
            .eq("availability_status", "actively_looking") \
            .execute()
        stats["actively_looking"] = active.count or 0

        # Employer's shortlist
        shortlist = supabase.table("employer_interactions") \
            .select("id", count="exact") \
            .eq("employer_id", user["id"]) \
            .eq("interaction", "shortlist") \
            .execute()
        stats["shortlisted"] = shortlist.count or 0

        # Active job posts
        posts = supabase.table("job_posts") \
            .select("id", count="exact") \
            .eq("employer_id", user["id"]) \
            .eq("is_active", True) \
            .execute()
        stats["active_posts"] = posts.count or 0

    except Exception as e:
        logger.error(f"Stats fetch error: {e}")

    # Recent candidates
    recent_candidates = []
    try:
        profiles = supabase.table("jobseeker_profiles") \
            .select("user_id, public_slug, profession, location, availability_status, full_name") \
            .eq("profile_visibility", "public") \
            .order("updated_at", desc=True) \
            .limit(6).execute()

        for p in (profiles.data or []):
            # Get best score for this user
            best = supabase.table("interview_sessions") \
                .select("score") \
                .eq("user_id", p["user_id"]) \
                .eq("status", "completed") \
                .order("score", desc=True) \
                .limit(1).execute()

            best_score = best.data[0]["score"] if best.data else 0
            if best_score < 60:
                continue

            # Get user email for initials fallback
            u = supabase.table("users") \
                .select("email, full_name") \
                .eq("id", p["user_id"]) \
                .limit(1).execute()
            user_data = u.data[0] if u.data else {}
            name = p.get("full_name") or user_data.get("full_name", "")
            email = user_data.get("email", "")

            recent_candidates.append({
                "slug":         p.get("public_slug", ""),
                "name":         name or email.split("@")[0].title(),
                "initials":     initials(name, email),
                "profession":   p.get("profession", ""),
                "location":     p.get("location", ""),
                "availability": p.get("availability_status", "open"),
                "best_score":   best_score,
            })
    except Exception as e:
        logger.error(f"Recent candidates error: {e}")

    # Job posts
    job_posts = []
    try:
        posts = supabase.table("job_posts") \
            .select("*") \
            .eq("employer_id", user["id"]) \
            .order("created_at", desc=True) \
            .limit(5).execute()

        for p in (posts.data or []):
            expires_at = p.get("expires_at")
            expires_formatted = ""
            is_active = p.get("is_active", False)
            if expires_at:
                try:
                    exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                    expires_formatted = exp.strftime("%d %b %Y")
                    if exp < datetime.now(timezone.utc):
                        is_active = False
                except Exception:
                    pass

            job_posts.append({
                "title":             p.get("title", ""),
                "profession":        p.get("profession", ""),
                "is_active":         is_active,
                "applicant_count":   p.get("applicant_count", 0),
                "expires_formatted": expires_formatted,
            })
    except Exception as e:
        logger.error(f"Job posts fetch error: {e}")

    return templates.TemplateResponse(
        request=request,
        name="employer/dashboard.html",
        context={
            "user":              user,
            "employer":          employer,
            "is_subscribed":     is_subscribed,
            "stats":             stats,
            "recent_candidates": recent_candidates,
            "job_posts":         job_posts,
            "professions":       PROFESSIONS,
            "search_q":          "",
        }
    )


@router.get("/candidates")
async def browse_candidates(
    request: Request,
    q: str = "",
    profession: str = "",
    availability: str = "",
    min_score: str = "",
    location: str = "",
    notice: str = "",
    sort: str = "score",
    page: int = 1,
):
    user = await get_employer_user(request)
    if not user:
        return RedirectResponse("/auth/login?next=/employer/candidates")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)

    PAGE_SIZE = 20
    offset = (page - 1) * PAGE_SIZE

    # Build query
    try:
        query = supabase.table("jobseeker_profiles") \
            .select("user_id, public_slug, profession, location, availability_status, available_from, notice_period_days, full_name", count="exact") \
            .eq("profile_visibility", "public")

        if profession:
            query = query.eq("profession", profession)
        if availability:
            query = query.eq("availability_status", availability)
        if location:
            query = query.ilike("location", f"%{location}%")
        if notice == "0":
            query = query.eq("notice_period_days", 0)
        elif notice == "30":
            query = query.lte("notice_period_days", 30)
        elif notice == "60":
            query = query.lte("notice_period_days", 60)

        result = query.range(offset, offset + PAGE_SIZE - 1).execute()
        all_profiles = result.data or []
        total_raw = result.count or 0

    except Exception as e:
        logger.error(f"Candidate browse error: {e}")
        all_profiles = []
        total_raw = 0

    # Get employer's shortlists and notes
    shortlisted_ids = set()
    noted_ids = set()
    try:
        interactions = supabase.table("employer_interactions") \
            .select("candidate_id, action") \
            .eq("employer_id", user["id"]).execute()
        for i in (interactions.data or []):
            if i["interaction"] == "shortlist":
                shortlisted_ids.add(i["candidate_id"])
            elif i["interaction"] == "note":
                noted_ids.add(i["candidate_id"])
    except Exception as e:
        logger.error(f"Interactions fetch error: {e}")

    # Enrich with scores and badges
    candidates = []
    min_score_val = int(min_score) if min_score else 0

    for p in all_profiles:
        uid = p["user_id"]
        try:
            # Best score
            best = supabase.table("interview_sessions") \
                .select("score") \
                .eq("user_id", uid).eq("status", "completed") \
                .order("score", desc=True).limit(1).execute()
            best_score = best.data[0]["score"] if best.data else 0
            if best_score < 60:
                continue
            if min_score_val and best_score < min_score_val:
                continue

            # Session count
            sess = supabase.table("interview_sessions") \
                .select("id", count="exact") \
                .eq("user_id", uid).eq("status", "completed").execute()
            session_count = sess.count or 0

            # Badges
            bdg = supabase.table("badges") \
                .select("label").eq("user_id", uid).limit(5).execute()
            badges = [b["label"] for b in (bdg.data or [])]

            # User info
            u = supabase.table("users") \
                .select("email, full_name") \
                .eq("id", uid).limit(1).execute()
            udata = u.data[0] if u.data else {}
            name = p.get("full_name") or udata.get("full_name", "")
            email = udata.get("email", "")
            display_name = name or email.split("@")[0].title()

            # Search filter
            if q and q.lower() not in display_name.lower() and q.lower() not in (p.get("profession") or "").lower():
                continue

            candidates.append({
                "user_id":          uid,
                "slug":             p.get("public_slug", ""),
                "name":             display_name,
                "initials":         initials(name, email),
                "profession":       p.get("profession", ""),
                "location":         p.get("location", ""),
                "availability":     p.get("availability_status", "open"),
                "available_from":   p.get("available_from", ""),
                "notice_period_days": p.get("notice_period_days"),
                "best_score":       best_score,
                "session_count":    session_count,
                "badge_count":      len(badges),
                "badges":           badges[:3],
                "is_shortlisted":   uid in shortlisted_ids,
                "has_note":         uid in noted_ids,
            })
        except Exception as e:
            logger.error(f"Candidate enrich error for {uid}: {e}")
            continue

    # Sort
    if sort == "score":
        candidates.sort(key=lambda x: x["best_score"], reverse=True)
    elif sort == "availability":
        order = {"actively_looking": 0, "open": 1, "not_looking": 2}
        candidates.sort(key=lambda x: order.get(x["availability"], 2))

    total = len(candidates)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    paged = candidates[(page-1)*PAGE_SIZE : page*PAGE_SIZE]

    # Build query string for pagination links
    params = {k: v for k, v in {"q": q, "profession": profession, "availability": availability,
              "min_score": min_score, "location": location, "notice": notice, "sort": sort}.items() if v}
    query_string = "&".join(f"{k}={v}" for k, v in params.items())

    return templates.TemplateResponse(
        request=request,
        name="employer/candidates.html",
        context={
            "user":         user,
            "is_subscribed": is_subscribed,
            "candidates":   paged,
            "total":        total,
            "page":         page,
            "total_pages":  total_pages,
            "sort":         sort,
            "filters":      {"q": q, "profession": profession, "availability": availability,
                            "min_score": min_score, "location": location, "notice": notice},
            "professions":  PROFESSIONS,
            "query_string": query_string,
        }
    )


from pydantic import BaseModel
from typing import Optional

class EmployerActionRequest(BaseModel):
    candidate_id: str
    action: str  # shortlist, unshortlist, note, like_profile
    note: Optional[str] = None


@router.post("/action")
async def employer_action(body: EmployerActionRequest, request: Request):
    """
    Handles employer interactions:
    - shortlist / unshortlist a candidate
    - save a private note about a candidate
    - like a candidate's profile
    Each action writes to employer_interactions and
    sends a notification to annual job seeker subscribers.
    """
    user = await get_employer_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)
    if not is_subscribed:
        raise HTTPException(403, "Subscription required for this action")

    # Get employer company name for notification
    employer_name = "An employer"
    try:
        ep = supabase.table("employer_profiles") \
            .select("company_name").eq("user_id", user["id"]).limit(1).execute()
        if ep.data and ep.data[0].get("company_name"):
            employer_name = ep.data[0]["company_name"]
    except Exception:
        pass

    now = datetime.now(timezone.utc).isoformat()

    if body.action == "unshortlist":
        try:
            supabase.table("employer_interactions") \
                .delete() \
                .eq("employer_id", user["id"]) \
                .eq("candidate_id", body.candidate_id) \
                .eq("interaction", "shortlist").execute()
        except Exception as e:
            logger.error(f"Unshortlist error: {e}")
        return {"status": "ok"}

    if body.action == "note":
        try:
            # Upsert note
            supabase.table("employer_interactions").upsert({
                "employer_id":   user["id"],
                "candidate_id":  body.candidate_id,
                "interaction":   "note",
                "note_text":     body.note or "",
                "created_at":    now,
            }, on_conflict="employer_id,candidate_id,interaction").execute()
        except Exception as e:
            logger.error(f"Note save error: {e}")
        return {"status": "ok"}

    # shortlist or like_profile — write interaction + notify candidate
    try:
        supabase.table("employer_interactions").upsert({
            "employer_id":   user["id"],
            "candidate_id":  body.candidate_id,
            "interaction":   body.action,
            "employer_name": employer_name,
            "created_at":    now,
        }, on_conflict="employer_id,candidate_id,interaction").execute()
    except Exception as e:
        logger.error(f"Interaction save error: {e}")
        raise HTTPException(500, "Could not save action")

    return {"status": "ok", "employer_name": employer_name}


@router.get("/note/{candidate_id}")
async def get_note(candidate_id: str, request: Request):
    user = await get_employer_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    supabase = get_supabase()
    try:
        result = supabase.table("employer_interactions") \
            .select("note") \
            .eq("employer_id", user["id"]) \
            .eq("candidate_id", candidate_id) \
            .eq("interaction", "note").limit(1).execute()
        if result.data:
            return {"note": result.data[0].get("note", "")}
    except Exception as e:
        logger.error(f"Note fetch error: {e}")
    return {"note": ""}


@router.get("/shortlist")
async def shortlist_page(request: Request):
    user = await get_employer_user(request)
    if not user:
        return RedirectResponse("/auth/login?next=/employer/shortlist")
    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)
    if not is_subscribed:
        return RedirectResponse("/employer/dashboard")

    shortlisted = []
    try:
        interactions = supabase.table("employer_interactions") \
            .select("candidate_id, note, created_at") \
            .eq("employer_id", user["id"]) \
            .eq("interaction", "shortlist") \
            .order("created_at", desc=True).execute()

        for i in (interactions.data or []):
            cid = i["candidate_id"]
            profile = supabase.table("jobseeker_profiles") \
                .select("public_slug, profession, location, full_name, availability_status") \
                .eq("user_id", cid).limit(1).execute()
            p = profile.data[0] if profile.data else {}
            u = supabase.table("users").select("email, full_name").eq("id", cid).limit(1).execute()
            udata = u.data[0] if u.data else {}
            name = p.get("full_name") or udata.get("full_name", "")
            email = udata.get("email", "")

            # Get note
            note_res = supabase.table("employer_interactions") \
                .select("note").eq("employer_id", user["id"]) \
                .eq("candidate_id", cid).eq("interaction", "note").limit(1).execute()
            note = note_res.data[0].get("note", "") if note_res.data else ""

            # Best score
            best = supabase.table("interview_sessions") \
                .select("score").eq("user_id", cid).eq("status", "completed") \
                .order("score", desc=True).limit(1).execute()
            best_score = best.data[0]["score"] if best.data else 0

            shortlisted.append({
                "user_id":    cid,
                "slug":       p.get("public_slug", ""),
                "name":       name or email.split("@")[0].title(),
                "initials":   initials(name, email),
                "profession": p.get("profession", ""),
                "location":   p.get("location", ""),
                "availability": p.get("availability_status", "open"),
                "best_score": best_score,
                "note":       note,
            })
    except Exception as e:
        logger.error(f"Shortlist fetch error: {e}")

    return templates.TemplateResponse(
        request=request,
        name="employer/shortlist.html",
        context={"user": user, "shortlisted": shortlisted, "is_subscribed": is_subscribed}
    )
