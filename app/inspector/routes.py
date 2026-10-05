# Inspector routes
import json
import os
import shutil
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from auth.utils import get_current_user
from database import get_session
from models.report import Report
from models.user import User
from models.audit_log import log_action
from models.project import Project, ProjectInspector
from ml_models.predict import predict_crack_presence
from ml_models.predict_severity import predict_severity
from ml_models.predict_video_building import analyze_building_video
from ml_models.predict_video_road import analyze_road_video
from condition_score import compute_condition_score
from exif_location import extract_gps
from time_utils import register_localtime
from text_utils import register_text_helpers
from asset_version import register_asset_version

router = APIRouter(prefix="/inspector")
templates = Jinja2Templates(directory=["templates", "inspector/templates"])
register_localtime(templates)
register_text_helpers(templates)
register_asset_version(templates)

UPLOAD_DIR = os.path.join("..", "uploads")

# Photo or video, both accepted on the Upload step -- see VIDEO_EXTENSIONS
# below for how a video is told apart from a photo. Which VIDEO PIPELINE
# runs (road-with-boxes vs building-with-floors) is decided by the
# "video_surface_type" radio choice on that same step (see
# new_report_upload_submit below and PROJECT_LOG.md section 70).
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm"}

# An Inspector can add several PHOTOS to one report (a video always goes on
# its own). Up to this many photos per report -- each one is checked by the AI
# later, so the limit keeps a single report quick to analyze.
MAX_PHOTOS_PER_REPORT = 6

# Every draft field that only ever applies to ONE of the three upload kinds
# (plain photo / Building video / Road video) -- cleared out on every fresh
# upload before the new kind's own fields are set, so an earlier draft's
# leftovers (e.g. switching from a Building video back to a photo mid-wizard)
# never leak into the new report.
VIDEO_ONLY_DRAFT_FIELDS = (
    "video_path", "total_floors", "estimated_floor", "estimated_height_meters",
    "model2_label", "model2_confidence", "model2_annotated_photo_path", "model2_video_detections",
    "model3_severity", "model3_confidence", "model3_annotated_photo_path",
    "condition_score", "condition_rating",
    # Pixel-only crack measurements (PROJECT_LOG.md section 75) -- only set
    # here for a VIDEO upload (see new_report_upload_submit below); a photo
    # report gets these later, at the Engineer's "Run Analysis" step, same
    # as model3_severity above.
    "crack_length_px", "crack_width_px", "crack_area_px",
    "analysis_run_at",
    # Every frame where a crack was actually flagged during video analysis
    # (the "See All Frames" feature) -- see models/report.py's
    # video_all_frames_json for the full explanation.
    "video_all_frames_json",
)


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
    project_id: Optional[int] = None,
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    reports = session.exec(
        select(Report).where(Report.inspector_id == user.id).order_by(Report.created_at.desc())
    ).all()

    if project_id:
        reports = [r for r in reports if r.project_id == project_id]

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
    return RedirectResponse("/inspector/reports/new/project", status_code=303)


def _assigned_projects(session: Session, inspector_id: int):
    """Every Project this Inspector has been assigned to by Admin (see
    admin/routes.py), name-sorted -- the only ones they're allowed to pick
    from on the new Select Project step below."""
    project_ids = [
        row.project_id for row in session.exec(
            select(ProjectInspector).where(ProjectInspector.user_id == inspector_id)
        ).all()
    ]
    if not project_ids:
        return []
    return session.exec(
        select(Project).where(Project.id.in_(project_ids)).order_by(Project.name)
    ).all()


