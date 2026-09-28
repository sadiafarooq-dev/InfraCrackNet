# Engineer routes
import json
import math
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user
from database import get_session
from models.report import Report
from models.user import User
from ml_models.predict import predict_crack_presence
from ml_models.predict_type import predict_crack_type
from ml_models.predict_severity import predict_severity
from condition_score import compute_condition_score
from engineer.pdf_export import build_report_pdf
from models.audit_log import log_action
from time_utils import register_localtime, now_local

router = APIRouter(prefix="/engineer")
templates = Jinja2Templates(directory=["templates", "engineer/templates"])
register_localtime(templates)

UPLOAD_DIR = os.path.join("..", "uploads")


LOW_CONFIDENCE_THRESHOLD = 70.0


OVERDUE_DAYS = 3


def _review_priority_score(report, now=None):
    """A deterministic, computed priority score for the Review Queue's
    "Priority" tab. NOT an AI/Gemini call -- just three plain, disclosed
    factors added together, so it's cheap to compute for every pending
    report on every page load and easy to explain in plain English:

      1. Severity (from Model 3, once analysis has run): High=50,
         Medium=25, Low=5, none of these (analysis not run yet, no crack
         shape found, or an analysis error) contributes 0 -- it isn't
         penalized OR boosted, it just doesn't get severity points.
      2. Road condition (Road reports only, once analysis has run, since
         PCI is a road-only metric -- same scope rule used everywhere
         else in this project): worse condition score contributes more,
         up to 30 points for the worst possible score.
      3. How long the report has been waiting: up to 20 points, capped at
         10 days, so a report doesn't rack up points forever but genuinely
         older reports still get pushed up the list even before an
         Engineer has run analysis on them at all.

    Highest score first is "most urgent". See PROJECT_LOG.md (Priority
    Queue feature) for why this heuristic and not another Gemini call --
    a live AI call for every row of a list, every time the page loads,
    would be slow and unnecessary for what is fundamentally simple
    arithmetic on numbers the app already has."""
    now = now or datetime.utcnow()
    score = 0.0

    severity_points = {"High": 50.0, "Medium": 25.0, "Low": 5.0}
    score += severity_points.get(report.model3_severity, 0.0)

    if report.surface_type == "Road" and report.condition_score is not None:
        score += max(0.0, (100 - report.condition_score) * 0.3)

    days_waiting = (now - report.created_at).total_seconds() / 86400.0
    score += min(max(days_waiting, 0.0), 10.0) * 2.0

    return round(score, 1)


