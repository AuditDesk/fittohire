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

    # If user is registered as job seeker, redirect to job seeker dashboard
    if user.get("role") == "job_seeker":
        return RedirectResponse("/dashboard?msg=wrong_role")

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


# ── Job posting routes ────────────────────────────────────────────────

EMPLOYMENT_LABELS = {
    "full_time": "Full-time", "part_time": "Part-time",
    "contract": "Contract", "internship": "Internship",
}
WORK_MODE_LABELS = {
    "office": "On-site", "remote": "Remote", "hybrid": "Hybrid",
}

MAX_ACTIVE_POSTS = 30


@router.get("/jobs")
async def jobs_list(request: Request):
    user = await get_employer_user(request)
    if not user:
        return RedirectResponse("/auth/login?next=/employer/jobs")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)

    job_posts = []
    active_count = 0
    try:
        posts = supabase.table("job_posts") \
            .select("*") \
            .eq("employer_id", user["id"]) \
            .order("created_at", desc=True).execute()

        now = datetime.now(timezone.utc)
        for p in (posts.data or []):
            expires_at = p.get("expires_at", "")
            posted_at = p.get("created_at", "")
            is_active = p.get("is_active", False)

            days_left = 0
            expires_date = ""
            try:
                exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                days_left = max(0, (exp - now).days)
                expires_date = exp.strftime("%d %b %Y")
                if exp < now:
                    is_active = False
            except Exception:
                pass

            posted_date = ""
            try:
                posted_date = datetime.fromisoformat(
                    posted_at.replace("Z", "+00:00")
                ).strftime("%d %b %Y")
            except Exception:
                pass

            if is_active:
                active_count += 1

            job_posts.append({
                "id":               p.get("id"),
                "title":            p.get("title", ""),
                "profession":       p.get("profession", ""),
                "location":         p.get("location", ""),
                "employment_type_label": EMPLOYMENT_LABELS.get(p.get("employment_type", ""), "Full-time"),
                "work_mode_label":  WORK_MODE_LABELS.get(p.get("work_mode", ""), "On-site"),
                "work_mode":        p.get("work_mode", "office"),
                "salary_min":       p.get("salary_min", ""),
                "salary_max":       p.get("salary_max", ""),
                "is_active":        is_active,
                "status_label":     "Active" if is_active else "Expired",
                "status_class":     "active" if is_active else "expired",
                "posted_date":      posted_date,
                "expires_date":     expires_date,
                "days_left":        days_left,
            })
    except Exception as e:
        logger.error(f"Jobs list error: {e}")

    return templates.TemplateResponse(
        request=request,
        name="employer/jobs.html",
        context={
            "user":         user,
            "is_subscribed": is_subscribed,
            "job_posts":    job_posts,
            "active_count": active_count,
        }
    )


@router.get("/jobs/new")
async def jobs_new_page(request: Request):
    user = await get_employer_user(request)
    if not user:
        return RedirectResponse("/auth/login?next=/employer/jobs/new")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)

    # Check active post cap
    active_count = 0
    try:
        r = supabase.table("job_posts") \
            .select("id", count="exact") \
            .eq("employer_id", user["id"]) \
            .eq("is_active", True).execute()
        active_count = r.count or 0
    except Exception:
        pass

    if active_count >= MAX_ACTIVE_POSTS:
        return RedirectResponse("/employer/jobs?msg=cap_reached")

    # Get employer profile
    employer = {}
    try:
        ep = supabase.table("employer_profiles") \
            .select("*").eq("user_id", user["id"]).limit(1).execute()
        if ep.data:
            employer = ep.data[0]
    except Exception:
        pass

    stats = {"active_posts": active_count}

    return templates.TemplateResponse(
        request=request,
        name="employer/jobs_new.html",
        context={
            "user":          user,
            "employer":      employer,
            "is_subscribed": is_subscribed,
            "stats":         stats,
            "professions":   PROFESSIONS,
        }
    )


from pydantic import BaseModel
from typing import Optional, List

