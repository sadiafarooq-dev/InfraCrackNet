# Shared routes -- used by any logged-in role
import asyncio
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user, hash_password, verify_password
from database import get_session, engine
from models.user import User
from models.report import Report
from models.audit_log import AuditLogEntry
from models.project import Project, ProjectInspector, ProjectEngineer
from time_utils import register_localtime
from asset_version import register_asset_version

router = APIRouter()
templates = Jinja2Templates(directory=["templates", "shared/templates"])
register_localtime(templates)
register_asset_version(templates)

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
        "body": "Watch your report move from Submitted to Reviewed right from your Home screen. You'll see the Engineer's decision the moment it's ready.",
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
        "body": "Open a report, click Run Analysis to see what the AI found, then record your own assessment: Reviewed, Resolved, or send it back Under Review.",
        "button": "Next",
        "next": 3,
    },
    3: {
        "icon": "camera",
        "title": "Track Follow-ups",
        "body": "Request a follow-up photo any time after a report is Reviewed or Resolved. Your Inspector gets notified and the before/after comparison shows up here once they submit it.",
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
def _profile_activity_context(user, session: Session) -> dict:
    """Builds the "recent work" part of the Profile page -- different per
    role, built entirely from data that already exists elsewhere in the
    app (nothing new added to the database for this): Inspector gets their
    5 most recently submitted reports, Engineer gets their 5 most recently
    reviewed reports, and both get the list of Projects they're assigned
    to. Admin instead gets their 5 most recent audit log actions, since
    Admin isn't personally assigned to Projects the way Inspectors and
    Engineers are. Shared between the GET and POST /profile handlers so
    this section looks the same whether you're just viewing the page or
    you just hit Save Changes -- see PROJECT_LOG.md section 108."""
    context = {"recent_reports": None, "my_projects": None, "recent_actions": None}

    if user.role == "Inspector":
        reports = session.exec(
            select(Report)
            .where(Report.inspector_id == user.id)
            .order_by(Report.created_at.desc())
        ).all()
        context["recent_reports"] = reports[:5]

        project_ids = session.exec(
            select(ProjectInspector.project_id).where(ProjectInspector.user_id == user.id)
        ).all()
        context["my_projects"] = (
            session.exec(select(Project).where(Project.id.in_(project_ids))).all()
            if project_ids else []
        )

    elif user.role == "Engineer":
        reports = session.exec(
            select(Report)
            .where(Report.reviewed_by_id == user.id)
            .order_by(Report.reviewed_at.desc())
        ).all()
        context["recent_reports"] = reports[:5]

        project_ids = session.exec(
            select(ProjectEngineer.project_id).where(ProjectEngineer.user_id == user.id)
        ).all()
        context["my_projects"] = (
            session.exec(select(Project).where(Project.id.in_(project_ids))).all()
            if project_ids else []
        )

    elif user.role == "Admin":
        actions = session.exec(
            select(AuditLogEntry)
            .where(AuditLogEntry.actor_name == user.name)
            .order_by(AuditLogEntry.created_at.desc())
        ).all()
        context["recent_actions"] = actions[:5]

    return context


@router.get("/profile")
def profile_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    context = {"user": user, "password_error": None, "password_saved": None}
    context.update(_profile_activity_context(user, session))
    return templates.TemplateResponse(request, "profile.html", context)


@router.post("/profile")
def profile_update(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    profile_photo: UploadFile = File(None),
    cover_photo: UploadFile = File(None),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    user.name = name
    user.email = email

    # Only replace a photo if they actually chose a new file this time --
    # an empty file input submits as a zero-byte UploadFile, not None, so
    # this checks the filename rather than truthiness of the upload.
    if profile_photo is not None and profile_photo.filename:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(profile_photo.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            shutil.copyfileobj(profile_photo.file, f)
        user.profile_photo_path = filename

    if cover_photo is not None and cover_photo.filename:
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(cover_photo.filename)[1]
        filename = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
            shutil.copyfileobj(cover_photo.file, f)
        user.cover_photo_path = filename

    session.add(user)
    session.commit()
    session.refresh(user)

    context = {"user": user, "saved": True, "password_error": None, "password_saved": None}
    context.update(_profile_activity_context(user, session))
    return templates.TemplateResponse(request, "profile.html", context)


# Change Password -- its own separate form and route (not bundled into the
# Save Changes above) so updating your name/email never risks also
# touching your password by accident, and vice versa. Re-added here at
# Sadia's explicit request on 2026-10-02, reversing the earlier removal
# from task #42 -- see PROJECT_LOG.md section 109 for that conversation.
MIN_PASSWORD_LENGTH = 8


@router.post("/profile/change-password")
def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not user:
        return RedirectResponse("/login", status_code=303)

    context = {"user": user, "password_error": None, "password_saved": None}
    context.update(_profile_activity_context(user, session))

    if not user.password_hash or not verify_password(current_password, user.password_hash):
        context["password_error"] = "Your current password is incorrect."
        return templates.TemplateResponse(request, "profile.html", context)

    if new_password != confirm_password:
        context["password_error"] = "The new passwords do not match."
        return templates.TemplateResponse(request, "profile.html", context)

    if len(new_password) < MIN_PASSWORD_LENGTH:
        context["password_error"] = f"New password must be at least {MIN_PASSWORD_LENGTH} characters."
        return templates.TemplateResponse(request, "profile.html", context)

    user.password_hash = hash_password(new_password)
    session.add(user)
    session.commit()

    context["password_saved"] = True
    return templates.TemplateResponse(request, "profile.html", context)


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
            "text": f"{a.actor_name} {a.action.lower()} · {a.details}",
            "actor": a.actor_name,
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
# Live notifications -- the server side of static/js/live-notifications.js.
#
# Two ways for the browser to hear about something new, both giving the
# exact same answer (the same real notification logic as the full page above):
#
#  1. GET /notifications/stream  -- a "live line". The browser opens it once
#     and the server keeps it open, looking at the database about once a
#     second and pushing a message the instant the list changes. That is what
#     makes the banner, chime and badge appear within about a second of, say,
#     an Engineer requesting a follow-up. (It is Server-Sent Events: built
#     into every browser and into FastAPI, no extra package to install.)
#     The line is deliberately closed every ~20 seconds and the browser
#     reconnects by itself within a moment; this keeps the server easy to
#     stop and restart, and it re-sends the full picture each time so nothing
#     can be missed in between.
#  2. GET /notifications/latest  -- a plain one-shot "what is the picture
#     right now?" answer. Used once when a page opens (so the badge is right
#     immediately) and as the backup if the live line ever cannot be used.
#
# Neither touches user.notifications_seen_at -- only opening the real
# Notifications page does, which is what clears the badge. The unread
# count ("unseen") is simply how many listed items are newer than that.
# ---------------------------------------------------------------------
STREAM_LIFETIME_SECONDS = 20
STREAM_CHECK_SECONDS = 1.0


def _notification_items(user, session: Session):
    if user.role == "Inspector":
        return _inspector_notifications(user, session)
    if user.role == "Engineer":
        return _engineer_notifications(user, session)
    if user.role == "Admin":
        # An Admin does not need a notification for their own actions.
        items = _admin_notifications(user, session)
        return [i for i in items if i.get("actor") != user.name]
    return []


# Times are stored as UTC with no zone marker. The trailing "Z" tells the
# browser they are UTC, so it does not mistake them for local time.
def _stamp(dt):
    return dt.isoformat(timespec="milliseconds") + "Z"


def _notification_payload(user, session: Session):
    # Taken BEFORE the list is built, so anything created while it is being
    # built is newer than "now" and is picked up on the next check.
    now = datetime.utcnow()
    items = _notification_items(user, session)
    seen = user.notifications_seen_at
    unseen = sum(1 for i in items if seen is None or i["time"] > seen)
    return {
        "now": _stamp(now),
        "user_id": user.id,
        "seen_at": _stamp(seen) if seen else None,
        "unseen": unseen,
        "items": [
            {"icon": i["icon"], "text": i["text"], "href": i["href"], "time": _stamp(i["time"])}
            for i in items
        ],
    }


@router.get("/notifications/latest")
def notifications_latest(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not user:
        return JSONResponse({"now": None, "user_id": None, "seen_at": None, "unseen": 0, "items": []})
    return JSONResponse(_notification_payload(user, session), headers={"Cache-Control": "no-store"})


def _stream_snapshot(user_id):
    """One look at the database for the live line. Runs in a worker thread
    (the database library is not async) with its own short-lived session."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return None
        return _notification_payload(user, session)


@router.get("/notifications/stream")
async def notifications_stream(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return JSONResponse({"error": "not signed in"}, status_code=401)

    async def live_line():
        # Tells the browser to reconnect quickly after the planned close.
        yield "retry: 500\n\n"
        waited = 0.0
        last_sig = None
        quiet = 0
        while waited < STREAM_LIFETIME_SECONDS:
            if await request.is_disconnected():
                return
            payload = await run_in_threadpool(_stream_snapshot, user_id)
            if payload is None:
                return
            # "now" changes every look, so it is left out of the comparison.
            sig = json.dumps([payload["unseen"], payload["seen_at"], payload["items"]], sort_keys=True)
            if sig != last_sig:
                last_sig = sig
                quiet = 0
                yield "data: " + json.dumps(payload) + "\n\n"
            else:
                quiet += 1
                if quiet >= 10:
                    quiet = 0
                    yield ": still here\n\n"
            await asyncio.sleep(STREAM_CHECK_SECONDS)
            waited += STREAM_CHECK_SECONDS

    return StreamingResponse(
        live_line(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