# --- AI-suggested Treatment (see PROJECT_LOG.md section 88) ---
# Free API key from https://aistudio.google.com/apikey, kept in .env, never
# in this file. Without a key set, the feature just tells the Engineer it
# isn't set up yet -- everything else about the page works exactly the
# same either way.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


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
    # detect_marker defaults to True here -- a normal photo report is
    # exactly the case real cm measurements are scoped to (PROJECT_LOG.md
    # section 72): one still photo, where a printed marker can realistically
    # be included in the shot. The pixel-only values (section 75) are
    # always attempted too, regardless of marker.
    (severity_label, annotated_saved, severity_confidence,
     crack_length_cm, crack_width_cm,
     crack_length_px, crack_width_px, crack_area_px) = predict_severity(photo_full_path, annotated_full_path)
    if not annotated_saved:
        annotated_filename = None

    # PCI-inspired Condition Score -- Road reports only (PCI is a pavement
    # metric, see condition_score.py). None/None for Buildings and for any
    # report where Model 3 didn't trace a real crack shape.
    if report.surface_type == "Road":
        condition_score, condition_rating = compute_condition_score(severity_label, type_label)
    else:
        condition_score, condition_rating = None, None

    return (ai_label, ai_confidence, type_label, type_confidence,
            severity_label, severity_confidence, annotated_filename,
            condition_score, condition_rating, crack_length_cm, crack_width_cm,
            crack_length_px, crack_width_px, crack_area_px)


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
    elif tab == "priority":
        # Smart priority ordering -- see _review_priority_score above for
        # the full, disclosed formula. Highest score (most urgent) first;
        # Python's sort is stable, so reports tied on score keep their
        # original oldest-first order as the tiebreaker.
        shown = sorted(pending, key=_review_priority_score, reverse=True)
    else:
        tab = "all"
        shown = pending

    rows = []
    for r in shown:
        inspector = session.get(User, r.inspector_id)
        rows.append({
            "report": r,
            "inspector_name": inspector.name if inspector else "Unknown",
            "severity_label": r.model3_severity if r.analysis_run_at else None,
        })

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
        condition_score = r.condition_score if r.analysis_run_at else None
        condition_rating = r.condition_rating if r.analysis_run_at else None
        crack_length_cm = r.crack_length_cm if r.analysis_run_at else None
        crack_width_cm = r.crack_width_cm if r.analysis_run_at else None
        crack_length_px = r.crack_length_px if r.analysis_run_at else None
        crack_width_px = r.crack_width_px if r.analysis_run_at else None
        crack_area_px = r.crack_area_px if r.analysis_run_at else None
        rows.append({
            "report": r,
            "inspector_name": inspector.name if inspector else "Unknown",
            "type_label": type_label,
            "severity": severity,
            "condition_score": condition_score,
            "condition_rating": condition_rating,
            "crack_length_cm": crack_length_cm,
            "crack_width_cm": crack_width_cm,
            "crack_length_px": crack_length_px,
            "crack_width_px": crack_width_px,
            "crack_area_px": crack_area_px,
        })

    return templates.TemplateResponse(request, "all_reports.html", {"user": user, "rows": rows, "q": q or ""})