class JobCreateRequest(BaseModel):
    title:            str
    profession:       str
    location:         str
    employment_type:  str = "full_time"
    work_mode:        str = "office"
    salary_min:       Optional[str] = None
    salary_max:       Optional[str] = None
    min_score:        Optional[int] = None
    description:      str
    requirements:     Optional[str] = None
    skills:           Optional[List[str]] = []
    company_name:     str
    company_website:  Optional[str] = None
    company_size:     Optional[str] = None
    contact_email:    str


@router.post("/jobs/create")
async def create_job_subscribed(body: JobCreateRequest, request: Request):
    """Subscribed employer — post job immediately, no payment."""
    user = await get_employer_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)
    if not is_subscribed:
        raise HTTPException(403, "Subscription required")

    # Check cap
    try:
        r = supabase.table("job_posts") \
            .select("id", count="exact") \
            .eq("employer_id", user["id"]) \
            .eq("is_active", True).execute()
        if (r.count or 0) >= MAX_ACTIVE_POSTS:
            raise HTTPException(400, "Active post limit reached (30 maximum)")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cap check error: {e}")

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=30)).isoformat()

    try:
        result = supabase.table("job_posts").insert({
            "employer_id":     user["id"],
            "title":           body.title,
            "profession":      body.profession,
            "location":        body.location,
            "employment_type": body.employment_type,
            "work_mode":       body.work_mode,
            "salary_min":      body.salary_min,
            "salary_max":      body.salary_max,
            "min_score":       body.min_score,
            "description":     body.description,
            "requirements":    body.requirements,
            "skills":          body.skills,
            "company_name":    body.company_name,
            "company_website": body.company_website,
            "company_size":    body.company_size,
            "contact_email":   body.contact_email,
            "is_active":       True,
            "created_at":      now.isoformat(),
            "expires_at":      expires_at,
        }).execute()

        # Update employer profile company info
        supabase.table("employer_profiles").upsert({
            "user_id":      user["id"],
            "company_name": body.company_name,
            "website":      body.company_website or "",
        }, on_conflict="user_id").execute()

        return {"status": "posted", "job_id": result.data[0]["id"] if result.data else None}
    except Exception as e:
        logger.error(f"Job create error: {e}")
        raise HTTPException(500, "Could not create job post. Please try again.")


@router.post("/jobs/create-order")
async def create_job_order(body: JobCreateRequest, request: Request):
    """Free employer — create Razorpay order for ₹499 job post."""
    user = await get_employer_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    supabase = get_supabase()

    # Save job as pending (not active yet)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=30)).isoformat()

    try:
        result = supabase.table("job_posts").insert({
            "employer_id":     user["id"],
            "title":           body.title,
            "profession":      body.profession,
            "location":        body.location,
            "employment_type": body.employment_type,
            "work_mode":       body.work_mode,
            "salary_min":      body.salary_min,
            "salary_max":      body.salary_max,
            "min_score":       body.min_score,
            "description":     body.description,
            "requirements":    body.requirements,
            "skills":          body.skills,
            "company_name":    body.company_name,
            "company_website": body.company_website,
            "company_size":    body.company_size,
            "contact_email":   body.contact_email,
            "is_active":       False,  # pending payment
            "created_at":      now.isoformat(),
            "expires_at":      expires_at,
        }).execute()
        job_id = result.data[0]["id"] if result.data else None
    except Exception as e:
        logger.error(f"Job pre-create error: {e}")
        raise HTTPException(500, "Could not create job post.")

    # Create Razorpay order
    import razorpay
    rz = razorpay.Client(
        auth=(os.getenv("RZP_KEY_ID"), os.getenv("RZP_KEY_SECRET"))
    )
    try:
        order = rz.order.create({
            "amount":   49900,  # ₹499 in paise
            "currency": "INR",
            "notes": {
                "employer_id": user["id"],
                "job_id":      str(job_id),
                "type":        "job_post",
            }
        })
        return {
            "order_id": order["id"],
            "key_id":   os.getenv("RZP_KEY_ID"),
            "job_id":   str(job_id),
        }
    except Exception as e:
        logger.error(f"Razorpay order create error: {e}")
        raise HTTPException(500, "Could not create payment order.")


class JobActivateRequest(BaseModel):
    order_id:   str
    payment_id: str
    signature:  str
    job_id:     str


