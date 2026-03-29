"""
FitToHire — Interview Engine
Routes:
  GET  /interview/start          → profession selector
  GET  /interview/rules/{prof}   → rules page before session
  POST /interview/begin          → create session + generate questions (Haiku)
  GET  /interview/session/{id}   → live interview page
  POST /interview/answer         → save one answer immediately
  POST /interview/score/{id}     → score all answers (Sonnet) + mint badge
  GET  /interview/result/{id}    → results page
  GET  /interview/preview/{prof} → 3 sample questions (all paid JS)
"""

import os
import json
import logging
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

import anthropic
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/interview", tags=["interview"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

PROFESSIONS = [
    "Chartered Accountant", "Software Developer", "Financial Analyst",
    "HR Manager", "Data Analyst", "Product Manager", "Tax Consultant",
    "DevOps Engineer", "Business Analyst", "Sales Executive",
    "Digital Marketer", "Operations Manager", "Investment Banker",
    "Data Scientist", "Project Manager", "Supply Chain Manager",
    "Recruiter", "UI/UX Designer", "Content Writer", "Legal / Compliance",
    "Cybersecurity Analyst", "Customer Success", "Brand Manager",
    "Quality Analyst", "Hospital Administrator", "Pharmacist",
    "Teacher / Trainer", "Civil Engineer", "Mechanical Engineer",
    "Electrical Engineer", "Bank Relationship Manager", "Credit Analyst",
    "Real Estate Agent", "Admin Executive", "Counsellor", "Journalist",
    "PR Manager", "Logistics Manager", "Warehouse Manager", "Accountant",
    "CPA", "MBA Graduate", "Branch Manager", "Academic Coordinator",
    "Video Editor", "Graphic Designer", "Social Media Manager",
    "Fleet Coordinator", "Property Manager",
]

DAILY_LIMITS = {"monthly": 2, "annual": 5}


def get_supabase():
    from supabase import create_client
    return create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

def get_claude():
    return anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

def badge_for_score(score: int):
    if score >= 90: return ("🥇", "Gold — Exceptional")
    if score >= 75: return ("🥈", "Silver — Strong")
    if score >= 60: return ("🥉", "Bronze — Competent")
    return (None, None)


# ---------------------------------------------------------------------------
# Attempt limit check
# ---------------------------------------------------------------------------

async def check_attempt_limit(user_id: str, profession: str, plan: str) -> tuple[bool, int, int]:
    """Returns (can_attempt, attempts_today, daily_limit)"""
    limit = DAILY_LIMITS.get(plan, 2)
    supabase = get_supabase()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        result = supabase.table("interview_sessions") \
            .select("id", count="exact") \
            .eq("user_id", user_id) \
            .eq("profession", profession) \
            .in_("status", ["completed", "in_progress"]) \
            .gte("created_at", today_start.isoformat()) \
            .execute()
        count = result.count or 0
        return count < limit, count, limit
    except Exception as e:
        logger.error(f"Attempt limit check failed: {e}")
        return True, 0, limit


# ---------------------------------------------------------------------------
# Profession selector
# ---------------------------------------------------------------------------

@router.get("/start")
async def interview_start(request: Request):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login?next=/interview/start")

    supabase = get_supabase()
    plan = "monthly"
    try:
        sub = supabase.table("jobseeker_subscriptions") \
            .select("plan, status, current_period_end") \
            .eq("user_id", user["id"]) \
            .eq("status", "active") \
            .limit(1).execute()
        if not sub.data:
            # No active subscription — redirect to dashboard with message
            return RedirectResponse("/dashboard?msg=subscription_required")
        # Check period not expired
        from datetime import datetime, timezone
        s = sub.data[0]
        if s.get("current_period_end"):
            end = datetime.fromisoformat(
                s["current_period_end"].replace("Z", "+00:00")
            )
            if end < datetime.now(timezone.utc):
                return RedirectResponse("/dashboard?msg=subscription_expired")
        plan = s["plan"]
    except Exception as e:
        logger.error(f"Subscription check failed: {e}")
        # Don't block on error — let them through with monthly defaults

    # Check for incomplete session to resume
    resume_session = None
    try:
        inc = supabase.table("interview_sessions") \
            .select("id, profession, created_at") \
            .eq("user_id", user["id"]) \
            .eq("status", "in_progress") \
            .order("created_at", desc=True) \
            .limit(1).execute()
        if inc.data:
            resume_session = inc.data[0]
    except Exception as e:
        logger.error(f"Resume check failed: {e}")

    return templates.TemplateResponse(
        request=request,
        name="interview/start.html",
        context={
            "user": user,
            "professions": PROFESSIONS,
            "plan": plan,
            "daily_limit": DAILY_LIMITS.get(plan, 2),
            "resume_session": resume_session,
        }
    )