def _haversine_distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance between two real GPS points, in meters --
    plain geometry (the standard haversine formula), not an AI call. See
    PROJECT_LOG.md (Duplicate/Nearby-Report Flagging feature)."""
    R = 6371000.0  # Earth's mean radius, in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


NEARBY_REPORT_THRESHOLD_M = 50.0


def _find_nearby_reports(candidates, target_report, threshold_m=NEARBY_REPORT_THRESHOLD_M):
    """Given a list of OTHER reports (the caller has already excluded the
    report itself), finds any within threshold_m meters of target_report's
    real GPS coordinates -- deliberately GPS-only, not an AI comparison of
    photos or descriptions. Two reports genuinely can be of the same crack
    from two different Inspectors, or of two different cracks that happen
    to be close together -- this can't tell those apart, it only flags
    "these are physically close, might be worth a look", which is why it's
    shown as a plain informational banner, never blocking anything. Returns
    [] if the target report has no real GPS at all (nothing to compare
    against, and a typed address is too imprecise to measure meters from --
    see PROJECT_LOG.md section 69 on location_source for why). Sorted
    nearest first, capped at 5 so a busy area doesn't produce an
    overwhelming list."""
    if target_report.latitude is None or target_report.longitude is None:
        return []
    nearby = []
    for r in candidates:
        if r.latitude is None or r.longitude is None:
            continue
        distance_m = _haversine_distance_m(
            target_report.latitude, target_report.longitude, r.latitude, r.longitude
        )
        if distance_m <= threshold_m:
            nearby.append({"report": r, "distance_m": round(distance_m, 1)})
    nearby.sort(key=lambda item: item["distance_m"])
    return nearby[:5]


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
    severity_confidence = report.model3_confidence if analysis_done else None
    annotated_photo_path = report.model3_annotated_photo_path if analysis_done else None
    # Road video reports only -- see the matching comment in
    # inspector/routes.py's report_detail() for what these are.
    type_boxes_photo_path = report.model2_annotated_photo_path if analysis_done else None
    type_detections = (
        json.loads(report.model2_video_detections)
        if analysis_done and report.model2_video_detections else None
    )
    # PCI-inspired Condition Score -- Road reports only, see condition_score.py.
    condition_score = report.condition_score if analysis_done else None
    condition_rating = report.condition_rating if analysis_done else None
    # Real cm crack measurements -- photo reports only, see
    # aruco_measurement.py and PROJECT_LOG.md section 72. None for video
    # reports, and None for a photo report where no marker was found.
    crack_length_cm = report.crack_length_cm if analysis_done else None
    crack_width_cm = report.crack_width_cm if analysis_done else None
    # The brought-back pixel-only version of the above -- see
    # aruco_measurement.py's measure_crack_pixels and PROJECT_LOG.md
    # section 75. Shown whenever there's a traced shape, no marker needed.
    crack_length_px = report.crack_length_px if analysis_done else None
    crack_width_px = report.crack_width_px if analysis_done else None
    crack_area_px = report.crack_area_px if analysis_done else None

    # Duplicate/Nearby-Report Flagging -- see _find_nearby_reports above.
    # Computed fresh on every page load (not stored) since which reports
    # count as "nearby" can change as new ones come in.
    other_reports = session.exec(
        select(Report).where(Report.id != report.id, Report.status != "Resolved")
    ).all()
    nearby = _find_nearby_reports(other_reports, report)
    nearby_reports = []
    for item in nearby:
        nearby_inspector = session.get(User, item["report"].inspector_id)
        nearby_reports.append({
            "report": item["report"],
            "distance_m": item["distance_m"],
            "inspector_name": nearby_inspector.name if nearby_inspector else "Unknown",
        })

    return templates.TemplateResponse(
        request,
        "report_review.html",
        {
            "user": user,
            "report": report,
            "inspector_name": inspector.name if inspector else "Unknown",
            "nearby_reports": nearby_reports,
            "analysis_done": analysis_done,
            "ai_label": ai_label,
            "ai_confidence": ai_confidence,
            "type_label": type_label,
            "type_confidence": type_confidence,
            "type_boxes_photo_path": type_boxes_photo_path,
            "type_detections": type_detections,
            "severity_label": severity_label,
            "severity_confidence": severity_confidence,
            "annotated_photo_path": annotated_photo_path,
            "condition_score": condition_score,
            "condition_rating": condition_rating,
            "crack_length_cm": crack_length_cm,
            "crack_width_cm": crack_width_cm,
            "crack_length_px": crack_length_px,
            "crack_width_px": crack_width_px,
            "crack_area_px": crack_area_px,
        },
    )


def _build_treatment_prompt(report, type_label, severity_label, condition_score,
                             condition_rating, crack_length_cm, crack_width_cm,
                             crack_length_px, crack_width_px):
    """Turns one report's AI-detected crack data into a plain-English prompt
    asking for a treatment recommendation. See PROJECT_LOG.md section 88."""
    lines = [
        "You are helping a civil engineer decide how to treat a detected crack "
        "on a road or building, based on an AI inspection system's findings.",
        "",
        f"Surface type: {report.surface_type}",
        f"Severity: {severity_label or 'not yet available'}",
    ]
    if report.surface_type == "Road" and type_label:
        lines.append(f"Crack type: {type_label}")
    if condition_score is not None:
        lines.append(f"Road condition (PCI) score: {condition_score} ({condition_rating})")
    if crack_length_cm is not None and crack_width_cm is not None:
        lines.append(f"Measured crack size: {crack_length_cm} cm long, {crack_width_cm} cm wide")
    elif crack_length_px is not None and crack_width_px is not None:
        lines.append(
            f"Measured crack size (pixels only, no physical marker in photo): "
            f"{crack_length_px}px long, {crack_width_px}px wide"
        )
    if report.site_notes:
        lines.append(f"Inspector's on-site notes: {report.site_notes}")

    lines += [
        "",
        "In 1-3 short sentences, written the way a field engineer would write it "
        "in an inspection report, recommend a specific, practical treatment "
        "(for example: seal, patch, resurface, monitor, or a structural repair). "
        "Do not repeat the input data back and do not add any headings -- just "
        "write the recommendation itself, plainly.",
    ]
    return "\n".join(lines)


def _call_gemini(prompt, not_configured_message, failure_message):
    """Shared Gemini REST call used by every "Suggest with AI" button in this
    app (Treatment, and now Cause -- see PROJECT_LOG.md sections 88 and 91).
    Returns (suggestion_text, error_message) -- exactly one of the two is
    set. Pulled out of _suggest_treatment_from_ai so Cause could reuse the
    exact same call/error-handling instead of copy-pasting it."""
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
            return None, "The AI didn't return a suggestion this time -- please try again."
        return text, None
    except Exception:
        return None, failure_message


def _suggest_treatment_from_ai(report, type_label, severity_label, condition_score,
                                condition_rating, crack_length_cm, crack_width_cm,
                                crack_length_px, crack_width_px):
    """Calls Google's Gemini API for a treatment suggestion. Returns
    (suggestion_text, error_message) -- exactly one of the two is set.
    See PROJECT_LOG.md section 88 for why Gemini, and .env.example for how
    to set up the free API key."""
    prompt = _build_treatment_prompt(
        report, type_label, severity_label, condition_score, condition_rating,
        crack_length_cm, crack_width_cm, crack_length_px, crack_width_px,
    )
    return _call_gemini(
        prompt,
        "AI suggestions aren't set up yet -- a Gemini API key needs to be added to .env.",
        "Couldn't reach the AI suggestion service right now. You can still write your own Treatment below.",
    )


def _build_cause_prompt(report, type_label, severity_label, condition_score,
                         condition_rating, crack_length_cm, crack_width_cm,
                         crack_length_px, crack_width_px):
    """Same idea as _build_treatment_prompt, but asks for a likely CAUSE
    instead of a treatment. See PROJECT_LOG.md section 91."""
    lines = [
        "You are helping a civil engineer note down the likely CAUSE of a "
        "detected crack on a road or building, based on an AI inspection "
        "system's findings.",
        "",
        f"Surface type: {report.surface_type}",
        f"Severity: {severity_label or 'not yet available'}",
    ]
    if report.surface_type == "Road" and type_label:
        lines.append(f"Crack type: {type_label}")
    if condition_score is not None:
        lines.append(f"Road condition (PCI) score: {condition_score} ({condition_rating})")
    if crack_length_cm is not None and crack_width_cm is not None:
        lines.append(f"Measured crack size: {crack_length_cm} cm long, {crack_width_cm} cm wide")
    elif crack_length_px is not None and crack_width_px is not None:
        lines.append(
            f"Measured crack size (pixels only, no physical marker in photo): "
            f"{crack_length_px}px long, {crack_width_px}px wide"
        )
    if report.site_notes:
        lines.append(f"Inspector's on-site notes: {report.site_notes}")
    if report.structure_age:
        lines.append(f"Structure/road age (as given by the Inspector): {report.structure_age}")

    lines += [
        "",
        "In 1-2 short sentences, written the way a field engineer would write "
        "it in an inspection report, suggest the most likely cause (for "
        "example: thermal expansion, water infiltration, heavy load/traffic, "
        "poor drainage, ground settlement, or normal ageing). Make clear this "
        "is a probable cause, not a certainty, since it wasn't confirmed on "
        "site. Do not repeat the input data back and do not add any "
        "headings -- just write the suggestion itself, plainly.",
    ]
    return "\n".join(lines)


def _suggest_cause_from_ai(report, type_label, severity_label, condition_score,
                           condition_rating, crack_length_cm, crack_width_cm,
                           crack_length_px, crack_width_px):
    """Calls Google's Gemini API for a likely-cause suggestion. Returns
    (suggestion_text, error_message) -- exactly one of the two is set.
    See PROJECT_LOG.md section 91."""
    prompt = _build_cause_prompt(
        report, type_label, severity_label, condition_score, condition_rating,
        crack_length_cm, crack_width_cm, crack_length_px, crack_width_px,
    )
    return _call_gemini(
        prompt,
        "AI suggestions aren't set up yet -- a Gemini API key needs to be added to .env.",
        "Couldn't reach the AI suggestion service right now. You can still write your own Cause below.",
    )


def _build_summary_prompt(report, ai_label, type_label, severity_label, condition_score,
                           condition_rating, crack_length_cm, crack_width_cm,
                           crack_length_px, crack_width_px):
    """Same idea as _build_treatment_prompt/_build_cause_prompt, but asks for
    a plain-English SUMMARY of what the AI found, written so someone with no
    engineering background can understand it -- meant to be shown to the
    Inspector too, not just the Engineer. See PROJECT_LOG.md (Summary
    feature)."""
    lines = [
        "You are explaining, in plain English, what an AI crack-inspection "
        "system found on a road or building, to someone who is NOT an "
        "engineer and may not know technical terms.",
        "",
        f"Surface type: {report.surface_type}",
    ]
    if ai_label:
        lines.append(f"Crack detected: {ai_label}")
    lines.append(f"Severity: {severity_label or 'not yet available'}")
    if report.surface_type == "Road" and type_label:
        lines.append(f"Crack type: {type_label}")
    if condition_score is not None:
        lines.append(f"Road condition (PCI) score: {condition_score} ({condition_rating})")
    if crack_length_cm is not None and crack_width_cm is not None:
        lines.append(f"Measured crack size: {crack_length_cm} cm long, {crack_width_cm} cm wide")
    elif crack_length_px is not None and crack_width_px is not None:
        lines.append(
            f"Measured crack size (pixels only, no physical marker in photo): "
            f"{crack_length_px}px long, {crack_width_px}px wide"
        )

    lines += [
        "",
        "In 2-4 short, simple sentences, with no technical jargon and no "
        "headings, explain what this means in plain language -- what was "
        "found, roughly how serious it is, and why it matters. Do not repeat "
        "the raw numbers back verbatim and do not recommend a treatment or "
        "guess a cause -- just explain the finding itself, plainly, the way "
        "you'd explain it to someone with no technical background.",
    ]
    return "\n".join(lines)


def _suggest_summary_from_ai(report, ai_label, type_label, severity_label, condition_score,
                              condition_rating, crack_length_cm, crack_width_cm,
                              crack_length_px, crack_width_px):
    """Calls Google's Gemini API for a plain-English summary suggestion.
    Returns (suggestion_text, error_message) -- exactly one of the two is
    set. See PROJECT_LOG.md (Summary feature)."""
    prompt = _build_summary_prompt(
        report, ai_label, type_label, severity_label, condition_score, condition_rating,
        crack_length_cm, crack_width_cm, crack_length_px, crack_width_px,
    )
    return _call_gemini(
        prompt,
        "AI suggestions aren't set up yet -- a Gemini API key needs to be added to .env.",
        "Couldn't reach the AI suggestion service right now. You can still write your own Summary below.",
    )


@router.post("/reports/{report_id}/suggest-summary")
def suggest_summary(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Same pattern as suggest_treatment/suggest_cause above, just for the
    plain-English Summary field -- see static/js/suggest-treatment.js
    (reused for all three buttons, see that file's own note)."""
    if not require_engineer(user):
        return JSONResponse({"error": "Not authorized."}, status_code=403)

    report = session.get(Report, report_id)
    if not report:
        return JSONResponse({"error": "Report not found."}, status_code=404)

    if report.analysis_run_at is None:
        return JSONResponse(
            {"error": "Run the AI analysis first -- there's nothing to base a suggestion on yet."},
            status_code=400,
        )

    suggestion, error = _suggest_summary_from_ai(
        report,
        report.model1_label,
        report.model2_label,
        report.model3_severity,
        report.condition_score,
        report.condition_rating,
        report.crack_length_cm,
        report.crack_width_cm,
        report.crack_length_px,
        report.crack_width_px,
    )
    if error:
        return JSONResponse({"error": error}, status_code=502)

    return JSONResponse({"suggestion": suggestion})


