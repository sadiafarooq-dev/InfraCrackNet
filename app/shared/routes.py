# Shared routes -- used by any logged-in role
import os
import shutil
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user, hash_password
from database import get_session
from models.report import Report
from models.audit_log import AuditLogEntry
from time_utils import register_localtime

router = APIRouter()
templates = Jinja2Templates(directory=["templates", "shared/templates"])
register_localtime(templates)

# Same shared /uploads folder every other photo (report photos, follow-up
# photos) already goes into -- see main.py's app.mount("/uploads", ...).
UPLOAD_DIR = os.path.join("..", "uploads")


# ---------------------------------------------------------------------
# Welcome tour -- one shared 3-screen onboarding, shown once right after
# EITHER an Inspector's or an Engineer's very first login (see
# auth/routes.py), then never again once they hit Skip or Get Started.
# Previously this only existed for the Inspector role (Figma only designed
# it for Inspector) -- now generalized so Engineer gets the same idea, with
# copy that fits their own job instead of the Inspector's. Admin doesn't get
# one, same as before.
# ---------------------------------------------------------------------
INSPECTOR_ONBOARDING_STEPS = {
    1: {
        "icon": "check",
        "title": "Welcome to InfraCrackNet",
        "body": "AI checks every photo you submit for cracks, and a human Engineer always reviews the finding before anything is acted on.",
        "button": "Next",
        "next": 2,
    },
    2: {
        "icon": "camera",
        "title": "Capture & Submit",
        "body": "Photograph or record video of the crack on site, mark its location, and add a few quick notes about the road or building's condition.",
        "button": "Next",
        "next": 3,
    },
    3: {
        "icon": "clipboard",
        "title": "Track Your Reports",
        "body": "Watch your report move from Submitted to Reviewed right from your Home screen — you'll see the Engineer's decision the moment it's ready.",
        "button": "Get Started",
        "next": None,
    },
}

ENGINEER_ONBOARDING_STEPS = {
    1: {
        "icon": "check",
        "title": "Welcome to InfraCrackNet",
        "body": "AI checks every submitted photo for cracks, and every finding still comes to you for a real, human decision before anything is acted on.",
        "button": "Next",
        "next": 2,
    },
    2: {
        "icon": "clipboard",
        "title": "Review & Decide",
        "body": "Open a report, click Run Analysis to see what the AI found, then record your own assessment — Reviewed, Resolved, or send it back Under Review.",
        "button": "Next",
        "next": 3,
    },
    3: {
        "icon": "camera",
        "title": "Track Follow-ups",
        "body": "Request a follow-up photo any time after a report is Reviewed or Resolved — your Inspector gets notified and the before/after comparison shows up here once they submit it.",
        "button": "Get Started",
        "next": None,
    },
}


def _onboarding_steps_for(user):
    if user.role == "Engineer":
        return ENGINEER_ONBOARDING_STEPS
    return INSPECTOR_ONBOARDING_STEPS


def _dashboard_url_for(user):
    if user.role == "Engineer":
        return "/engineer/dashboard"
    if user.role == "Admin":
        return "/admin/dashboard"
    return "/inspector/dashboard"