# ---------------------------------------------------------------------------
# Rules page
# ---------------------------------------------------------------------------

@router.get("/rules/{profession:path}")
async def interview_rules(request: Request, profession: str):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login")

    supabase = get_supabase()
    plan = "monthly"
    try:
        sub = supabase.table("jobseeker_subscriptions") \
            .select("plan").eq("user_id", user["id"]) \
            .eq("status", "active").limit(1).execute()
        if sub.data:
            plan = sub.data[0]["plan"]
    except:
        pass

    can_attempt, attempts_today, daily_limit = await check_attempt_limit(
        user["id"], profession, plan
    )

    return templates.TemplateResponse(
        request=request,
        name="interview/rules.html",
        context={
            "user": user,
            "profession": profession,
            "plan": plan,
            "daily_limit": daily_limit,
            "attempts_today": attempts_today,
            "can_attempt": can_attempt,
            "is_annual": plan == "annual",
        }
    )


# ---------------------------------------------------------------------------
# Begin session — create DB row + generate questions via Haiku
# ---------------------------------------------------------------------------

class BeginRequest(BaseModel):
    profession: str
    is_custom: bool = False

@router.post("/begin")
async def begin_session(body: BeginRequest, request: Request):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    supabase = get_supabase()

    # Get plan
    plan = "monthly"
    try:
        sub = supabase.table("jobseeker_subscriptions") \
            .select("plan").eq("user_id", user["id"]) \
            .eq("status", "active").limit(1).execute()
        if sub.data:
            plan = sub.data[0]["plan"]
        else:
            raise HTTPException(403, "Active subscription required")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Plan fetch error: {e}")

    # Check attempt limit
    can_attempt, attempts_today, daily_limit = await check_attempt_limit(
        user["id"], body.profession, plan
    )
    if not can_attempt:
        raise HTTPException(429, f"You've reached your {daily_limit} daily attempts for {body.profession}. Come back tomorrow!")

    # Validate custom profession
    profession = body.profession.strip()
    if body.is_custom:
        claude = get_claude()
        validation = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            messages=[{
                "role": "user",
                "content": f"Is '{profession}' a real, legitimate profession where a job interview would make sense? Reply with only YES or NO."
            }]
        )
        answer = validation.content[0].text.strip().upper()
        if "NO" in answer:
            raise HTTPException(400, f"'{profession}' doesn't appear to be a recognised profession. Please check the spelling or choose from our list.")

    # Generate 10 questions via Claude Haiku
    claude = get_claude()
    prompt = f"""Generate exactly 10 interview questions for a {profession} role in India.
Mix question types: 2 behavioural (tell me about a time...), 3 situational (what would you do if...), 3 technical/knowledge-based, 2 motivational (why this role/career).
Make questions realistic, specific to the Indian job market context.
Return ONLY a JSON array of 10 strings, no other text.
Example: ["Question 1?", "Question 2?", ...]"""

    questions_raw = claude.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        questions_text = questions_raw.content[0].text.strip()
        # Extract JSON array robustly
        match = re.search(r'\[.*\]', questions_text, re.DOTALL)
        if match:
            questions = json.loads(match.group())
        else:
            questions = json.loads(questions_text)
        questions = questions[:10]
    except Exception as e:
        logger.error(f"Question parse error: {e}\nRaw: {questions_raw.content[0].text}")
        raise HTTPException(500, "Could not generate questions. Please try again.")

    # Create session in Supabase
    now = datetime.now(timezone.utc).isoformat()
    try:
        session = supabase.table("interview_sessions").insert({
            "user_id":        user["id"],
            "profession":     profession,
            "question_count": 10,
            "status":         "in_progress",
            "questions":      json.dumps(questions),
            "created_at":     now,
        }).execute()
        session_id = session.data[0]["id"]
    except Exception as e:
        logger.error(f"Session create error: {e}")
        raise HTTPException(500, "Could not start session. Please try again.")

    # Log custom profession request
    if body.is_custom:
        try:
            supabase.table("profession_requests").upsert({
                "profession": profession,
                "count": 1,
            }, on_conflict="profession").execute()
        except:
            pass

    return {"session_id": session_id, "profession": profession, "questions": questions}