@router.get("/reports/new/project")
def new_report_project_page(
    request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    projects = _assigned_projects(session, user.id)
    return templates.TemplateResponse(request, "new_report_project.html", {"user": user, "projects": projects})


@router.post("/reports/new/project")
def new_report_project_submit(
    request: Request,
    project_id: int = Form(...),
    user=Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    # Guard against someone posting a project_id they're not actually
    # assigned to (e.g. by editing the form) -- silently re-shows the same
    # step instead of trusting the raw form value.
    assigned_ids = {p.id for p in _assigned_projects(session, user.id)}
    if project_id not in assigned_ids:
        return RedirectResponse("/inspector/reports/new/project", status_code=303)

    project = session.get(Project, project_id)
    draft = _draft(request)
    draft["project_id"] = project_id
    draft["project_name"] = project.name if project else None
    _save_draft(request, draft)
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
    draft = _draft(request)
    if "project_id" not in draft:
        return RedirectResponse("/inspector/reports/new/project", status_code=303)
    return templates.TemplateResponse(
        request, "new_report_upload.html", {"user": user, "max_photos": MAX_PHOTOS_PER_REPORT}
    )


@router.post("/reports/new/upload")
def new_report_upload_submit(
    request: Request,
    photo: List[UploadFile] = File(...),
    video_surface_type: Optional[str] = Form(None),
    video_total_floors: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    if "project_id" not in _draft(request):
        return RedirectResponse("/inspector/reports/new/project", status_code=303)

    # One or more files can arrive under the same "photo" field. Either ONE
    # video, or one to MAX_PHOTOS_PER_REPORT photos -- never a mix.
    uploads = [u for u in photo if u and u.filename]
    upload_error = None
    if not uploads:
        upload_error = "Please choose a photo or a video."
    elif len(uploads) > 1 and any(
        os.path.splitext(u.filename)[1].lower() in VIDEO_EXTENSIONS for u in uploads
    ):
        upload_error = "A video has to be uploaded on its own. Choose either one video, or photos only."
    elif len(uploads) > MAX_PHOTOS_PER_REPORT:
        upload_error = f"You can add up to {MAX_PHOTOS_PER_REPORT} photos to one report."
    if upload_error:
        return templates.TemplateResponse(
            request,
            "new_report_upload.html",
            {"user": user, "error": upload_error, "max_photos": MAX_PHOTOS_PER_REPORT},
        )

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    saved_files = []   # (file name, full path on disk), in the order chosen
    for upload in uploads:
        upload_ext = os.path.splitext(upload.filename)[1].lower()
        upload_name = f"{uuid.uuid4().hex}{upload_ext}"
        upload_path = os.path.join(UPLOAD_DIR, upload_name)
        with open(upload_path, "wb") as f:
            shutil.copyfileobj(upload.file, f)
        saved_files.append((upload_name, upload_path))

    # The first file is the report's main photo (or its video); anything after
    # it is an extra photo of the same crack.
    filename, saved_path = saved_files[0]
    ext = os.path.splitext(filename)[1].lower()
    extra_photo_names = [name for name, _ in saved_files[1:]]

    draft = _draft(request)
    draft.pop("extra_photos", None)

    # Try to read the real GPS location saved inside the file ITSELF (see
    # exif_location.py) -- this is the fix for the location bug: it
    # captures where the photo/video was actually TAKEN, not where the
    # Inspector's device happens to be right now while uploading it. Runs
    # for every upload, photo or video (with several photos, the first one
    # that has a location saved in it is used). None is a completely normal
    # result (see exif_location.py's own notes on why) -- the Location step
    # falls back to the old device-location button when this comes back empty.
    exif_gps = None
    for _, path_on_disk in saved_files:
        exif_gps = extract_gps(path_on_disk)
        if exif_gps:
            break
    if exif_gps:
        draft["exif_latitude"], draft["exif_longitude"] = exif_gps
    else:
        draft["exif_latitude"] = None
        draft["exif_longitude"] = None

    if ext in VIDEO_EXTENSIONS:
        # A video upload runs one of two different pipelines depending on
        # what the Inspector said the video shows (the "video_surface_type"
        # radio on the Upload step) -- the actual model-running happens
        # right now, at upload time, because it means reading through the
        # whole video's frames -- there's no single "photo" to run it on
        # later the way the normal photo flow does. The RESULTS, though,
        # are kept hidden until an Engineer clicks "Run Analysis", same as
        # a photo report -- see the analysis_run_at note further down.
        # Defaults to "Road" if the field is somehow missing, since that's
        # the option that needs no extra input from the Inspector.
        surface_type = video_surface_type if video_surface_type in ("Road", "Building") else "Road"

        for key in VIDEO_ONLY_DRAFT_FIELDS:
            draft.pop(key, None)

        if surface_type == "Building":
            try:
                total_floors = int(video_total_floors) if video_total_floors else None
            except ValueError:
                total_floors = None
            if not total_floors or total_floors < 1:
                # Can't estimate a floor/height without a floor count -- send
                # the Inspector back with a clear, honest error instead of
                # guessing a number that was never provided.
                return templates.TemplateResponse(
                    request,
                    "new_report_upload.html",
                    {
                        "user": user,
                        "error": "For a building video, please enter how many floors the building has (at least 1) so we can estimate where each part of the video was taken.",
                    },
                )

            result = analyze_building_video(saved_path, total_floors, UPLOAD_DIR)
            # The uploaded video itself is kept as video_path either way, so
            # the Inspector/Engineer can still watch the original footage
            # even though photo_path below points at the one frame the
            # pipeline picked.
            if result is None:
                return templates.TemplateResponse(
                    request,
                    "new_report_upload.html",
                    {
                        "user": user,
                        "error": "That video file couldn't be read -- it may be corrupted or in an unsupported format. Please try a different file.",
                    },
                )

            draft["source_type"] = "video"
            draft["surface_type"] = "Building"
            draft["video_path"] = filename
            draft["photo_path"] = result["photo_path"]
            draft["total_floors"] = total_floors
            draft["estimated_floor"] = result["estimated_floor"]
            draft["estimated_height_meters"] = result["estimated_height_meters"]
            draft["model3_severity"] = result["model3_severity"]
            draft["model3_confidence"] = result["model3_confidence"]
            draft["model3_annotated_photo_path"] = result["model3_annotated_photo_path"]
            draft["crack_length_px"] = result["crack_length_px"]
            draft["crack_width_px"] = result["crack_width_px"]
            draft["crack_area_px"] = result["crack_area_px"]
            # Every trusted frame the pipeline flagged, not just the one
            # winning frame above -- powers the "See All Frames" gallery
            # (see models/report.py's video_all_frames_json).
            draft["video_all_frames_json"] = json.dumps(result["all_frames"])
        else:
            # Road video -- the new pipeline from PROJECT_LOG.md section 70:
            # Model 2 runs on frames sampled across the video and keeps
            # EVERY confident detection (not just the top one), and the
            # frame with the single most confident detection wins, with its
            # boxes drawn on. Model 3 also runs on that same winning frame,
            # for the same Severity a normal Road photo report gets.
            result = analyze_road_video(saved_path, UPLOAD_DIR)
            if result is None:
                return templates.TemplateResponse(
                    request,
                    "new_report_upload.html",
                    {
                        "user": user,
                        "error": "That video file couldn't be read -- it may be corrupted or in an unsupported format. Please try a different file.",
                    },
                )

            draft["source_type"] = "video"
            draft["surface_type"] = "Road"
            draft["video_path"] = filename
            draft["photo_path"] = result["photo_path"]
            draft["model2_label"] = result["model2_label"]
            draft["model2_confidence"] = result["model2_confidence"]
            draft["model2_annotated_photo_path"] = result["model2_annotated_photo_path"]
            # Stored as a JSON string in the draft/database -- see
            # models/report.py's own note on model2_video_detections for why.
            draft["model2_video_detections"] = json.dumps(result["model2_detections"])
            draft["model3_severity"] = result["model3_severity"]
            draft["model3_confidence"] = result["model3_confidence"]
            draft["model3_annotated_photo_path"] = result["model3_annotated_photo_path"]
            draft["crack_length_px"] = result["crack_length_px"]
            draft["crack_width_px"] = result["crack_width_px"]
            draft["crack_area_px"] = result["crack_area_px"]
            # PCI-inspired Condition Score (PROJECT_LOG.md section 71, see
            # condition_score.py) -- Road reports only, so computed right
            # here rather than in the Building branch above. None/None if
            # Model 3 didn't trace a real crack shape in the winning frame.
            draft["condition_score"], draft["condition_rating"] = compute_condition_score(
                result["model3_severity"], result["model2_label"]
            )
            # Every flagged frame the pipeline found, not just the one
            # winning frame above -- powers the "See All Frames" gallery
            # (see models/report.py's video_all_frames_json).
            draft["video_all_frames_json"] = json.dumps(result["all_frames"])

        # NOTE: analysis_run_at is deliberately NOT set here. The models
        # above already ran on the video right now, at upload time -- that
        # part can't be deferred, since there's no single "photo" to defer
        # analysis on the way the plain photo flow does. But whether those
        # results are actually SHOWN to anyone is a separate question, and
        # that's what analysis_run_at controls everywhere in the app (every
        # report-detail/review page checks "if report.analysis_run_at" and
        # hides the AI fields otherwise). Leaving it unset here means a
        # fresh video report lands with its results computed but hidden,
        # exactly like a fresh photo report -- an Engineer has to click
        # "Run Analysis" to reveal them (see engineer/routes.py's
        # _run_analysis, which for a video report just reveals these
        # already-computed values instead of re-running the models).
    else:
        draft["source_type"] = "photo"
        draft["photo_path"] = filename
        if extra_photo_names:
            draft["extra_photos"] = extra_photo_names
        # A photo report always falls back to the Report model's own "Road"
        # default (see models/report.py) -- clearing any leftover
        # "Building"/"Road" choice from an earlier video upload in this same
        # in-progress draft, so going back and swapping a video for a photo
        # can't leave a stale surface type behind.
        draft.pop("surface_type", None)
        for key in VIDEO_ONLY_DRAFT_FIELDS:
            draft.pop(key, None)

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
        request,
        "new_report_location.html",
        {
            "user": user,
            "location": draft.get("location", ""),
            "latitude": draft.get("latitude", ""),
            "longitude": draft.get("longitude", ""),
            # The real location read out of the uploaded file itself (see
            # exif_location.py), if any was found -- shown as the preferred,
            # trustworthy option. None/None if the file had no GPS saved in
            # it (a completely normal, expected case -- not an error).
            "exif_latitude": draft.get("exif_latitude"),
            "exif_longitude": draft.get("exif_longitude"),
        },
    )


@router.post("/reports/new/location")
def new_report_location_submit(
    request: Request,
    location: str = Form(...),
    latitude: Optional[str] = Form(None),
    longitude: Optional[str] = Form(None),
    location_source: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    draft = _draft(request)
    if "photo_path" not in draft:
        return RedirectResponse("/inspector/reports/new/upload", status_code=303)
    draft["location"] = location
    # Real GPS coordinates, from whichever source the Inspector actually
    # used on this step (see new_report_location.html + geolocation.js) --
    # blank/missing if they typed the address by hand instead, which is a
    # completely normal, expected case, not an error.
    try:
        draft["latitude"] = float(latitude) if latitude else None
        draft["longitude"] = float(longitude) if longitude else None
    except ValueError:
        draft["latitude"] = None
        draft["longitude"] = None
    # "exif" (from the file itself) / "device" (the browser's live GPS,
    # fallback-only) / None (typed by hand) -- see models/report.py's own
    # comment on this field for the full story.
    draft["location_source"] = location_source or None
    _save_draft(request, draft)
    return RedirectResponse("/inspector/reports/new/context", status_code=303)


def _cause_quality_nudge(cause):
    """A small, honest, non-blocking heuristic for the optional "Suspected
    cause" box on the New Report wizard's Context step. NOT an AI call --
    just a word count, so it's instant and needs no network. Leaving the
    box blank is always fine (it's genuinely optional, per its own "leave
    blank if you're not sure" placeholder) and is never nudged -- this only
    catches the case where something WAS typed but looks too brief to be
    useful to the Engineer later (a single word, a fragment). Returns a
    gentle message to show, or None if there's nothing to flag. See
    PROJECT_LOG.md (Report-Quality Nudge feature) for why this is
    non-blocking -- the Inspector can always submit again as-is."""
    cause = (cause or "").strip()
    if not cause:
        return None
    if len(cause.split()) < 4:
        return (
            "That's pretty brief -- a few more words (what you saw, or where) "
            "helps the Engineer later. Feel free to continue as-is if you're "
            "confident this is enough."
        )
    return None


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
    nudge_acknowledged: str = Form(""),
    user=Depends(get_current_user),
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)
    draft = _draft(request)
    if "location" not in draft:
        return RedirectResponse("/inspector/reports/new/location", status_code=303)

    cause = cause.strip()
    nudge_message = None if nudge_acknowledged else _cause_quality_nudge(cause)
    if nudge_message:
        # Non-blocking -- re-shows the same page with everything they
        # entered kept, plus the nudge and a hidden field that lets the
        # very same Continue button proceed on a second click. See
        # PROJECT_LOG.md (Report-Quality Nudge feature).
        return templates.TemplateResponse(
            request,
            "new_report_context.html",
            {
                "user": user,
                "structure_age": structure_age,
                "surface_condition": surface_condition,
                "actively_growing": actively_growing,
                "cause": cause,
                "nudge_message": nudge_message,
            },
        )

    draft["structure_age"] = structure_age
    draft["surface_condition"] = surface_condition
    draft["actively_growing"] = actively_growing
    draft["cause"] = cause
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

    # Stays None for every brand-new report, photo or video. A video report's
    # models already ran back in new_report_upload_submit (so its results are
    # sitting in the fields below, ready to go) but analysis_run_at itself is
    # what decides whether those results are actually shown anywhere -- and
    # that only happens once an Engineer clicks "Run Analysis". This keeps
    # video reports behaving exactly like photo reports from the Inspector's
    # and Engineer's point of view: nothing shows up until that click.
    analysis_run_at = (
        datetime.fromisoformat(draft["analysis_run_at"]) if draft.get("analysis_run_at") else None
    )

    report = Report(
        inspector_id=user.id,
        project_id=draft.get("project_id"),
        photo_path=draft["photo_path"],
        extra_photos_json=json.dumps(draft["extra_photos"]) if draft.get("extra_photos") else None,
        surface_type=draft.get("surface_type", "Road"),
        source_type=draft.get("source_type", "photo"),
        video_path=draft.get("video_path"),
        location=draft["location"],
        latitude=draft.get("latitude"),
        longitude=draft.get("longitude"),
        location_source=draft.get("location_source"),
        structure_age=draft["structure_age"],
        surface_condition=draft["surface_condition"],
        actively_growing=draft["actively_growing"],
        cause=draft.get("cause") or None,
        total_floors=draft.get("total_floors"),
        estimated_floor=draft.get("estimated_floor"),
        estimated_height_meters=draft.get("estimated_height_meters"),
        model2_label=draft.get("model2_label"),
        model2_confidence=draft.get("model2_confidence"),
        model2_annotated_photo_path=draft.get("model2_annotated_photo_path"),
        model2_video_detections=draft.get("model2_video_detections"),
        model3_severity=draft.get("model3_severity"),
        model3_confidence=draft.get("model3_confidence"),
        model3_annotated_photo_path=draft.get("model3_annotated_photo_path"),
        condition_score=draft.get("condition_score"),
        condition_rating=draft.get("condition_rating"),
        crack_length_px=draft.get("crack_length_px"),
        crack_width_px=draft.get("crack_width_px"),
        crack_area_px=draft.get("crack_area_px"),
        analysis_run_at=analysis_run_at,
        video_all_frames_json=draft.get("video_all_frames_json"),
    )
    session.add(report)
    session.flush()
    log_action(session, actor_name=user.name, action="Submitted Report",
               details=f"New report RPT-{report.id:04d} · {report.location}")
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
    severity_confidence = report.model3_confidence if analysis_done else None
    annotated_photo_path = report.model3_annotated_photo_path if analysis_done else None
    # Road video reports only -- the same frame as photo_path, with every
    # confident Model 2 detection drawn on as a labeled box (PROJECT_LOG.md
    # section 70). None for a photo report or a Building video report.
    type_boxes_photo_path = report.model2_annotated_photo_path if analysis_done else None
    # Every detection Model 2 found in that frame, turned back from the
    # stored JSON string into a real list -- [] / None if there isn't one.
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
    # Every flagged video frame, for the "See All Frames" gallery -- see
    # models/report.py's video_all_frames_json. [] / None for a photo
    # report, or an older video report submitted before this feature
    # existed.
    all_frames = (
        json.loads(report.video_all_frames_json) if report.video_all_frames_json else None
    )

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
            "all_frames": all_frames,
            "reviewer": reviewer,
            "follow_up_pending": follow_up_pending,
        },
    )


