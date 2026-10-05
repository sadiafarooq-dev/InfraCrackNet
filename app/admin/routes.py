# Admin routes
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user, generate_employee_code, verify_password
from database import get_session
from models.user import User
from models.report import Report
from models.audit_log import AuditLogEntry, log_action
from models.app_settings import get_settings, generate_registration_code
from models.project import Project, ProjectInspector, ProjectEngineer
from time_utils import register_localtime
from asset_version import register_asset_version

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=["templates", "admin/templates"])
register_localtime(templates)
register_asset_version(templates)


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


def _compute_trends_context(reports, range):
    """The actual number-crunching behind the Trends page -- pulled out on
    its own so both the normal page load (trends() below) and the new AI
    Trend Insights button (trends_summarize() below) compute the exact same
    numbers the same way, instead of this logic existing in two places that
    could quietly drift apart. Returns the same dict of buckets/top_locations
    /status_breakdown that used to be built directly inside trends()."""
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

    return {"buckets": buckets, "top_locations": top_locations, "status_breakdown": status_breakdown}


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
    ctx = _compute_trends_context(reports, range)

    return templates.TemplateResponse(
        request,
        "trends.html",
        {
            "user": user,
            "range": range,
            "buckets": ctx["buckets"],
            "top_locations": ctx["top_locations"],
            "status_breakdown": ctx["status_breakdown"],
        },
    )


# --- AI Trend Insights (4th of the 6 AI features -- see PROJECT_LOG.md) ---
# Kept as its own small, local copy of the same Gemini REST call pattern
# used in engineer/routes.py (Treatment/Cause/Summary), rather than
# importing across routers, so the Admin and Engineer routers stay
# independent of each other -- same idea, deliberately not shared code this
# time, to avoid coupling two otherwise-separate parts of the app together.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


def _call_gemini(prompt, not_configured_message, failure_message):
    """Same behavior as engineer/routes.py's _call_gemini -- returns
    (suggestion_text, error_message), exactly one of the two set."""
    if not GEMINI_API_KEY:
        return None, not_configured_message
    try:
        resp = requests.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        if not text:
            return None, "The AI didn't return a summary this time -- please try again."
        return text, None
    except Exception:
        return None, failure_message


RANGE_LABELS = {"month": "the last 4 weeks", "quarter": "the last 3 months", "year": "the last 12 months"}


def _build_trends_summary_prompt(range, buckets, top_locations, status_breakdown):
    """Turns the same aggregate numbers already shown on the Trends page
    into a plain-English prompt asking for a short pattern summary --
    nothing here is a live per-report AI call, it's just the same totals
    the charts on the page already show, handed to Gemini in words instead
    of bars."""
    lines = [
        "You are summarizing crack-inspection report trends for a civil "
        "infrastructure Admin dashboard, covering " + RANGE_LABELS.get(range, "the selected period") + ".",
        "",
        "Reports submitted per period, oldest first:",
    ]
    for b in buckets:
        lines.append(f"- {b['label']}: {b['count']}")

    if top_locations:
        lines.append("")
        lines.append("Top affected locations:")
        for loc in top_locations:
            lines.append(f"- {loc['location']}: {loc['count']} report(s)")

    lines.append("")
    lines.append("Reports by current status:")
    for s in status_breakdown:
        lines.append(f"- {s['label']}: {s['count']} ({s['pct']}%)")

    lines += [
        "",
        "In 3-5 short, plain-English sentences with no headings, summarize the "
        "overall pattern -- whether report volume is rising, falling, or "
        "steady across the periods shown, which locations stand out (if any), "
        "and what the status breakdown suggests about how well reports are "
        "being kept up with. Do not just list the numbers back -- describe "
        "what they mean, the way you'd brief a busy Admin who hasn't looked "
        "at the charts yet.",
    ]
    return "\n".join(lines)


def _suggest_trends_summary_from_ai(range, buckets, top_locations, status_breakdown):
    """Calls Google's Gemini API for a plain-English Trends summary. Returns
    (suggestion_text, error_message) -- exactly one of the two is set."""
    prompt = _build_trends_summary_prompt(range, buckets, top_locations, status_breakdown)
    return _call_gemini(
        prompt,
        "AI insights aren't set up yet -- a Gemini API key needs to be added to .env.",
        "Couldn't reach the AI insights service right now. The charts above are still fully up to date.",
    )