# ---------------------------------------------------------------------------
# Save answer immediately (called after each question submitted)
# ---------------------------------------------------------------------------

class AnswerRequest(BaseModel):
    session_id: str
    question_index: int   # 0-9
    question: str
    answer: str
    input_type: str = "text"  # "text" or "voice"

@router.post("/answer")
async def save_answer(body: AnswerRequest, request: Request):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    supabase = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Upsert answer (handles reconnect/resubmit gracefully)
        supabase.table("interview_answers").upsert({
            "session_id":     body.session_id,
            "user_id":        user["id"],
            "question_index": body.question_index,
            "question":       body.question,
            "answer":         body.answer,
            "input_type":     body.input_type,
            "created_at":     now,
        }, on_conflict="session_id,question_index").execute()

        return {"status": "saved", "question_index": body.question_index}
    except Exception as e:
        logger.error(f"Answer save error: {e}")
        raise HTTPException(500, "Could not save answer.")


# ---------------------------------------------------------------------------
# Score session via Claude Sonnet + mint badge
# ---------------------------------------------------------------------------

@router.post("/score/{session_id}")
async def score_session(session_id: str, request: Request):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    supabase = get_supabase()

    # Get session
    try:
        sess = supabase.table("interview_sessions") \
            .select("*").eq("id", session_id) \
            .eq("user_id", user["id"]).limit(1).execute()
        if not sess.data:
            raise HTTPException(404, "Session not found")
        session = sess.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Session fetch error: {e}")

    # Get all answers
    try:
        answers_result = supabase.table("interview_answers") \
            .select("*").eq("session_id", session_id) \
            .order("question_index").execute()
        answers = answers_result.data or []
    except Exception as e:
        raise HTTPException(500, f"Answers fetch error: {e}")

    if len(answers) < 10:
        raise HTTPException(400, f"Only {len(answers)} answers found. Complete all 10 questions first.")

    # Get plan for feedback level
    plan = "monthly"
    try:
        sub = supabase.table("jobseeker_subscriptions") \
            .select("plan").eq("user_id", user["id"]) \
            .eq("status", "active").limit(1).execute()
        if sub.data:
            plan = sub.data[0]["plan"]
    except:
        pass

    profession = session["profession"]
    is_annual = plan in ("js_annual", "annual")

    # Build scoring prompt for Claude Sonnet
    qa_text = "\n\n".join([
        f"Q{i+1}: {a['question']}\nA{i+1}: {a['answer']}"
        for i, a in enumerate(answers)
    ])

    feedback_instruction = """For each question also provide:
- score_breakdown: {clarity: 0-100, depth: 0-100, relevance: 0-100, communication: 0-100}
- feedback: 2-3 sentences of specific, constructive feedback on this answer
- coaching_tip: one actionable tip to improve this specific answer type""" if is_annual else """For each question also provide:
- score_breakdown: {clarity: 0-100, depth: 0-100, relevance: 0-100, communication: 0-100}"""

    scoring_prompt = f"""You are an expert interviewer evaluating a {profession} candidate in India.

Score each of the 10 interview answers below. Be fair but rigorous — a good answer scores 70-85, an exceptional answer 86-100, a weak answer below 60.

{feedback_instruction}

Also provide:
- overall_score: weighted average 0-100
- overall_feedback: 3-4 sentence summary of the candidate's overall performance{"" if not is_annual else ""}
- strengths: 2-3 key strengths observed
- improvements: 2-3 specific areas to improve

Return ONLY valid JSON in this exact structure:
{{
  "overall_score": 75,
  "overall_feedback": "...",
  "strengths": ["...", "..."],
  "improvements": ["...", "..."],
  "questions": [
    {{
      "index": 0,
      "score": 80,
      "score_breakdown": {{"clarity": 80, "depth": 75, "relevance": 85, "communication": 80}}{''',
      "feedback": "...",
      "coaching_tip": "..."''' if is_annual else ''},
    }}
  ]
}}

Interview Q&A:
{qa_text}"""

    claude = get_claude()
    try:
        scoring_response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            messages=[{"role": "user", "content": scoring_prompt}]
        )
        raw = scoring_response.content[0].text.strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        result = json.loads(match.group() if match else raw)
    except Exception as e:
        logger.error(f"Scoring error: {e}")
        raise HTTPException(500, "Scoring failed. Please try again.")

    overall_score = int(result.get("overall_score", 0))
    now = datetime.now(timezone.utc).isoformat()

    # Save per-answer scores and breakdowns back to interview_answers
    questions = result.get("questions", [])
    for q in questions:
        idx = q.get("index")
        if idx is None:
            continue
        try:
            update_data = {
                "score": q.get("score"),
            }
            breakdown = q.get("score_breakdown")
            if breakdown:
                update_data["score_breakdown"] = json.dumps(breakdown)
            if is_annual:
                update_data["feedback"]     = q.get("feedback", "")
                update_data["coaching_tip"] = q.get("coaching_tip", "")
            supabase.table("interview_answers")                 .update(update_data)                 .eq("session_id", session_id)                 .eq("question_index", idx).execute()
        except Exception as e:
            logger.error(f"Answer score update error idx={idx}: {e}")

    # Compute overall score_breakdown as average across all questions
    avg_breakdown = {}
    if questions:
        dims = ["clarity", "depth", "relevance", "communication"]
        for dim in dims:
            vals = [
                q["score_breakdown"][dim]
                for q in questions
                if q.get("score_breakdown") and dim in q["score_breakdown"]
            ]
            if vals:
                avg_breakdown[dim] = round(sum(vals) / len(vals))

    # Update session to completed
    try:
        supabase.table("interview_sessions").update({
            "status":           "completed",
            "score":            overall_score,
            "score_breakdown":  json.dumps(avg_breakdown) if avg_breakdown else json.dumps(result),
            "completed_at":     now,
        }).eq("id", session_id).execute()
    except Exception as e:
        logger.error(f"Session update error: {e}")

    # Mint badge if score >= 60
    badge_icon, badge_label = badge_for_score(overall_score)
    badge_id = None
    if badge_icon:
        try:
            badge = supabase.table("badges").insert({
                "user_id":    user["id"],
                "session_id": session_id,
                "profession": profession,
                "score":      overall_score,
                "label":      badge_label,
                "icon":       badge_icon,
                "created_at": now,
            }).execute()
            badge_id = badge.data[0]["id"]
        except Exception as e:
            logger.error(f"Badge mint error: {e}")

    return {
        "session_id":    session_id,
        "overall_score": overall_score,
        "badge_icon":    badge_icon,
        "badge_label":   badge_label,
        "badge_id":      badge_id,
        "result":        result,
        "is_annual":     is_annual,
    }