@router.post("/jobs/activate")
async def activate_job_post(body: JobActivateRequest, request: Request):
    """Verify payment and activate job post."""
    user = await get_employer_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    # Verify Razorpay signature
    import razorpay, hmac, hashlib
    key_secret = os.getenv("RZP_KEY_SECRET", "")
    expected = hmac.new(
        key_secret.encode(),
        f"{body.order_id}|{body.payment_id}".encode(),
        hashlib.sha256
    ).hexdigest()
    if expected != body.signature:
        raise HTTPException(400, "Payment verification failed")

    supabase = get_supabase()
    try:
        supabase.table("job_posts") \
            .update({"is_active": True}) \
            .eq("id", body.job_id) \
            .eq("employer_id", user["id"]).execute()

        # Record credit usage
        supabase.table("job_post_credits").insert({
            "employer_id":  user["id"],
            "job_id":       body.job_id,
            "order_id":     body.order_id,
            "payment_id":   body.payment_id,
            "amount_paise": 49900,
            "created_at":   datetime.now(timezone.utc).isoformat(),
        }).execute()

        return {"status": "activated"}
    except Exception as e:
        logger.error(f"Job activate error: {e}")
        raise HTTPException(500, "Could not activate job post.")


@router.post("/jobs/{job_id}/close")
async def close_job(job_id: str, request: Request):
    user = await get_employer_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    supabase = get_supabase()
    try:
        supabase.table("job_posts") \
            .update({"is_active": False}) \
            .eq("id", job_id) \
            .eq("employer_id", user["id"]).execute()
        return {"status": "closed"}
    except Exception as e:
        logger.error(f"Job close error: {e}")
        raise HTTPException(500, "Could not close job post.")


# ── Answer view route ─────────────────────────────────────────────────