# Projects -- read-only for Inspector: just the Projects Admin has assigned
# them to, with how many of their own reports fall under each one. Picking a
# Project when starting a New Report (above) is the only real "use" of a
# Project an Inspector has; this page is mainly so they can see their own
# site list and jump into that site's reports.
@router.get("/projects")
def inspector_projects_page(
    request: Request, user=Depends(get_current_user), session: Session = Depends(get_session)
):
    if not require_inspector(user):
        return RedirectResponse("/login", status_code=303)

    projects = _assigned_projects(session, user.id)
    rows = []
    for p in projects:
        report_count = len(session.exec(
            select(Report).where(Report.inspector_id == user.id, Report.project_id == p.id)
        ).all())
        rows.append({"project": p, "report_count": report_count})

    return templates.TemplateResponse(request, "projects.html", {"user": user, "rows": rows})


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
    # detect_marker=False -- real cm measurements (PROJECT_LOG.md section 72)
    # are scoped to a report's main photo only in this round; the Report
    # model has no follow_up_length_cm/follow_up_width_cm (or pixel-only
    # follow_up_length_px/follow_up_width_px) fields to save them into yet,
    # so there's nowhere for any of these five trailing values to go even
    # if detected.
    severity_label, annotated_saved, severity_confidence, _, _, _, _, _ = predict_severity(
        follow_up_full_path, follow_up_annotated_full_path, detect_marker=False
    )
    if not annotated_saved:
        follow_up_annotated_filename = None

    report.follow_up_photo_path = filename
    report.follow_up_created_at = datetime.utcnow()
    report.follow_up_ai_label = ai_label
    report.follow_up_ai_confidence = ai_confidence
    report.follow_up_severity = severity_label
    report.follow_up_severity_confidence = severity_confidence
    report.follow_up_annotated_photo_path = follow_up_annotated_filename
    session.add(report)
    log_action(session, actor_name=user.name, action="Submitted Follow-up Photo",
               details=f"Submitted the requested follow-up photo for RPT-{report.id:04d}")
    session.commit()

    return RedirectResponse(f"/inspector/reports/{report_id}", status_code=303)