# ---------------------------------------------------------------------------
# Abandon session
# ---------------------------------------------------------------------------

@router.post("/abandon/{session_id}")
async def abandon_session(session_id: str, request: Request):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401)
    supabase = get_supabase()
    try:
        supabase.table("interview_sessions").update({
            "status": "abandoned"
        }).eq("id", session_id).eq("user_id", user["id"]).execute()
        return {"status": "abandoned"}
    except Exception as e:
        raise HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# Result page
# ---------------------------------------------------------------------------

@router.get("/result/{session_id}")
async def result_page(request: Request, session_id: str):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login")

    supabase = get_supabase()
    try:
        sess = supabase.table("interview_sessions") \
            .select("*").eq("id", session_id) \
            .eq("user_id", user["id"]).limit(1).execute()
        if not sess.data:
            return RedirectResponse("/dashboard")
        session = sess.data[0]

        answers = supabase.table("interview_answers") \
            .select("*").eq("session_id", session_id) \
            .order("question_index").execute()

        badge = supabase.table("badges") \
            .select("*").eq("session_id", session_id) \
            .limit(1).execute()

        plan = "monthly"
        sub = supabase.table("jobseeker_subscriptions") \
            .select("plan").eq("user_id", user["id"]) \
            .eq("status", "active").limit(1).execute()
        if sub.data:
            plan = sub.data[0]["plan"]

        score_data = json.loads(session.get("score_breakdown") or "{}")

    except Exception as e:
        logger.error(f"Result fetch error: {e}")
        return RedirectResponse("/dashboard")

    return templates.TemplateResponse(
        request=request,
        name="interview/result.html",
        context={
            "user":       user,
            "session":    session,
            "answers":    answers.data or [],
            "badge":      badge.data[0] if badge.data else None,
            "score_data": score_data,
            "plan":       plan,
            "is_annual":  plan in ("js_annual", "annual"),
        }
    )