@router.get("/candidate/{candidate_id}/answers/{session_id}")
async def view_answers(candidate_id: str, session_id: str, request: Request):
    """
    Subscribed employer reads a candidate's actual interview answers.
    Also records a 'view' interaction for the candidate's notification feed.
    """
    user = await get_employer_user(request)
    if not user:
        return RedirectResponse(f"/auth/login?next=/employer/candidate/{candidate_id}/answers/{session_id}")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)

    # Get candidate profile
    candidate = {}
    try:
        profile = supabase.table("jobseeker_profiles") \
            .select("*").eq("user_id", candidate_id).limit(1).execute()
        u = supabase.table("users") \
            .select("email, full_name").eq("id", candidate_id).limit(1).execute()

        p = profile.data[0] if profile.data else {}
        ud = u.data[0] if u.data else {}
        name = p.get("full_name") or ud.get("full_name", "")
        email = ud.get("email", "")

        candidate = {
            "user_id":      candidate_id,
            "slug":         p.get("public_slug", ""),
            "name":         name or email.split("@")[0].title(),
            "initials":     initials(name, email),
            "email":        email if p.get("show_contact") else None,
            "show_contact": p.get("show_contact", False),
            "profession":   p.get("profession", ""),
            "location":     p.get("location", ""),
            "availability": p.get("availability_status", "open"),
        }
    except Exception as e:
        logger.error(f"Candidate fetch error: {e}")

    if not is_subscribed:
        return templates.TemplateResponse(
            request=request,
            name="employer/answers.html",
            context={
                "candidate":     candidate,
                "is_subscribed": False,
                "sessions":      [],
                "answers":       [],
                "current_session": {},
                "is_shortlisted": False,
                "employer_note":  "",
            }
        )

    # Record view interaction (non-blocking, ignore errors)
    try:
        employer_name = ""
        ep = supabase.table("employer_profiles") \
            .select("company_name").eq("user_id", user["id"]).limit(1).execute()
        if ep.data:
            employer_name = ep.data[0].get("company_name", "")

        supabase.table("employer_interactions").upsert({
            "employer_id":   user["id"],
            "candidate_id":  candidate_id,
            "interaction":   "view",
            "employer_name": employer_name,
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }, on_conflict="employer_id,candidate_id,interaction").execute()
    except Exception as e:
        logger.error(f"View record error: {e}")

    # Get all completed sessions for this candidate
    sessions = []
    current_session = {}
    try:
        all_sessions = supabase.table("interview_sessions") \
            .select("id, profession, score, question_count, badge_label, completed_at, score_breakdown") \
            .eq("user_id", candidate_id) \
            .eq("status", "completed") \
            .order("completed_at", desc=True).execute()

        for s in (all_sessions.data or []):
            date_str = ""
            try:
                dt = datetime.fromisoformat(s["completed_at"].replace("Z", "+00:00"))
                date_str = dt.strftime("%d %b")
            except Exception:
                pass

            session_obj = {
                "id":             s["id"],
                "profession":     s.get("profession", ""),
                "score":          s.get("score", 0),
                "question_count": s.get("question_count", 10),
                "badge_label":    s.get("badge_label", ""),
                "date":           date_str,
                "breakdown":      s.get("score_breakdown") or {},
            }
            sessions.append(session_obj)
            if s["id"] == session_id:
                current_session = session_obj

        if not current_session and sessions:
            current_session = sessions[0]

    except Exception as e:
        logger.error(f"Sessions fetch error: {e}")

    # Get answers for current session
    answers = []
    liked_indices = set()
    try:
        # Get liked answer indices
        likes = supabase.table("employer_interactions") \
            .select("answer_index") \
            .eq("employer_id", user["id"]) \
            .eq("candidate_id", candidate_id) \
            .eq("interaction", "like_answer").execute()
        liked_indices = {l["answer_index"] for l in (likes.data or []) if l.get("answer_index") is not None}

        # Get answers
        ans = supabase.table("interview_answers") \
            .select("question, answer, score, score_breakdown") \
            .eq("session_id", current_session.get("id", session_id)) \
            .order("question_index").execute()

        for i, a in enumerate(ans.data or []):
            answers.append({
                "question":        a.get("question", ""),
                "answer":          a.get("answer", ""),
                "score":           a.get("score"),
                "score_breakdown": a.get("score_breakdown") or {},
                "is_liked":        i in liked_indices,
            })
    except Exception as e:
        logger.error(f"Answers fetch error: {e}")

    # Check if shortlisted
    is_shortlisted = False
    try:
        sl = supabase.table("employer_interactions") \
            .select("id") \
            .eq("employer_id", user["id"]) \
            .eq("candidate_id", candidate_id) \
            .eq("interaction", "shortlist").limit(1).execute()
        is_shortlisted = bool(sl.data)
    except Exception:
        pass

    # Get employer's note on this candidate
    employer_note = ""
    try:
        note = supabase.table("employer_interactions") \
            .select("note_text") \
            .eq("employer_id", user["id"]) \
            .eq("candidate_id", candidate_id) \
            .eq("interaction", "note").limit(1).execute()
        if note.data:
            employer_note = note.data[0].get("note_text", "")
    except Exception:
        pass

    return templates.TemplateResponse(
        request=request,
        name="employer/answers.html",
        context={
            "candidate":       candidate,
            "is_subscribed":   is_subscribed,
            "sessions":        sessions,
            "current_session": current_session,
            "answers":         answers,
            "is_shortlisted":  is_shortlisted,
            "employer_note":   employer_note,
        }
    )


# ── Analytics route ───────────────────────────────────────────────────

