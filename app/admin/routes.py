# Admin routes
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user, generate_employee_code, verify_password
from database import get_session
from models.user import User
from models.report import Report
from models.audit_log import AuditLogEntry, log_action
from models.message import Message
from models.app_settings import get_settings, generate_registration_code
from time_utils import register_localtime

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=["templates", "admin/templates"])
register_localtime(templates)


def require_admin(user):
    return user and user.role == "Admin"


# Admin's own Sign In page 
@router.get("/login")
def admin_login_page(request: Request):
    return templates.TemplateResponse(request, "admin_login.html", {})


@router.post("/login")
def admin_login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.email == email)).first()

    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "admin_login.html", {"error": "Wrong email or password."}
        )

    request.session["user_id"] = user.id
    request.session["role"] = user.role

   
    if user.role == "Inspector":
        if not user.has_seen_onboarding:
            return RedirectResponse("/onboarding/1", status_code=303)
        return RedirectResponse("/inspector/dashboard", status_code=303)
    if user.role == "Engineer":
        if not user.has_seen_onboarding:
            return RedirectResponse("/onboarding/1", status_code=303)
        return RedirectResponse("/engineer/dashboard", status_code=303)
    return RedirectResponse("/admin/dashboard", status_code=303)


def _severity_stats(reports):
    """Real Low/Medium/High counts for the Overview donut -- only among
    reports that have actually been analyzed AND where Model 3 found a real
    crack shape. Reports with no analysis run yet, or where Model 3's result
    was "No crack shape detected", are left out of the percentages entirely
    rather than being folded into any bucket. Returns None when there's
    nothing analyzed yet, so the template can fall back to the old
    not-yet-available message."""
    counts = {"Low": 0, "Medium": 0, "High": 0}
    for r in reports:
        if r.analysis_run_at and r.model3_severity in counts:
            counts[r.model3_severity] += 1
    total = sum(counts.values())
    if not total:
        return None
    pct = {k: round(v / total * 100) for k, v in counts.items()}
    stop1 = pct["Low"]
    stop2 = stop1 + pct["Medium"]
    return {
        "total": total,
        "low": {"count": counts["Low"], "pct": pct["Low"]},
        "medium": {"count": counts["Medium"], "pct": pct["Medium"]},
        "high": {"count": counts["High"], "pct": pct["High"]},
        "stop1": stop1,
        "stop2": stop2,
    }


# Overview
@router.get("/dashboard")
def overview(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(select(Report)).all()
    users = session.exec(select(User)).all()

    week_ago = datetime.utcnow() - timedelta(days=7)
    total_reports = len(reports)
    resolved = len([r for r in reports if r.status == "Resolved"])

    stats = {
        "total_reports": total_reports,
        "new_this_week": len([r for r in reports if r.created_at >= week_ago]),
        "pending_review": len([r for r in reports if r.status in ("Submitted", "Under Review")]),
        "resolved": resolved,
        "resolution_rate": round((resolved / total_reports) * 100) if total_reports else 0,
        "active_users": len([u for u in users if u.role in ("Inspector", "Engineer") and u.status == "Active"]),
    }

    recent_activity = session.exec(
        select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc()).limit(5)
    ).all()

    severity_stats = _severity_stats(reports)

    return templates.TemplateResponse(
        request,
        "overview.html",
        {"user": user, "stats": stats, "recent_activity": recent_activity, "severity_stats": severity_stats},
    )