# ---------------------------------------------------------------------------
# Question preview (3 sample questions — all paid subscribers)
# ---------------------------------------------------------------------------

@router.get("/preview/{profession:path}")
async def preview_questions(request: Request, profession: str):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401)

    claude = get_claude()
    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{
                "role": "user",
                "content": f"Generate 3 sample interview questions for a {profession} role in India. Return ONLY a JSON array of 3 strings."
            }]
        )
        raw = response.content[0].text.strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        questions = json.loads(match.group() if match else raw)
        return {"profession": profession, "questions": questions[:3]}
    except Exception as e:
        raise HTTPException(500, "Could not generate preview questions.")


# ---------------------------------------------------------------------------
# Session page + data endpoint (for direct navigation / resume)
# ---------------------------------------------------------------------------

@router.get("/session/{session_id}")
async def session_page(request: Request, session_id: str):
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/auth/login")

    supabase = get_supabase()
    try:
        sess = supabase.table("interview_sessions") \
            .select("*").eq("id", session_id) \
            .eq("user_id", user["id"]).limit(1).execute()
        if not sess.data:
            return RedirectResponse("/dashboard")
        session = sess.data[0]
        if session["status"] == "completed":
            return RedirectResponse(f"/interview/result/{session_id}")
        if session["status"] == "abandoned":
            return RedirectResponse("/interview/start")
    except Exception as e:
        return RedirectResponse("/dashboard")

    return templates.TemplateResponse(
        request=request,
        name="interview/session.html",
        context={
            "session_id": session_id,
            "profession": session["profession"],
            "user": user,
        }
    )


@router.get("/session-data/{session_id}")
async def session_data(session_id: str, request: Request):
    """Returns questions + answered count for resume flow"""
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        raise HTTPException(401)

    supabase = get_supabase()
    try:
        sess = supabase.table("interview_sessions") \
            .select("questions").eq("id", session_id) \
            .eq("user_id", user["id"]).limit(1).execute()
        if not sess.data:
            raise HTTPException(404)
        questions = json.loads(sess.data[0]["questions"] or "[]")

        answered = supabase.table("interview_answers") \
            .select("question_index", count="exact") \
            .eq("session_id", session_id).execute()
        answered_count = answered.count or 0

        return {"questions": questions, "answered_count": answered_count}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))
