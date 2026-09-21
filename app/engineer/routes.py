# Engineer routes
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user
from database import get_session
from models.report import Report
from models.user import User
from ml_models.predict import predict_crack_presence
from ml_models.predict_type import predict_crack_type
from ml_models.predict_severity import predict_severity
from engineer.pdf_export import build_report_pdf
from models.audit_log import log_action
from time_utils import register_localtime, now_local

router = APIRouter(prefix="/engineer")
templates = Jinja2Templates(directory=["templates", "engineer/templates"])
register_localtime(templates)

UPLOAD_DIR = os.path.join("..", "uploads")


LOW_CONFIDENCE_THRESHOLD = 70.0


OVERDUE_DAYS = 3


def require_engineer(user):
    return user and user.role == "Engineer"


def _ai_result(report: Report):
    """Runs the real, working crack-presence model (Model 1) on a report's
    original photo. Returns (label, confidence) or (None, None) if the model
    file isn't in place -- same helper pattern as the Inspector's Report
    Detail page."""
    photo_full_path = os.path.join(UPLOAD_DIR, report.photo_path)
    return predict_crack_presence(photo_full_path)


def _run_analysis(report: Report):
    """Actually runs Model 1, Model 2 (for Road reports), and Model 3 on a
    report's photo, for real -- this is the one place in the app that calls
    the trained models to produce the "official" saved result for a report.
    Called only when an Engineer deliberately clicks "Run Analysis" (see
    run_analysis_submit() below), not on every page load."""
    photo_full_path = os.path.join(UPLOAD_DIR, report.photo_path)
    ai_label, ai_confidence = predict_crack_presence(photo_full_path)

    if report.surface_type == "Road":
        type_label, type_confidence = predict_crack_type(photo_full_path)
    else:
        type_label, type_confidence = None, None

    ext = os.path.splitext(report.photo_path)[1] or ".jpg"
    annotated_filename = f"{uuid.uuid4().hex}_outline{ext}"
    annotated_full_path = os.path.join(UPLOAD_DIR, annotated_filename)
    severity_label, annotated_saved = predict_severity(photo_full_path, annotated_full_path)
    if not annotated_saved:
        annotated_filename = None

    return (ai_label, ai_confidence, type_label, type_confidence,
            severity_label, annotated_filename)