@router.post("/trends/summarize")
def trends_summarize(
    request: Request,
    range: str = Form("quarter"),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Behind the "Generate Insights" button on the Trends page. Recomputes
    the exact same numbers as the normal page load (via
    _compute_trends_context, so the two can never disagree), asks Gemini for
    a plain-English summary of them, and re-renders the same page with that
    summary included -- computed fresh on each click, never saved to the
    database, since these are aggregate numbers across ALL reports rather
    than something that belongs to one report."""
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    if range not in ("month", "quarter", "year"):
        range = "quarter"

    reports = session.exec(select(Report)).all()
    ctx = _compute_trends_context(reports, range)

    summary, error = _suggest_trends_summary_from_ai(
        range, ctx["buckets"], ctx["top_locations"], ctx["status_breakdown"]
    )

    return templates.TemplateResponse(
        request,
        "trends.html",
        {
            "user": user,
            "range": range,
            "buckets": ctx["buckets"],
            "top_locations": ctx["top_locations"],
            "status_breakdown": ctx["status_breakdown"],
            "trend_summary": summary,
            "trend_summary_error": error,
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
    project_names = {p.id: p.name for p in session.exec(select(Project)).all()}
    rows = []
    for r in reports:
        inspector = session.get(User, r.inspector_id)
        rows.append({
            "report": r,
            "inspector_name": inspector.name if inspector else "Unknown",
            "project_name": project_names.get(r.project_id, "Unassigned"),
            "severity": r.model3_severity if r.analysis_run_at else None,
            "condition_score": r.condition_score if r.analysis_run_at else None,
            "condition_rating": r.condition_rating if r.analysis_run_at else None,
            "crack_length_cm": r.crack_length_cm if r.analysis_run_at else None,
            "crack_width_cm": r.crack_width_cm if r.analysis_run_at else None,
            "crack_length_px": r.crack_length_px if r.analysis_run_at else None,
            "crack_width_px": r.crack_width_px if r.analysis_run_at else None,
            "crack_area_px": r.crack_area_px if r.analysis_run_at else None,
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


# ---------------------------------------------------------------------
# Projects -- a genuinely new feature (see PROJECT_LOG.md): a Project is a
# specific site (e.g. "Metro Bridge, North Span") that collects many crack
# reports over time. Only Admin creates/edits Projects, and picks which
# Inspectors and which Engineers are assigned to each one -- Inspectors only
# ever see their own assigned Projects when starting a New Report, and
# Engineers only ever see reports from their own assigned Projects in their
# Review Queue / All Reports.
# ---------------------------------------------------------------------
@router.get("/projects")
def projects_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    projects = session.exec(select(Project).order_by(Project.name)).all()
    rows = []
    for p in projects:
        inspector_count = len(session.exec(
            select(ProjectInspector).where(ProjectInspector.project_id == p.id)
        ).all())
        engineer_count = len(session.exec(
            select(ProjectEngineer).where(ProjectEngineer.project_id == p.id)
        ).all())
        report_count = len(session.exec(select(Report).where(Report.project_id == p.id)).all())
        rows.append({
            "project": p,
            "inspector_count": inspector_count,
            "engineer_count": engineer_count,
            "report_count": report_count,
        })

    return templates.TemplateResponse(request, "projects.html", {"user": user, "rows": rows})


def _assignable_staff(session: Session):
    """Every active-or-not Inspector and Engineer account, for the checkbox
    lists on the Create/Edit Project forms -- Admin picks from the real
    account list rather than typing names."""
    inspectors = session.exec(select(User).where(User.role == "Inspector").order_by(User.name)).all()
    engineers = session.exec(select(User).where(User.role == "Engineer").order_by(User.name)).all()
    return inspectors, engineers


@router.get("/projects/new")
def new_project_page(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    inspectors, engineers = _assignable_staff(session)
    return templates.TemplateResponse(
        request,
        "new_project.html",
        {"user": user, "inspectors": inspectors, "engineers": engineers,
         "selected_inspector_ids": [], "selected_engineer_ids": []},
    )


@router.post("/projects/new")
def new_project_submit(
    request: Request,
    name: str = Form(...),
    location: str = Form(""),
    inspector_ids: list[int] = Form([]),
    engineer_ids: list[int] = Form([]),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)

    project = Project(name=name, location=location.strip() or None)
    session.add(project)
    session.flush()

    for uid in inspector_ids:
        session.add(ProjectInspector(project_id=project.id, user_id=uid))
    for uid in engineer_ids:
        session.add(ProjectEngineer(project_id=project.id, user_id=uid))

    log_action(
        session, actor_name=user.name, action="Created Project",
        details=f"Created project \"{project.name}\" with {len(inspector_ids)} inspector(s) and {len(engineer_ids)} engineer(s) assigned",
    )
    session.commit()

    return RedirectResponse("/admin/projects", status_code=303)


@router.get("/projects/{project_id}/edit")
def edit_project_page(
    project_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    project = session.get(Project, project_id)
    if not project:
        return RedirectResponse("/admin/projects", status_code=303)

    inspectors, engineers = _assignable_staff(session)
    selected_inspector_ids = [
        row.user_id for row in session.exec(
            select(ProjectInspector).where(ProjectInspector.project_id == project_id)
        ).all()
    ]
    selected_engineer_ids = [
        row.user_id for row in session.exec(
            select(ProjectEngineer).where(ProjectEngineer.project_id == project_id)
        ).all()
    ]

    return templates.TemplateResponse(
        request,
        "edit_project.html",
        {
            "user": user, "project": project, "inspectors": inspectors, "engineers": engineers,
            "selected_inspector_ids": selected_inspector_ids, "selected_engineer_ids": selected_engineer_ids,
        },
    )


@router.post("/projects/{project_id}/edit")
def edit_project_submit(
    project_id: int,
    request: Request,
    name: str = Form(...),
    location: str = Form(""),
    inspector_ids: list[int] = Form([]),
    engineer_ids: list[int] = Form([]),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_admin(user):
        return RedirectResponse("/login", status_code=303)
    project = session.get(Project, project_id)
    if not project:
        return RedirectResponse("/admin/projects", status_code=303)

    project.name = name
    project.location = location.strip() or None
    session.add(project)

    # Replace the assignment rows wholesale -- simplest correct way to sync
    # "whatever's checked now" without diffing old vs new one by one.
    for row in session.exec(select(ProjectInspector).where(ProjectInspector.project_id == project_id)).all():
        session.delete(row)
    for row in session.exec(select(ProjectEngineer).where(ProjectEngineer.project_id == project_id)).all():
        session.delete(row)
    for uid in inspector_ids:
        session.add(ProjectInspector(project_id=project_id, user_id=uid))
    for uid in engineer_ids:
        session.add(ProjectEngineer(project_id=project_id, user_id=uid))

    log_action(
        session, actor_name=user.name, action="Updated Project",
        details=f"Updated project \"{project.name}\" -- now {len(inspector_ids)} inspector(s) and {len(engineer_ids)} engineer(s) assigned",
    )
    session.commit()

    return RedirectResponse("/admin/projects", status_code=303)