@router.post("/reports/{report_id}/suggest-treatment")
def suggest_treatment(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """AJAX endpoint behind the "Suggest with AI" button on the Report
    Review page. Always returns JSON, never a redirect or an HTML page --
    see static/js/suggest-treatment.js for the button logic, and
    PROJECT_LOG.md section 88 for the full story of this feature."""
    if not require_engineer(user):
        return JSONResponse({"error": "Not authorized."}, status_code=403)

    report = session.get(Report, report_id)
    if not report:
        return JSONResponse({"error": "Report not found."}, status_code=404)

    if report.analysis_run_at is None:
        return JSONResponse(
            {"error": "Run the AI analysis first -- there's nothing to base a suggestion on yet."},
            status_code=400,
        )

    suggestion, error = _suggest_treatment_from_ai(
        report,
        report.model2_label,
        report.model3_severity,
        report.condition_score,
        report.condition_rating,
        report.crack_length_cm,
        report.crack_width_cm,
        report.crack_length_px,
        report.crack_width_px,
    )
    if error:
        return JSONResponse({"error": error}, status_code=502)

    return JSONResponse({"suggestion": suggestion})


@router.post("/reports/{report_id}/suggest-cause")
def suggest_cause(
    report_id: int,
    request: Request,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Same pattern as suggest_treatment above, just for the Cause field --
    see PROJECT_LOG.md section 91 and static/js/suggest-treatment.js (reused
    for both buttons, see that file's own note)."""
    if not require_engineer(user):
        return JSONResponse({"error": "Not authorized."}, status_code=403)

    report = session.get(Report, report_id)
    if not report:
        return JSONResponse({"error": "Report not found."}, status_code=404)

    if report.analysis_run_at is None:
        return JSONResponse(
            {"error": "Run the AI analysis first -- there's nothing to base a suggestion on yet."},
            status_code=400,
        )

    suggestion, error = _suggest_cause_from_ai(
        report,
        report.model2_label,
        report.model3_severity,
        report.condition_score,
        report.condition_rating,
        report.crack_length_cm,
        report.crack_width_cm,
        report.crack_length_px,
        report.crack_width_px,
    )
    if error:
        return JSONResponse({"error": error}, status_code=502)

    return JSONResponse({"suggestion": suggestion})


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
     severity_label, severity_confidence, annotated_filename,
     condition_score, condition_rating,
     crack_length_cm, crack_width_cm,
     crack_length_px, crack_width_px, crack_area_px) = _run_analysis(report)

    report.model1_label = ai_label
    report.model1_confidence = ai_confidence
    report.model2_label = type_label
    report.model2_confidence = type_confidence
    report.model3_severity = severity_label
    report.model3_confidence = severity_confidence
    report.model3_annotated_photo_path = annotated_filename
    report.condition_score = condition_score
    report.condition_rating = condition_rating
    report.crack_length_cm = crack_length_cm
    report.crack_width_cm = crack_width_cm
    report.crack_length_px = crack_length_px
    report.crack_width_px = crack_width_px
    report.crack_area_px = crack_area_px
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
    engineer_cause: str = Form(""),
    treatment: str = Form(""),
    ai_summary: str = Form(""),
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
    report.engineer_cause = engineer_cause.strip() or None
    report.treatment = treatment.strip() or None
    report.ai_summary = ai_summary.strip() or None
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
    original_severity_confidence = report.model3_confidence if report.analysis_run_at else None

    return templates.TemplateResponse(
        request,
        "follow_up_scan.html",
        {
            "user": user,
            "report": report,
            "original_label": original_label,
            "original_confidence": original_confidence,
            "original_severity": original_severity,
            "original_severity_confidence": original_severity_confidence,
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
