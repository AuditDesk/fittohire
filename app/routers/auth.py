"""
FitToHire — Auth Routes
Magic link (OTP) only. No passwords.
Single /login page with role selector (job_seeker | employer).

Flow:
  1. POST /auth/send-otp   → user enters email + role → Supabase sends magic link
  2. GET  /auth/callback   → Supabase redirects here after link click → set session cookie
  3. GET  /auth/me         → returns current user from cookie (used by all protected routes)
  4. POST /auth/logout     → clears session cookie
"""

import os
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, EmailStr
from supabase import create_client, Client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# Absolute path — works regardless of where uvicorn is launched from
BASE_DIR = Path(__file__).resolve().parent.parent  # → /app/app
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
APP_URL = os.getenv("APP_URL", "https://fittohire.in")


def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SendOTPRequest(BaseModel):
    email: EmailStr
    role: str  # "job_seeker" | "employer"


# ---------------------------------------------------------------------------
# Dependency — get current user from session cookie
# Returns None if not logged in (use for optional auth)
# Raises 401 if not logged in (use for required auth)
# ---------------------------------------------------------------------------

async def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get("sb_access_token")
    if not token:
        return None
    try:
        supabase = get_supabase()
        user = supabase.auth.get_user(token)
        if user and user.user:
            return {
                "id":    user.user.id,
                "email": user.user.email,
                "role":  user.user.user_metadata.get("role", "job_seeker"),
                "name":  user.user.user_metadata.get("full_name", ""),
            }
    except Exception as e:
        logger.warning(f"Token validation failed: {e}")
    return None


async def require_user(request: Request) -> dict:
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_job_seeker(request: Request) -> dict:
    user = await require_user(request)
    if user["role"] != "job_seeker":
        raise HTTPException(status_code=403, detail="Job seeker access only")
    return user


async def require_employer(request: Request) -> dict:
    user = await require_user(request)
    if user["role"] != "employer":
        raise HTTPException(status_code=403, detail="Employer access only")
    return user


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/dashboard"):
    """
    Single login/signup page.
    User picks role (job seeker / employer) then enters email.
    Supabase sends a magic link — no password needed.
    """
    user = await get_current_user(request)
    if user:
        # Already logged in — redirect to correct dashboard
        return RedirectResponse(
            "/employer/dashboard" if user["role"] == "employer" else "/dashboard"
        )
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"next": next},
    )


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@router.post("/send-otp")
async def send_otp(body: SendOTPRequest, request: Request):
    """
    Send magic link to user's email.
    Role is stored in Supabase user_metadata so we know
    if they're a job seeker or employer after they click the link.
    """
    if body.role not in ("job_seeker", "employer"):
        raise HTTPException(400, "Role must be job_seeker or employer.")

    supabase = get_supabase()

    # Redirect URL after magic link click
    redirect_url = f"{APP_URL}/auth/callback?role={body.role}"

    try:
        supabase.auth.sign_in_with_otp({
            "email": body.email,
            "options": {
                "email_redirect_to": redirect_url,
                "data": {
                    "role": body.role,
                }
            }
        })
        return {"status": "sent", "email": body.email}

    except Exception as e:
        logger.error(f"OTP send failed for {body.email}: {e}")
        raise HTTPException(500, "Could not send magic link. Please try again.")


@router.get("/callback")
async def auth_callback(
    request: Request,
    response: Response,
    role: str = "job_seeker",
):
    """
    Supabase redirects here after user clicks magic link.
    Supabase appends #access_token=... as a URL fragment.
    Fragments are not sent to the server — we handle token
    extraction in the frontend JS and POST it back via /auth/set-session.
    This route just serves the callback HTML page.
    """
    return templates.TemplateResponse(
        request=request,
        name="auth/callback.html",
        context={"role": role},
    )



def send_welcome_email(email: str, role: str, supabase):
    """Send welcome email on first login using Supabase Auth email."""
    try:
        # Load the correct template
        template_dir = Path(__file__).resolve().parent.parent / "templates" / "emails"
        env = Environment(loader=FileSystemLoader(str(template_dir)))

        if role == "employer":
            template = env.get_template("welcome_employer.html")
            subject = "Welcome to FitToHire — Find candidates who have already proved themselves"
        else:
            template = env.get_template("welcome_jobseeker.html")
            subject = "Welcome to FitToHire — Let's get you hired"

        html_content = template.render()

        # Send via Gmail SMTP
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        gmail_user = os.getenv("GMAIL_USER", "auditdesk.hq@gmail.com")
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD")  # Gmail App Password from Railway env

        if not gmail_pass:
            logger.warning("GMAIL_APP_PASSWORD not set — welcome email skipped")
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = f"FitToHire <{gmail_user}>"
        msg["To"]      = email
        msg.attach(MIMEText(html_content, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, email, msg.as_string())

        logger.info(f"Welcome email sent to {email} (role={role})")

    except Exception as e:
        # Never block login because of email failure
        logger.error(f"Welcome email error for {email}: {e}")


@router.post("/set-session")
async def set_session(request: Request, response: Response, background_tasks: BackgroundTasks = None):
    """
    Frontend extracts access_token + refresh_token from URL fragment
    and POSTs them here. We set them as HttpOnly cookies.
    """
    body = await request.json()
    access_token  = body.get("access_token")
    refresh_token = body.get("refresh_token")
    role          = body.get("role", "job_seeker")

    if not access_token:
        raise HTTPException(400, "No access token provided.")

    # Validate token with Supabase
    try:
        supabase = get_supabase()
        user_resp = supabase.auth.get_user(access_token)
        if not user_resp or not user_resp.user:
            raise HTTPException(401, "Invalid token.")

        user_id = user_resp.user.id

        # Check if this is a first login
        existing = supabase.table("users")             .select("id, role")             .eq("id", user_id)             .limit(1).execute()
        is_first_login = not existing.data

        # Upsert user profile into our users table
        supabase.table("users").upsert({
            "id":    user_id,
            "email": user_resp.user.email,
            "role":  role,
        }, on_conflict="id").execute()

        # Send welcome email on first login (non-blocking)
        if is_first_login and background_tasks:
            background_tasks.add_task(
                send_welcome_email, user_resp.user.email, role, supabase
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session set failed: {e}")
        raise HTTPException(500, "Session setup failed.")

    # Set HttpOnly cookies — JS cannot read these (XSS protection)
    is_prod = os.getenv("APP_ENV", "production") == "production"

    response.set_cookie(
        key="sb_access_token",
        value=access_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=3600,          # 1 hour
        path="/",
    )
    response.set_cookie(
        key="sb_refresh_token",
        value=refresh_token,
        httponly=True,
        secure=is_prod,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,  # 30 days
        path="/",
    )

    # Redirect destination based on role
    redirect = "/employer/dashboard" if role == "employer" else "/dashboard"
    return {"status": "ok", "redirect": redirect, "user_id": user_id}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("sb_access_token", path="/")
    response.delete_cookie("sb_refresh_token", path="/")
    return RedirectResponse("/", status_code=302)


@router.get("/logout")
async def logout_get(response: Response):
    response.delete_cookie("sb_access_token", path="/")
    response.delete_cookie("sb_refresh_token", path="/")
    return RedirectResponse("/", status_code=302)
