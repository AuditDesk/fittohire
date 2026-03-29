"""
FitToHire — Certificate Routes
/certificate/{session_id} — download PDF certificate for a completed session
Available to all subscribers (monthly and annual).
"""

import io
import os
import logging
from pathlib import Path
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import Response, RedirectResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["certificate"])


def get_supabase():
    from supabase import create_client
    return create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY")
    )


def generate_certificate_pdf(
    candidate_name: str,
    profession: str,
    score: int,
    session_date: str,
    session_id: str,
    badge_label: str = None,
    question_count: int = 10,
) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    buffer = io.BytesIO()
    w, h = A4

    c = canvas.Canvas(buffer, pagesize=A4)

    # Brand colors
    RED    = colors.HexColor('#DC2626')
    YELLOW = colors.HexColor('#EAB308')
    GREEN  = colors.HexColor('#16A34A')
    DARK   = colors.HexColor('#0D0D0D')
    MUTED  = colors.HexColor('#6B7280')
    CREAM  = colors.HexColor('#F9F8F4')
    BORDER = colors.HexColor('#E8E8E4')

    # Score color + badge
    if score >= 90:
        score_color = colors.HexColor('#D97706')
        if not badge_label: badge_label = 'Gold Scorer'
    elif score >= 75:
        score_color = colors.HexColor('#6B7280')
        if not badge_label: badge_label = 'Silver Scorer'
    elif score >= 60:
        score_color = colors.HexColor('#92400E')
        if not badge_label: badge_label = 'Bronze Scorer'
    else:
        score_color = DARK
        if not badge_label: badge_label = 'Completed'

    # Background
    c.setFillColor(colors.white)
    c.rect(0, 0, w, h, fill=1, stroke=0)

    # Top brand stripe
    c.setFillColor(RED);    c.rect(0, h-8*mm, w/3, 8*mm, fill=1, stroke=0)
    c.setFillColor(YELLOW); c.rect(w/3, h-8*mm, w/3, 8*mm, fill=1, stroke=0)
    c.setFillColor(GREEN);  c.rect(2*w/3, h-8*mm, w/3, 8*mm, fill=1, stroke=0)

    # Outer border
    c.setStrokeColor(BORDER)
    c.setLineWidth(1)
    c.rect(15*mm, 15*mm, w-30*mm, h-30*mm, fill=0, stroke=1)

    # Logo
    logo_y = h - 45*mm
    c.setFont('Helvetica-Bold', 28)
    x_start = w/2 - 44*mm
    c.setFillColor(RED);    c.drawString(x_start,       logo_y, 'Fit')
    c.setFillColor(YELLOW); c.drawString(x_start+22*mm, logo_y, 'To')
    c.setFillColor(GREEN);  c.drawString(x_start+36*mm, logo_y, 'Hire')

    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    c.drawCentredString(w/2, logo_y - 8*mm, 'AI-Powered Interview Certification')

    # Divider
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(30*mm, logo_y - 14*mm, w-30*mm, logo_y - 14*mm)

    # Heading
    cert_y = logo_y - 28*mm
    c.setFont('Helvetica', 11)
    c.setFillColor(MUTED)
    c.drawCentredString(w/2, cert_y, 'CERTIFICATE OF INTERVIEW PERFORMANCE')

    # Candidate name
    name_y = cert_y - 14*mm
    c.setFont('Helvetica-Bold', 26)
    c.setFillColor(DARK)
    c.drawCentredString(w/2, name_y, candidate_name)

    c.setFont('Helvetica', 12)
    c.setFillColor(MUTED)
    c.drawCentredString(w/2, name_y - 10*mm, 'has successfully completed an AI-scored mock interview for')

    # Profession
    c.setFont('Helvetica-Bold', 18)
    c.setFillColor(DARK)
    c.drawCentredString(w/2, name_y - 22*mm, profession)

    # Score circle
    score_y = name_y - 48*mm
    cx, cy, r = w/2, score_y, 22*mm
    c.setFillColor(CREAM)
    c.setStrokeColor(score_color)
    c.setLineWidth(3)
    c.circle(cx, cy, r, fill=1, stroke=1)
    c.setFont('Helvetica-Bold', 30)
    c.setFillColor(score_color)
    c.drawCentredString(cx, cy - 5*mm, f'{score}%')
    c.setFont('Helvetica', 8)
    c.setFillColor(MUTED)
    c.drawCentredString(cx, cy - 12*mm, 'SCORE')

    # Badge pill
    badge_y = score_y - 32*mm
    pill_w, pill_h = 50*mm, 9*mm
    pill_x = w/2 - pill_w/2
    c.setFillColor(CREAM)
    c.setStrokeColor(score_color)
    c.setLineWidth(1)
    c.roundRect(pill_x, badge_y - pill_h/2, pill_w, pill_h, 4*mm, fill=1, stroke=1)
    c.setFont('Helvetica-Bold', 10)
    c.setFillColor(score_color)
    c.drawCentredString(w/2, badge_y - 2*mm, badge_label)

    # Details row
    detail_y = badge_y - 22*mm
    details = [
        ('Date', session_date),
        ('Questions', str(question_count)),
        ('Verified by', 'FitToHire AI'),
    ]
    col_w = (w - 60*mm) / len(details)
    for i, (label, value) in enumerate(details):
        dx = 30*mm + i * col_w + col_w/2
        c.setFont('Helvetica', 8)
        c.setFillColor(MUTED)
        c.drawCentredString(dx, detail_y, label.upper())
        c.setFont('Helvetica-Bold', 11)
        c.setFillColor(DARK)
        c.drawCentredString(dx, detail_y - 6*mm, value)

    # Trust statement
    trust_y = detail_y - 24*mm
    c.setFont('Helvetica', 9)
    c.setFillColor(MUTED)
    trust = (
        'This certificate is generated from actual interview responses '
        'scored by AI on clarity, depth, relevance and communication.'
    )
    words = trust.split()
    line, lines = [], []
    for word in words:
        line.append(word)
        if len(' '.join(line)) > 80:
            lines.append(' '.join(line[:-1]))
            line = [word]
    if line:
        lines.append(' '.join(line))
    for i, ln in enumerate(lines):
        c.drawCentredString(w/2, trust_y - i*5*mm, ln)

    # Footer
    footer_y = 25*mm
    c.setStrokeColor(BORDER)
    c.setLineWidth(0.5)
    c.line(30*mm, footer_y + 8*mm, w-30*mm, footer_y + 8*mm)
    c.setFont('Helvetica', 8)
    c.setFillColor(MUTED)
    short_id = session_id[:16].upper() if len(session_id) >= 16 else session_id.upper()
    c.drawString(30*mm, footer_y + 3*mm, f'Certificate ID: {short_id}')
    c.drawString(30*mm, footer_y - 2*mm, 'Verify at: fittohire.in/verify')
    c.drawRightString(w-30*mm, footer_y + 3*mm, 'fittohire.in')
    c.drawRightString(w-30*mm, footer_y - 2*mm, 'auditdesk.hq@gmail.com')

    c.save()
    return buffer.getvalue()


