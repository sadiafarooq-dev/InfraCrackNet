# Inspector routes
import os
import shutil
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user
from database import get_session
from models.report import Report
from models.user import User
from models.audit_log import log_action
from ml_models.predict import predict_crack_presence
from ml_models.predict_severity import predict_severity
from time_utils import register_localtime

router = APIRouter(prefix="/inspector")
templates = Jinja2Templates(directory=["templates", "inspector/templates"])
register_localtime(templates)

UPLOAD_DIR = os.path.join("..", "uploads")


STRUCTURE_AGE_OPTIONS = ["< 5 yrs", "5-15 yrs", "15+ yrs"]
SURFACE_CONDITION_OPTIONS = ["Dry", "Damp", "Wet"]
ACTIVELY_GROWING_OPTIONS = ["Yes", "No", "Not sure"]


def require_inspector(user):
    return user and user.role == "Inspector"


def _draft(request: Request) -> dict:
    return request.session.get("new_report_draft", {})


def _save_draft(request: Request, draft: dict):
    request.session["new_report_draft"] = draft


# Home 
@router.get("/dashboard")
def home(request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(
        select(Report).where(Report.inspector_id == user.id).order_by(Report.created_at.desc())
    ).all()

    stats = {
        "total": len(reports),
        "pending": len([r for r in reports if r.status in ("Submitted", "Under Review")]),
        "resolved": len([r for r in reports if r.status == "Resolved"]),
    }

    return templates.TemplateResponse(
        request, "home.html", {"user": user, "stats": stats, "recent_reports": reports[:5]}
    )


# My Reports
@router.get("/reports")
def my_reports_page(
    request: Request,
    q: Optional[str] = None,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(
        select(Report).where(Report.inspector_id == user.id).order_by(Report.created_at.desc())
    ).all()

    if q:
        needle = q.strip().lower()
        reports = [
            r for r in reports
            if needle in r.location.lower() or needle in f"rpt-{r.id:04d}".lower()
        ]

    return templates.TemplateResponse(
        request, "my_reports.html", {"user": user, "reports": reports, "q": q or ""}
    )


# New Report 
@router.get("/reports/new")
def new_report_start(request: Request, user=Depends(get_current_user)):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    request.session.pop("new_report_draft", None)
    return RedirectResponse("/inspector/reports/new/upload", status_code=303)


@router.get("/reports/new/cancel")
def new_report_cancel(request: Request, user=Depends(get_current_user)):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    request.session.pop("new_report_draft", None)
    return RedirectResponse("/inspector/dashboard", status_code=303)


@router.get("/reports/new/upload")
def new_report_upload_page(request: Request, user=Depends(get_current_user)):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "new_report_upload.html", {"user": user})


@router.post("/reports/new/upload")
def new_report_upload_submit(
    request: Request,
    photo: UploadFile = File(...),
    user=Depends(get_current_user),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(photo.filename)[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        shutil.copyfileobj(photo.file, f)

    draft = _draft(request)
    draft["photo_path"] = filename
    _save_draft(request, draft)

    return RedirectResponse("/inspector/reports/new/location", status_code=303)


@router.get("/reports/new/location")
def new_report_location_page(request: Request, user=Depends(get_current_user)):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    draft = _draft(request)
    if "photo_path" not in draft:
        return RedirectResponse("/inspector/reports/new/upload", status_code=303)
    return templates.TemplateResponse(
        request, "new_report_location.html", {"user": user, "location": draft.get("location", "")}
    )


@router.post("/reports/new/location")
def new_report_location_submit(
    request: Request,
    location: str = Form(...),
    user=Depends(get_current_user),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    draft = _draft(request)
    if "photo_path" not in draft:
        return RedirectResponse("/inspector/reports/new/upload", status_code=303)
    draft["location"] = location
    _save_draft(request, draft)
    return RedirectResponse("/inspector/reports/new/context", status_code=303)


@router.get("/reports/new/context")
def new_report_context_page(request: Request, user=Depends(get_current_user)):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    draft = _draft(request)
    if "location" not in draft:
        return RedirectResponse("/inspector/reports/new/location", status_code=303)
    return templates.TemplateResponse(
        request,
        "new_report_context.html",
        {
            "user": user,
            "structure_age": draft.get("structure_age", ""),
            "surface_condition": draft.get("surface_condition", ""),
            "actively_growing": draft.get("actively_growing", ""),
            "cause": draft.get("cause", ""),
        },
    )


@router.post("/reports/new/context")
def new_report_context_submit(
    request: Request,
    structure_age: str = Form(...),
    surface_condition: str = Form(...),
    actively_growing: str = Form(...),
    cause: str = Form(""),
    user=Depends(get_current_user),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    draft = _draft(request)
    if "location" not in draft:
        return RedirectResponse("/inspector/reports/new/location", status_code=303)
    draft["structure_age"] = structure_age
    draft["surface_condition"] = surface_condition
    draft["actively_growing"] = actively_growing
    draft["cause"] = cause.strip()
    _save_draft(request, draft)
    return RedirectResponse("/inspector/reports/new/review", status_code=303)


@router.get("/reports/new/review")
def new_report_review_page(request: Request, user=Depends(get_current_user)):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    draft = _draft(request)
    if "structure_age" not in draft:
        return RedirectResponse("/inspector/reports/new/context", status_code=303)
    return templates.TemplateResponse(request, "new_report_review.html", {"user": user, "draft": draft})


@router.post("/reports/new/review")
def new_report_submit(
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    draft = _draft(request)
    if "structure_age" not in draft:
        return RedirectResponse("/inspector/reports/new/context", status_code=303)

    report = Report(
        inspector_id=user.id,
        photo_path=draft["photo_path"],
        location=draft["location"],
        structure_age=draft["structure_age"],
        surface_condition=draft["surface_condition"],
        actively_growing=draft["actively_growing"],
        cause=draft.get("cause") or None,
    )
    session.add(report)
    session.flush()
    log_action(session, actor_name=user.name, action="Submitted Report",
               details=f"New report RPT-{report.id:04d} — {report.location}")
    session.commit()
    session.refresh(report)

    request.session.pop("new_report_draft", None)

    return RedirectResponse(f"/inspector/reports/{report.id}/confirmation", status_code=303)


# Confirmation
@router.get("/reports/{report_id}/confirmation")
def report_confirmation(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report or report.inspector_id != user.id:
        return RedirectResponse("/inspector/dashboard", status_code=303)

    return templates.TemplateResponse(request, "confirmation.html", {"user": user, "report": report})


# Report detail (read-only) 
@router.get("/reports/{report_id}")
def report_detail(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report or report.inspector_id != user.id:
        return RedirectResponse("/inspector/dashboard", status_code=303)

   
    analysis_done = report.analysis_run_at is not None
    ai_label = report.model1_label if analysis_done else None
    ai_confidence = report.model1_confidence if analysis_done else None
    type_label = report.model2_label if analysis_done else None
    type_confidence = report.model2_confidence if analysis_done else None
    severity_label = report.model3_severity if analysis_done else None
    annotated_photo_path = report.model3_annotated_photo_path if analysis_done else None

    reviewer = session.get(User, report.reviewed_by_id) if report.reviewed_by_id else None

   
    follow_up_pending = bool(report.follow_up_requested_at) and not report.follow_up_photo_path

    return templates.TemplateResponse(
        request,
        "report_detail.html",
        {
            "user": user,
            "report": report,
            "analysis_done": analysis_done,
            "ai_label": ai_label,
            "ai_confidence": ai_confidence,
            "type_label": type_label,
            "type_confidence": type_confidence,
            "severity_label": severity_label,
            "annotated_photo_path": annotated_photo_path,
            "reviewer": reviewer,
            "follow_up_pending": follow_up_pending,
        },
    )


@router.get("/reports/{report_id}/follow-up/new")
def follow_up_upload_page(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report or report.inspector_id != user.id:
        return RedirectResponse("/inspector/dashboard", status_code=303)
    if not report.follow_up_requested_at or report.follow_up_photo_path:
        return RedirectResponse(f"/inspector/reports/{report_id}", status_code=303)

    return templates.TemplateResponse(request, "follow_up_upload.html", {"user": user, "report": report})


@router.post("/reports/{report_id}/follow-up/new")
def follow_up_upload_submit(
    report_id: int,
    request: Request,
    photo: UploadFile = File(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    report = session.get(Report, report_id)
    if not report or report.inspector_id != user.id:
        return RedirectResponse("/inspector/dashboard", status_code=303)
    if not report.follow_up_requested_at or report.follow_up_photo_path:
        return RedirectResponse(f"/inspector/reports/{report_id}", status_code=303)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    ext = os.path.splitext(photo.filename)[1]
    filename = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        shutil.copyfileobj(photo.file, f)

    follow_up_full_path = os.path.join(UPLOAD_DIR, filename)
    ai_label, ai_confidence = predict_crack_presence(follow_up_full_path)

    follow_up_ext = os.path.splitext(filename)[1] or ".jpg"
    follow_up_annotated_filename = f"{uuid.uuid4().hex}_outline{follow_up_ext}"
    follow_up_annotated_full_path = os.path.join(UPLOAD_DIR, follow_up_annotated_filename)
    severity_label, annotated_saved = predict_severity(follow_up_full_path, follow_up_annotated_full_path)
    if not annotated_saved:
        follow_up_annotated_filename = None

    report.follow_up_photo_path = filename
    report.follow_up_created_at = datetime.utcnow()
    report.follow_up_ai_label = ai_label
    report.follow_up_ai_confidence = ai_confidence
    report.follow_up_severity = severity_label
    report.follow_up_annotated_photo_path = follow_up_annotated_filename
    session.add(report)
    log_action(session, actor_name=user.name, action="Submitted Follow-up Photo",
               details=f"Submitted the requested follow-up photo for RPT-{report.id:04d}")
    session.commit()

    return RedirectResponse(f"/inspector/reports/{report_id}", status_code=303)