@router.get("/analytics")
async def employer_analytics(request: Request):
    user = await get_employer_user(request)
    if not user:
        return RedirectResponse("/auth/login?next=/employer/analytics")

    supabase = get_supabase()
    is_subscribed = await check_employer_subscription(user["id"], supabase)

    if not is_subscribed:
        return templates.TemplateResponse(
            request=request,
            name="employer/analytics.html",
            context={"user": user, "is_subscribed": False,
                     "stats": {}, "score_dist": [], "top_professions": [],
                     "recent_shortlisted": [], "activity": []}
        )

    stats = {"total_viewed": 0, "shortlisted": 0, "active_posts": 0, "notes_written": 0}
    score_dist = []
    top_professions = []
    recent_shortlisted = []
    activity = []

    try:
        # Stats
        interactions = supabase.table("employer_interactions") \
            .select("interaction, candidate_id, employer_name, created_at") \
            .eq("employer_id", user["id"]).execute()
        all_ints = interactions.data or []

        stats["total_viewed"]  = sum(1 for i in all_ints if i["interaction"] == "view")
        stats["shortlisted"]   = sum(1 for i in all_ints if i["interaction"] == "shortlist")
        stats["notes_written"] = sum(1 for i in all_ints if i["interaction"] == "note")

        posts = supabase.table("job_posts") \
            .select("id", count="exact") \
            .eq("employer_id", user["id"]) \
            .eq("is_active", True).execute()
        stats["active_posts"] = posts.count or 0

        # Shortlisted candidate IDs
        shortlisted_ids = [i["candidate_id"] for i in all_ints if i["interaction"] == "shortlist"]

        # Score distribution of shortlisted candidates
        bands = [
            {"label": "90–100%", "min": 90, "max": 100, "color": "#D97706", "count": 0},
            {"label": "75–89%",  "min": 75, "max": 89,  "color": "#6B7280", "count": 0},
            {"label": "60–74%",  "min": 60, "max": 74,  "color": "#92400E", "count": 0},
            {"label": "Below 60","min": 0,  "max": 59,  "color": "#E5E7EB", "count": 0},
        ]

        prof_counts = {}
        for cid in shortlisted_ids:
            try:
                best = supabase.table("interview_sessions") \
                    .select("score, profession") \
                    .eq("user_id", cid).eq("status", "completed") \
                    .order("score", desc=True).limit(1).execute()
                if best.data:
                    score = best.data[0]["score"] or 0
                    prof  = best.data[0].get("profession", "Other")
                    for band in bands:
                        if band["min"] <= score <= band["max"]:
                            band["count"] += 1
                            break
                    prof_counts[prof] = prof_counts.get(prof, 0) + 1
            except Exception:
                pass

        score_dist = bands
        top_professions = sorted(
            [{"profession": p, "count": c} for p, c in prof_counts.items()],
            key=lambda x: x["count"], reverse=True
        )[:5]

        # Recent shortlisted with profile info
        recent_sl_ids = [i["candidate_id"] for i in sorted(
            [i for i in all_ints if i["interaction"] == "shortlist"],
            key=lambda x: x["created_at"], reverse=True
        )][:5]

        for cid in recent_sl_ids:
            try:
                p = supabase.table("jobseeker_profiles") \
                    .select("public_slug, profession, location, full_name") \
                    .eq("user_id", cid).limit(1).execute()
                u = supabase.table("users") \
                    .select("email, full_name").eq("id", cid).limit(1).execute()
                pd = p.data[0] if p.data else {}
                ud = u.data[0] if u.data else {}
                name = pd.get("full_name") or ud.get("full_name", "")
                email = ud.get("email", "")
                display = name or email.split("@")[0].title()

                best = supabase.table("interview_sessions") \
                    .select("score").eq("user_id", cid).eq("status", "completed") \
                    .order("score", desc=True).limit(1).execute()
                best_score = best.data[0]["score"] if best.data else 0

                recent_shortlisted.append({
                    "slug":       pd.get("public_slug", ""),
                    "name":       display,
                    "initials":   initials(name, email),
                    "profession": pd.get("profession", ""),
                    "location":   pd.get("location", ""),
                    "best_score": best_score,
                })
            except Exception:
                pass

        # Activity timeline
        now = datetime.now(timezone.utc)
        action_labels = {
            "shortlist":   ("shortlist", "Shortlisted a candidate"),
            "view":        ("view",      "Viewed candidate answers"),
            "note":        ("note",      "Added a private note"),
            "like_answer": ("note",      "Liked an interview answer"),
        }
        for item in sorted(all_ints, key=lambda x: x["created_at"], reverse=True)[:10]:
            action = item["interaction"]
            if action not in action_labels:
                continue
            dot_type, label = action_labels[action]
            try:
                dt = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00"))
                days_ago = (now - dt).days
                when = "today" if days_ago == 0 else f"{days_ago}d ago"
            except Exception:
                when = ""
            activity.append({"type": dot_type, "text": label, "when": when})

    except Exception as e:
        logger.error(f"Analytics error: {e}")

    return templates.TemplateResponse(
        request=request,
        name="employer/analytics.html",
        context={
            "user":              user,
            "is_subscribed":     is_subscribed,
            "stats":             stats,
            "score_dist":        score_dist,
            "top_professions":   top_professions,
            "recent_shortlisted": recent_shortlisted,
            "activity":          activity,
        }
    )