@router.get("/certificate/{session_id}")
async def download_certificate(session_id: str, request: Request):
    """
    Generate and download a PDF certificate for a completed session.
    Available to all active subscribers (monthly and annual).
    """
    from app.routers.auth import get_current_user
    user = await get_current_user(request)
    if not user:
        return RedirectResponse(f"/auth/login?next=/certificate/{session_id}")

    supabase = get_supabase()
    user_id = user["id"]

    # Check active subscription
    try:
        sub = supabase.table("jobseeker_subscriptions") \
            .select("plan, status") \
            .eq("user_id", user_id) \
            .eq("status", "active") \
            .limit(1).execute()
        if not sub.data:
            raise HTTPException(403, "Active subscription required to download certificates.")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Subscription check error: {e}")

    # Get session — must belong to this user
    try:
        sess = supabase.table("interview_sessions") \
            .select("*") \
            .eq("id", session_id) \
            .eq("user_id", user_id) \
            .eq("status", "completed") \
            .limit(1).execute()
        if not sess.data:
            raise HTTPException(404, "Session not found or not completed.")
        session = sess.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session fetch error: {e}")
        raise HTTPException(500, "Could not retrieve session.")

    # Get candidate name from profile/users
    candidate_name = "Candidate"
    try:
        profile = supabase.table("jobseeker_profiles") \
            .select("full_name").eq("user_id", user_id).limit(1).execute()
        if profile.data and profile.data[0].get("full_name"):
            candidate_name = profile.data[0]["full_name"]
        else:
            u = supabase.table("users") \
                .select("full_name, email").eq("id", user_id).limit(1).execute()
            if u.data:
                candidate_name = u.data[0].get("full_name") or \
                    u.data[0].get("email", "").split("@")[0].title()
    except Exception as e:
        logger.error(f"Name fetch error: {e}")

    # Format session date
    session_date = "2026"
    try:
        dt = datetime.fromisoformat(
            session["completed_at"].replace("Z", "+00:00")
        )
        session_date = dt.strftime("%d %b %Y")
    except Exception:
        pass

    # Generate PDF
    try:
        pdf_bytes = generate_certificate_pdf(
            candidate_name=candidate_name,
            profession=session.get("profession", "General"),
            score=session.get("score", 0),
            session_date=session_date,
            session_id=session_id,
            badge_label=session.get("badge_label"),
            question_count=session.get("question_count", 10),
        )
    except Exception as e:
        logger.error(f"PDF generation error: {e}")
        raise HTTPException(500, "Could not generate certificate. Please try again.")

    # Clean filename
    prof_clean = session.get("profession", "Interview").replace(" ", "_").replace("/", "-")
    filename = f"FitToHire_Certificate_{prof_clean}_{session_date.replace(' ', '_')}.pdf"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        }
    )