# Trends
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _month_buckets(reports, months_back):
    """Real month-by-month counts for the last `months_back` months
    (including this one), oldest first."""
    now = datetime.utcnow()
    buckets = []
    for i in range(months_back - 1, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            m += 12
            y -= 1
        buckets.append({"year": y, "month": m, "label": MONTH_ABBR[m], "count": 0})
    index = {(b["year"], b["month"]): b for b in buckets}
    for r in reports:
        key = (r.created_at.year, r.created_at.month)
        if key in index:
            index[key]["count"] += 1
    return buckets


def _week_buckets(reports, weeks_back=4):
    """Real week-by-week counts for the last `weeks_back` 7-day windows,
    oldest first -- used for the "This Month" filter."""
    now = datetime.utcnow()
    buckets = []
    for i in range(weeks_back - 1, -1, -1):
        end = now - timedelta(days=7 * i)
        start = end - timedelta(days=7)
        buckets.append({"start": start, "end": end, "label": f"Wk {weeks_back - i}", "count": 0})
    for r in reports:
        for b in buckets:
            if b["start"] <= r.created_at < b["end"]:
                b["count"] += 1
                break
    return buckets


@router.get("/trends")
def trends(
    request: Request,
    range: str = "quarter",
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    if range not in ("month", "quarter", "year"):
        range = "quarter"

    reports = session.exec(select(Report)).all()

  
    if range == "month":
        buckets = _week_buckets(reports)
    elif range == "year":
        buckets = _month_buckets(reports, 12)
    else:
        buckets = _month_buckets(reports, 3)

    max_count = max([b["count"] for b in buckets], default=0) or 1
    for b in buckets:
        b["height_pct"] = round((b["count"] / max_count) * 100)

   
    location_counts = defaultdict(int)
    for r in reports:
        location_counts[r.location] += 1
    top_locations = sorted(location_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    max_location = top_locations[0][1] if top_locations else 1
    top_locations = [
        {"location": loc, "count": count, "width_pct": round((count / max_location) * 100)}
        for loc, count in top_locations
    ]

    status_counts = {"Submitted": 0, "Under Review": 0, "Reviewed": 0, "Resolved": 0}
    for r in reports:
        if r.status in status_counts:
            status_counts[r.status] += 1
    total = sum(status_counts.values()) or 1
    status_breakdown = [
        {"label": label, "count": count, "pct": round((count / total) * 100), "slug": label.lower().replace(" ", "-")}
        for label, count in status_counts.items()
    ]

    return templates.TemplateResponse(
        request,
        "trends.html",
        {
            "user": user,
            "range": range,
            "buckets": buckets,
            "top_locations": top_locations,
            "status_breakdown": status_breakdown,
        },
    )


# User management
@router.get("/users")
def user_management(
    request: Request,
    created: Optional[str] = None,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    users = session.exec(select(User).order_by(User.name)).all()
    return templates.TemplateResponse(
        request, "user_management.html", {"user": user, "users": users, "created": created}
    )


@router.get("/users/new")
def new_user_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    settings = get_settings(session)
    return templates.TemplateResponse(
        request, "new_user.html", {"user": user, "registration_code": settings.registration_code}
    )


@router.post("/users/new")
def new_user_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    employee_code = generate_employee_code(role, session)
    new_user = User(name=name, email=email, role=role, employee_code=employee_code)
    session.add(new_user)
    log_action(
        session, actor_name=user.name, action="Created Account",
        details=f"Created {role.lower()} account for {name} ({employee_code})",
    )
    session.commit()

    return RedirectResponse(f"/admin/users?created={employee_code}", status_code=303)


@router.post("/settings/registration-code/regenerate")
def regenerate_registration_code(
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    settings = get_settings(session)
    settings.registration_code = generate_registration_code()
    session.add(settings)
    log_action(session, actor_name=user.name, action="Regenerated Registration Code",
               details="Generated a new self-sign-up registration code")
    session.commit()

    return RedirectResponse("/admin/users/new", status_code=303)


@router.get("/users/{user_id}/edit")
def edit_user_page(
    user_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    target = session.get(User, user_id)
    if not target:
        return RedirectResponse("/admin/users", status_code=303)
    return templates.TemplateResponse(request, "edit_user.html", {"user": user, "target": target})


@router.post("/users/{user_id}/edit")
def edit_user_submit(
    user_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    status: str = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    target = session.get(User, user_id)
    if not target:
        return RedirectResponse("/admin/users", status_code=303)

    status_changed = target.status != status
    target.name = name
    target.email = email
    target.status = status
    session.add(target)

    if status_changed:
        action = "Suspended Account" if status == "Suspended" else "Reactivated Account"
        log_action(session, actor_name=user.name, action=action,
                   details=f"{action.split()[0]} {target.name}'s {target.role.lower()} account")

    session.commit()
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/users/{user_id}/remove")
def remove_user_confirm(
    user_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    target = session.get(User, user_id)
    if not target:
        return RedirectResponse("/admin/users", status_code=303)
    return templates.TemplateResponse(request, "remove_user_confirm.html", {"user": user, "target": target})


@router.post("/users/{user_id}/remove")
def remove_user_submit(
    user_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    target = session.get(User, user_id)
    if not target:
        return RedirectResponse("/admin/users", status_code=303)

    if target.id == user.id:
        # Don't let an Admin delete their own account out from under
        # themselves mid-session -- a plain, sensible guard, not from Figma.
        return RedirectResponse("/admin/users", status_code=303)

    name, role = target.name, target.role
    session.delete(target)
    log_action(session, actor_name=user.name, action="Removed Account",
               details=f"Removed {name}'s {role.lower()} account")
    session.commit()

    return RedirectResponse("/admin/users", status_code=303)


# ---------------------------------------------------------------------
# All Reports
# ---------------------------------------------------------------------
@router.get("/reports")
def all_reports(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(select(Report).order_by(Report.created_at.desc())).all()
    rows = []
    for r in reports:
        inspector = session.get(User, r.inspector_id)
        rows.append({
            "report": r,
            "inspector_name": inspector.name if inspector else "Unknown",
            "severity": r.model3_severity if r.analysis_run_at else None,
        })

    return templates.TemplateResponse(request, "all_reports.html", {"user": user, "rows": rows})


# ---------------------------------------------------------------------
# Admin Delete Report Confirmation (Figma 68:30) -- a genuinely new
# feature: before this chunk, an Admin had no way to delete a report at
# all. Same real popup-styled confirmation pattern as Remove Account.
# ---------------------------------------------------------------------
@router.get("/reports/{report_id}/delete")
def delete_report_confirm(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/admin/reports", status_code=303)
    return templates.TemplateResponse(request, "delete_report_confirm.html", {"user": user, "report": report})


@router.post("/reports/{report_id}/delete")
def delete_report_submit(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/admin/reports", status_code=303)

    report_ref = f"RPT-{report.id:04d}"
    location = report.location
    session.delete(report)
    log_action(session, actor_name=user.name, action="Deleted Report",
               details=f"Deleted {report_ref} ({location})")
    session.commit()

    return RedirectResponse("/admin/reports", status_code=303)


# Audit Log
@router.get("/audit-log")
def audit_log_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    entries = session.exec(select(AuditLogEntry).order_by(AuditLogEntry.created_at.desc())).all()
    return templates.TemplateResponse(request, "audit_log.html", {"user": user, "entries": entries})


# Inbox
@router.get("/inbox")
def inbox_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    messages = session.exec(select(Message).order_by(Message.created_at.desc())).all()
    return templates.TemplateResponse(request, "inbox.html", {"user": user, "messages": messages})


@router.get("/inbox/compose")
def compose_message_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    recipients = session.exec(select(User).where(User.id != user.id).order_by(User.name)).all()
    return templates.TemplateResponse(request, "compose_message.html", {"user": user, "recipients": recipients})


@router.post("/inbox/compose")
def compose_message_submit(
    request: Request,
    recipient_id: int = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    recipient = session.get(User, recipient_id)
    if not recipient:
        return RedirectResponse("/admin/inbox", status_code=303)

    msg = Message(
        from_admin=True,
        sender_name=user.name,
        sender_user_id=user.id,
        recipient_name=recipient.name,
        recipient_user_id=recipient.id,
        subject=subject,
        body=body,
        is_read=True,
    )
    session.add(msg)
    session.commit()

    return RedirectResponse("/admin/inbox", status_code=303)


@router.get("/inbox/{message_id}")
def inbox_thread_page(
    message_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    msg = session.get(Message, message_id)
    if not msg:
        return RedirectResponse("/admin/inbox", status_code=303)

    if not msg.is_read:
        msg.is_read = True
        session.add(msg)
        session.commit()
        session.refresh(msg)

    return templates.TemplateResponse(request, "inbox_thread.html", {"user": user, "message": msg})


@router.post("/inbox/{message_id}/reply")
def inbox_reply_submit(
    message_id: int,
    request: Request,
    reply_body: str = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    msg = session.get(Message, message_id)
    if not msg:
        return RedirectResponse("/admin/inbox", status_code=303)

    msg.reply_body = reply_body
    msg.replied_at = datetime.utcnow()
    session.add(msg)
    session.commit()

    return RedirectResponse(f"/admin/inbox/{message_id}", status_code=303)