@router.get("/dashboard")
def home(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(select(Report).order_by(Report.created_at.desc())).all()
    pending = [r for r in reports if r.status in ("Submitted", "Under Review")]

    flagged_ids = set()
    for r in pending:
        _, confidence = _ai_result(r)
        if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
            flagged_ids.add(r.id)

    week_ago = datetime.utcnow() - timedelta(days=7)
    reviewed_this_week = len([r for r in reports if r.reviewed_at and r.reviewed_at >= week_ago])

    stats = {
        "pending": len(pending),
        "flagged": len(flagged_ids),
        "reviewed_week": reviewed_this_week,
    }

   
    flagged_reports = [r for r in pending if r.id in flagged_ids]
    other_reports = [r for r in pending if r.id not in flagged_ids]
    attention_rows = [
        {"report": r, "flagged": True} for r in flagged_reports
    ] + [
        {"report": r, "flagged": False} for r in other_reports
    ]
    attention_rows = attention_rows[:5]

    return templates.TemplateResponse(
        request, "home.html", {"user": user, "stats": stats, "attention_rows": attention_rows}
    )


@router.get("/queue")
def review_queue(
    request: Request,
    tab: str = "all",
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    pending = session.exec(
        select(Report).where(Report.status.in_(["Submitted", "Under Review"])).order_by(Report.created_at)
    ).all()

    now = datetime.utcnow()
    overdue = [r for r in pending if (now - r.created_at).days >= OVERDUE_DAYS]

    if tab == "overdue":
        shown = overdue
    else:
        tab = "all"
        shown = pending

    rows = []
    for r in shown:
        inspector = session.get(User, r.inspector_id)
        rows.append({"report": r, "inspector_name": inspector.name if inspector else "Unknown"})

    return templates.TemplateResponse(
        request,
        "review_queue.html",
        {
            "user": user,
            "rows": rows,
            "tab": tab,
            "all_count": len(pending),
            "overdue_count": len(overdue),
        },
    )


@router.get("/reports")
def all_reports(
    request: Request,
    q: Optional[str] = None,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(select(Report).order_by(Report.created_at.desc())).all()

    if q:
        needle = q.strip().lower()
        reports = [
            r for r in reports
            if needle in r.location.lower() or needle in f"rpt-{r.id:04d}".lower()
        ]

    rows = []
    for r in reports:
        inspector = session.get(User, r.inspector_id)
        type_label = r.model2_label if r.analysis_run_at else None
        severity = r.model3_severity if r.analysis_run_at else None
        rows.append({
            "report": r,
            "inspector_name": inspector.name if inspector else "Unknown",
            "type_label": type_label,
            "severity": severity,
        })

    return templates.TemplateResponse(request, "all_reports.html", {"user": user, "rows": rows, "q": q or ""})


@router.get("/reports/{report_id}")
def report_review_page(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)

    if report.status == "Submitted":
        report.status = "Under Review"
        report.under_review_at = datetime.utcnow()
        session.add(report)
        session.commit()
        session.refresh(report)

    inspector = session.get(User, report.inspector_id)


    analysis_done = report.analysis_run_at is not None
    ai_label = report.model1_label if analysis_done else None
    ai_confidence = report.model1_confidence if analysis_done else None
    type_label = report.model2_label if analysis_done else None
    type_confidence = report.model2_confidence if analysis_done else None
    severity_label = report.model3_severity if analysis_done else None
    annotated_photo_path = report.model3_annotated_photo_path if analysis_done else None

    return templates.TemplateResponse(
        request,
        "report_review.html",
        {
            "user": user,
            "report": report,
            "inspector_name": inspector.name if inspector else "Unknown",
            "analysis_done": analysis_done,
            "ai_label": ai_label,
            "ai_confidence": ai_confidence,
            "type_label": type_label,
            "type_confidence": type_confidence,
            "severity_label": severity_label,
            "annotated_photo_path": annotated_photo_path,
        },
    )


@router.post("/reports/{report_id}/run-analysis")
def run_analysis_submit(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)

    (ai_label, ai_confidence, type_label, type_confidence,
     severity_label, annotated_filename) = _run_analysis(report)

    report.model1_label = ai_label
    report.model1_confidence = ai_confidence
    report.model2_label = type_label
    report.model2_confidence = type_confidence
    report.model3_severity = severity_label
    report.model3_annotated_photo_path = annotated_filename
    report.analysis_run_at = datetime.utcnow()
    session.add(report)

    log_action(session, actor_name=user.name, action="Ran AI Analysis",
               details=f"Ran AI analysis on RPT-{report.id:04d}")

    session.commit()

    return RedirectResponse(f"/engineer/reports/{report.id}/analyzing", status_code=303)


@router.get("/reports/{report_id}/analyzing")
def analyzing_page(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)
    if not report.analysis_run_at:
        # Nothing to show an animation for -- send them back to run it first.
        return RedirectResponse(f"/engineer/reports/{report_id}", status_code=303)

    return templates.TemplateResponse(
        request,
        "analyzing.html",
        {
            "user": user,
            "report": report,
            "is_road": report.surface_type == "Road",
            "ai_label": report.model1_label,
            "ai_confidence": report.model1_confidence,
            "type_label": report.model2_label,
            "type_confidence": report.model2_confidence,
            "severity_label": report.model3_severity,
        },
    )


@router.post("/reports/{report_id}")
def report_review_submit(
    report_id: int,
    request: Request,
    engineer_remarks: str = Form(""),
    treatment: str = Form(""),
    status: str = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)

    report.engineer_remarks = engineer_remarks
    report.treatment = treatment.strip() or None
    report.status = status
    report.reviewed_by_id = user.id
    report.reviewed_at = datetime.utcnow()
    session.add(report)

    action = "Resolved Report" if status == "Resolved" else "Reviewed Report"
    log_action(session, actor_name=user.name, action=action,
               details=f"Marked RPT-{report.id:04d} as {status}")

    session.commit()

    return RedirectResponse(f"/engineer/reports/{report.id}", status_code=303)



@router.get("/reports/{report_id}/mark-resolved")
def mark_resolved_confirm(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)
    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)
    return templates.TemplateResponse(request, "mark_resolved_confirm.html", {"user": user, "report": report})


@router.post("/reports/{report_id}/mark-resolved")
def mark_resolved_submit(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)
    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)

    report.status = "Resolved"
    report.reviewed_by_id = user.id
    report.reviewed_at = datetime.utcnow()
    session.add(report)
    log_action(session, actor_name=user.name, action="Resolved Report",
               details=f"Marked RPT-{report.id:04d} as Resolved")
    session.commit()

    return RedirectResponse(f"/engineer/reports/{report.id}", status_code=303)



@router.get("/reports/{report_id}/follow-up/new")
def request_follow_up_page(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)
    if report.status not in ("Reviewed", "Resolved"):
        return RedirectResponse(f"/engineer/reports/{report_id}", status_code=303)

    waiting_on_inspector = bool(report.follow_up_requested_at) and not report.follow_up_photo_path

    return templates.TemplateResponse(
        request, "follow_up_new.html", {"user": user, "report": report, "waiting_on_inspector": waiting_on_inspector}
    )


@router.post("/reports/{report_id}/follow-up/request")
def request_follow_up_submit(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report or report.status not in ("Reviewed", "Resolved"):
        return RedirectResponse("/engineer/queue", status_code=303)

    
    report.follow_up_requested_at = datetime.utcnow()
    report.follow_up_photo_path = None
    report.follow_up_created_at = None
    report.follow_up_ai_label = None
    report.follow_up_ai_confidence = None
    report.follow_up_severity = None
    report.follow_up_annotated_photo_path = None
    report.follow_up_decision = None
    session.add(report)
    log_action(session, actor_name=user.name, action="Requested Follow-up",
               details=f"Requested a follow-up photo for RPT-{report.id:04d} — {report.location}")
    session.commit()

    return RedirectResponse(f"/engineer/reports/{report_id}", status_code=303)


@router.get("/reports/{report_id}/follow-up")
def follow_up_scan_page(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)
    if not report.follow_up_photo_path:
        return RedirectResponse(f"/engineer/reports/{report_id}/follow-up/new", status_code=303)

    original_label, original_confidence = _ai_result(report)

    
    original_severity = report.model3_severity if report.analysis_run_at else None

    return templates.TemplateResponse(
        request,
        "follow_up_scan.html",
        {
            "user": user,
            "report": report,
            "original_label": original_label,
            "original_confidence": original_confidence,
            "original_severity": original_severity,
        },
    )


@router.post("/reports/{report_id}/follow-up/decision")
def follow_up_decision_submit(
    report_id: int,
    request: Request,
    decision: str = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report or not report.follow_up_photo_path:
        return RedirectResponse("/engineer/queue", status_code=303)

    report.follow_up_decision = decision
    if decision == "Escalated":
        
        report.status = "Under Review"
    session.add(report)

    if decision == "Escalated":
        log_action(session, actor_name=user.name, action="Escalated Follow-up",
                   details=f"RPT-{report.id:04d} flagged for another look after a follow-up scan")

    session.commit()

    return RedirectResponse(f"/engineer/reports/{report_id}/follow-up", status_code=303)


@router.get("/follow-ups")
def follow_ups_index(
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(
        select(Report).where(Report.status.in_(["Reviewed", "Resolved"])).order_by(Report.created_at.desc())
    ).all()

    return templates.TemplateResponse(request, "follow_ups_index.html", {"user": user, "reports": reports})



@router.get("/reports/{report_id}/pdf")
def pdf_preview_page(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)

    reviewer = session.get(User, report.reviewed_by_id) if report.reviewed_by_id else None

    return templates.TemplateResponse(
        request,
        "pdf_preview.html",
        {
            "user": user,
            "report": report,
            "reviewer": reviewer,
            "now": now_local().strftime("%b %d, %Y"),
        },
    )


@router.get("/reports/{report_id}/pdf/download")
def pdf_download(
    report_id: int,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_engineer(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report:
        return RedirectResponse("/engineer/queue", status_code=303)

    reviewer = session.get(User, report.reviewed_by_id) if report.reviewed_by_id else None
    photo_full_path = os.path.join(UPLOAD_DIR, report.photo_path)
    pdf_bytes = build_report_pdf(report, reviewer, photo_full_path)

    filename = f"InfraCrackNet_Report_RPT-{report.id:04d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