@router.get("/onboarding/finish")
def onboarding_finish(
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Registered ABOVE "/onboarding/{step}" on purpose: routes are checked
    # in the order they're written, and a plain {step} pattern would
    # otherwise catch "finish" first (reading it as a step number) before
    # this more specific address ever gets a chance to match.
    if not user or user.role not in ("Inspector", "Engineer"):
        return RedirectResponse("/login", status_code=303)
    user.has_seen_onboarding = True
    session.add(user)
    session.commit()
    return RedirectResponse(_dashboard_url_for(user), status_code=303)


@router.get("/onboarding/{step}")
def onboarding_page(step: int, request: Request, user=Depends(get_current_user)):
    if not user or user.role not in ("Inspector", "Engineer"):
        return RedirectResponse("/login", status_code=303)
    steps = _onboarding_steps_for(user)
    if step not in steps:
        return RedirectResponse(_dashboard_url_for(user), status_code=303)
    return templates.TemplateResponse(
        request, "onboarding.html", {"user": user, "step": step, "content": steps[step]}
    )


# Profile -- one shared page for all 3 roles (Inspector/Engineer/Admin all
# see the same Full Name / Employee Code / Email / photo fields; Figma's
# own Admin variant is a little sparser than the other two, but this uses
# the fuller, editable version everywhere for consistency, same kind of
# judgment call as the Inspector/Engineer sidebars -- see PROJECT_LOG.md).
@router.get("/profile")
def profile_page(request: Request, user=Depends(get_current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "profile.html", {"user": user})


@router.post("/profile")
def profile_update(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    profile_photo: UploadFile = File(None),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    user.name = name
    user.email = email

    # Only replace the photo if they actually chose a new file this time --
    # an empty file input submits as a zero-byte UploadFile, not None, so
    # this checks the filename rather than truthiness of profile_photo.
    if profile_photo is not None and profile_photo.filename:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(profile_photo.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            shutil.copyfileobj(profile_photo.file, f)
        user.profile_photo_path = filename

    session.add(user)
    session.commit()
    session.refresh(user)

    return templates.TemplateResponse(request, "profile.html", {"user": user, "saved": True})


# ---------------------------------------------------------------------
# Notifications -- one real page per role, replacing Figma's "Notification
# Dropdown" (68:18). Since this whole app avoids JavaScript on purpose, a
# JS-powered dropdown isn't possible -- this is the same idea (a short,
# real, most-recent-first list) as a real full page instead, reached from
# a plain bell icon in every sidebar/navbar. One disclosed judgment call:
# there's no live unread-count badge on the bell, since that would mean
# computing this same list on every single page load across the whole
# app just to show a number -- opening the page always shows the true,
# current list either way.
# ---------------------------------------------------------------------
NOTIFICATION_OVERDUE_DAYS = 3


def _inspector_notifications(user, session: Session):
    reports = session.exec(
        select(Report).where(Report.inspector_id == user.id)
    ).all()
    items = []
    for r in reports:
        if r.status in ("Reviewed", "Resolved") and r.reviewed_at:
            items.append({
                "icon": "check" if r.status == "Resolved" else "clipboard",
                "text": f"Your report RPT-{r.id:04d} ({r.location}) was marked {r.status}",
                "time": r.reviewed_at,
                "href": f"/inspector/reports/{r.id}",
            })
        if r.follow_up_decision == "Escalated":
            items.append({
                "icon": "warning",
                "text": f"Your report RPT-{r.id:04d} ({r.location}) was escalated after a follow-up scan",
                "time": r.follow_up_created_at or r.created_at,
                "href": f"/inspector/reports/{r.id}",
            })
        if r.follow_up_requested_at and not r.follow_up_photo_path:
            items.append({
                "icon": "clipboard",
                "text": f"Your Engineer requested a follow-up photo for RPT-{r.id:04d} ({r.location})",
                "time": r.follow_up_requested_at,
                "href": f"/inspector/reports/{r.id}",
            })
    items.sort(key=lambda i: i["time"], reverse=True)
    return items[:15]


def _engineer_notifications(user, session: Session):
    now = datetime.utcnow()
    pending = session.exec(
        select(Report).where(Report.status.in_(["Submitted", "Under Review"]))
    ).all()
    items = []
    for r in pending:
        overdue = (now - r.created_at).days >= NOTIFICATION_OVERDUE_DAYS
        items.append({
            "icon": "warning" if overdue else "clipboard",
            "text": (
                f"RPT-{r.id:04d} ({r.location}) is overdue for review"
                if overdue else
                f"New report RPT-{r.id:04d} ({r.location}) is waiting for review"
            ),
            "time": r.created_at,
            "href": f"/engineer/reports/{r.id}",
        })
    escalated = session.exec(
        select(Report).where(Report.follow_up_decision == "Escalated")
    ).all()
    for r in escalated:
        items.append({
            "icon": "warning",
            "text": f"Follow-up scan on RPT-{r.id:04d} ({r.location}) was escalated",
            "time": r.follow_up_created_at or r.created_at,
            "href": f"/engineer/reports/{r.id}/follow-up",
        })
    fulfilled_follow_ups = session.exec(
        select(Report).where(Report.follow_up_created_at.is_not(None))
    ).all()
    for r in fulfilled_follow_ups:
        items.append({
            "icon": "check",
            "text": f"The Inspector submitted the follow-up photo you requested for RPT-{r.id:04d} ({r.location})",
            "time": r.follow_up_created_at,
            "href": f"/engineer/reports/{r.id}/follow-up",
        })
    items.sort(key=lambda i: i["time"], reverse=True)
    return items[:15]


def _admin_notifications(user, session: Session):
    items = []
    recent_actions = session.exec(
        select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc()).limit(10)
    ).all()
    for a in recent_actions:
        items.append({
            "icon": "audit",
            "text": f"{a.actor_name} {a.action.lower()} — {a.details}",
            "time": a.created_at,
            "href": "/admin/audit-log",
        })
    items.sort(key=lambda i: i["time"], reverse=True)
    return items[:15]


@router.get("/notifications")
def notifications_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not user:
        return RedirectResponse("/login", status_code=303)

    if user.role == "Inspector":
        items = _inspector_notifications(user, session)
    elif user.role == "Engineer":
        items = _engineer_notifications(user, session)
    elif user.role == "Admin":
        items = _admin_notifications(user, session)
    else:
        items = []

    user.notifications_seen_at = datetime.utcnow()
    session.add(user)
    session.commit()

    return templates.TemplateResponse(request, "notifications.html", {"user": user, "items": items})


# ---------------------------------------------------------------------
# Live notification pop-ups -- a small JSON "anything new?" endpoint for
# static/js/live-notifications.js to poll every ~25 seconds (see that file
# for the plain-language explanation). This is the SAME real notification
# logic as the full page above -- just handed back as JSON instead of
# HTML, and it deliberately does NOT touch user.notifications_seen_at, so
# it never interferes with what the real Notifications page counts as
# "seen". This is the second and last place this app uses JavaScript.
# ---------------------------------------------------------------------
@router.get("/notifications/latest")
def notifications_latest(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not user:
        return JSONResponse([])

    if user.role == "Inspector":
        items = _inspector_notifications(user, session)
    elif user.role == "Engineer":
        items = _engineer_notifications(user, session)
    elif user.role == "Admin":
        items = _admin_notifications(user, session)
    else:
        items = []

    return JSONResponse([
        {"icon": i["icon"], "text": i["text"], "href": i["href"], "time": i["time"].isoformat()}
        for i in items
    ])
