# Report model
from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class Report(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    inspector_id: int = Field(foreign_key="user.id")
    # Which Project (site) this report belongs to -- picked by the Inspector
    # on the new "Select Project" step of the New Report wizard (see
    # inspector/routes.py). None for any report submitted before this feature
    # existed -- treated everywhere as a shared "Unassigned" report that every
    # Engineer can still see, rather than being hidden from everyone.
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    # For a video-based Building report, this is NOT the raw uploaded video
    # -- it's the single best frame our video pipeline picked out of it (see
    # video_path below for the actual uploaded video file). Every part of
    # the app that shows "the photo" keeps working unchanged either way.
    photo_path: str
    # Kept for backwards compatibility with older reports -- the rebuilt
    # New Report wizard (matching Figma) no longer asks for this, so new
    # reports just leave it as the default.
    surface_type: Optional[str] = "Road"
    # "photo" (the original, single-image flow) or "video" -- set the
    # moment a video is uploaded for a Building report (see
    # ml_models/predict_video_building.py + inspector/routes.py). Every
    # older/normal report is "photo".
    source_type: Optional[str] = "photo"
    # The actual uploaded video file, kept alongside the extracted photo_path
    # above so the Inspector/Engineer can still watch the original footage.
    # None for a normal photo report.
    video_path: Optional[str] = None
    location: str
    # Real GPS coordinates for this report, from one of two genuinely
    # different sources -- see location_source below for which one. None if
    # the Inspector typed the address by hand instead (no GPS in the file,
    # declined permission, older browser, etc.) -- `location` above always
    # has a value either way, this is just the extra, real GPS data when it
    # was available.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    # Where latitude/longitude actually came from -- added in the round
    # that fixed a real bug you found (PROJECT_LOG.md section 69):
    #   "exif"   -- read directly out of the photo/video file itself (see
    #               exif_location.py), i.e. where it was genuinely TAKEN.
    #               This is the trustworthy one.
    #   "device" -- the fallback: the browser's live GPS, asked for on the
    #               Location step (see static/js/geolocation.js). This is
    #               where the Inspector's DEVICE was at the moment they hit
    #               the button -- which can be a different place than where
    #               the photo/video was actually taken, if they upload
    #               later. Kept only as a fallback for files with no GPS
    #               saved inside them.
    #   None     -- the Inspector typed the address in by hand, with no GPS
    #               from either source.
    location_source: Optional[str] = None
    site_notes: Optional[str] = None

    # The 3 "on-site context" questions from the New Report wizard's
    # Context step (see inspector/routes.py) -- shown to the Engineer as
    # extra judgement context, never fed into the AI models themselves.
    structure_age: Optional[str] = None
    surface_condition: Optional[str] = None
    actively_growing: Optional[str] = None

    status: str = "Submitted"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Stamped the moment an Engineer opens it and it moves to "Under
    # Review" -- powers the Status Timeline on the Inspector's Report
    # Detail page.
    under_review_at: Optional[datetime] = None

    # AI Analysis results -- filled in only once an Engineer deliberately
    # clicks "Run Analysis" on the Report Review screen (see
    # engineer/routes.py), instead of being recomputed live every time
    # someone opens the report. None/None until that happens -- both the
    # Engineer's and Inspector's screens show "Pending analysis" until then.
    # model2_label/model2_confidence stay None for Building reports (Model 2
    # only ever looked at road photos -- see PROJECT_LOG.md section 17) even
    # after analysis has been run.
    analysis_run_at: Optional[datetime] = None
    model1_label: Optional[str] = None
    model1_confidence: Optional[float] = None
    model2_label: Optional[str] = None
    model2_confidence: Optional[float] = None
    # Model 3 -- Severity ("Low"/"Medium"/"High", "No crack shape detected",
    # or "Analysis error") + the saved filename of a copy of the photo with
    # the traced crack outline drawn on it (relative to the uploads folder,
    # same as photo_path). Runs on both Road and Building reports -- unlike
    # Model 2, Model 3 isn't gated to roads only (see PROJECT_LOG.md section
    # 17). (This used to also store a pixel-based "Length Estimate" -- removed
    # on request, since it was more confusing than useful even though it was
    # always honestly disclosed as not a real-world measurement.)
    model3_severity: Optional[str] = None
    # How confident Model 3 was in the crack shape(s) it traced (0-100).
    # This was always computed internally (see predict_severity.py's
    # MIN_CONFIDENCE cutoff) but used to be thrown away instead of saved --
    # added in the same round as location_source above, to fill the
    # "Confidence: --" gap this left on video-based Building reports (which
    # don't run Model 1, so had no confidence number showing anywhere).
    # None whenever model3_severity is "No crack shape detected", "Analysis
    # error", or None.
    model3_confidence: Optional[float] = None
    model3_annotated_photo_path: Optional[str] = None
    # A single 0-100 "PCI-inspired Condition Score" (plus the real PCI
    # rating label -- Good/Satisfactory/Fair/Poor/Very Poor/Serious/Failed)
    # computed from model3_severity and, for Road reports, model2_label --
    # see condition_score.py for the exact, disclosed formula and
    # PROJECT_LOG.md section 71 for why this is "PCI-inspired" and not the
    # real ASTM D6433 PCI. ROAD REPORTS ONLY (photo or Road video) -- PCI
    # is a pavement metric, so Buildings don't get one, same reasoning as
    # Model 2 being Road-only (section 17). Also None whenever there's no
    # traced crack shape to score (model3_severity is "No crack shape
    # detected", "Analysis error", or None) -- same "don't fabricate a
    # number" rule used everywhere else in this project.
    condition_score: Optional[int] = None
    condition_rating: Optional[str] = None
    # Real cm crack measurements, using a printed ArUco marker placed in the
    # photo as a scale reference (see aruco_measurement.py for the full
    # explanation and PROJECT_LOG.md section 72 for why this is genuinely a
    # real-world measurement this time, not a pixel-only estimate like the
    # earlier removed "Length Estimate" from section 61). PHOTO REPORTS
    # ONLY -- not computed for Building or Road videos, since there's no
    # realistic way to expect a printed marker to stay in frame across a
    # whole video (same reasoning style as condition_score being Road-only
    # above, just a different scope line). Also None whenever a photo
    # report's crack shape was found but no marker was detected in that
    # particular photo -- a normal, expected result (the marker just wasn't
    # in the shot), not an error, and never a fabricated number.
    crack_length_cm: Optional[float] = None
    crack_width_cm: Optional[float] = None
    # Pixel-based crack length/width/area -- brought back on request
    # (PROJECT_LOG.md section 75). NOT a real-world measurement -- these
    # are plain pixel counts, computed with the exact same shape math as
    # the real cm numbers above (see aruco_measurement.py's
    # measure_crack_pixels), just with no marker/scale conversion, so they
    # work even when no marker was found in the photo, or the report came
    # from a video. Computed for EVERY report with a traced crack shape
    # (model3_severity is "Low"/"Medium"/"High") -- an earlier, similar
    # pixel-only "Length Estimate" existed in this project before, and was
    # removed for being more confusing than useful (see the note on
    # model3_severity above); this version is shown clearly labeled as
    # pixel-only, alongside the real cm numbers rather than in their
    # place, which is the difference this time. None whenever there's no
    # traced crack shape at all (model3_severity is "No crack shape
    # detected", "Analysis error", or None), or for an older report
    # analyzed before this feature existed (re-run analysis to fill these
    # in -- see engineer/routes.py's "Re-run Analysis" button).
    crack_length_px: Optional[float] = None
    crack_width_px: Optional[float] = None
    crack_area_px: Optional[int] = None
    # Road VIDEO reports only (source_type == "video" and surface_type ==
    # "Road") -- added in the round that wired road videos into the app
    # (PROJECT_LOG.md section 70), the road-side counterpart to
    # model3_annotated_photo_path above. A copy of the report's photo_path
    # frame with every confident Model 2 detection drawn on it as a labeled
    # box (see ml_models/predict_video_road.py). None for photo reports and
    # for Building video reports, where there's no "type" to box.
    model2_annotated_photo_path: Optional[str] = None
    # Road video reports only -- EVERY confident detection Model 2 found in
    # the winning frame (not just the single one shown in model2_label/
    # model2_confidence above), stored as a JSON-encoded list of
    # {"label", "confidence", "box"} dicts so the report can also mention
    # any other damage types spotted in the same frame. None for photo
    # reports and Building video reports. (Stored as a JSON string rather
    # than a real list because the database column type this project uses
    # can only hold plain text/numbers -- inspector/routes.py and
    # engineer/routes.py are what turn it back into a real list to display.)
    model2_video_detections: Optional[str] = None
    # Video-based Building reports only (source_type == "video") -- the
    # number of floors the Inspector said this building has, plus the
    # honest, disclosed TIMING-BASED GUESS of which floor/height the saved
    # photo_path frame came from (steady-climb assumption -- see
    # ml_models/predict_video_building.py and PROJECT_LOG.md sections 66/67
    # for why this is a guess, not a measurement, and why there's no real
    # "geo model" that could do better here). All None for photo reports.
    total_floors: Optional[int] = None
    estimated_floor: Optional[float] = None
    estimated_height_meters: Optional[float] = None
    # Video reports only (Road or Building) -- EVERY frame where a crack was
    # actually flagged during video analysis, not just the single "winning"
    # frame saved above as photo_path. Stored as a JSON-encoded list of
    # {"path", "label", "confidence", "timestamp", "is_winner"} dicts, same
    # "text column, parsed back into a list to display" pattern as
    # model2_video_detections above. Powers the "See All Frames" gallery on
    # the Inspector's Report Detail page and the Engineer's Report Review
    # page. None for photo reports, and for any video report submitted
    # before this feature existed.
    video_all_frames_json: Optional[str] = None
    # More than one photo in the SAME report: the Inspector can pick 2, 3 or
    # more photos at once on the Upload step (up to 6). The FIRST photo is
    # stored in photo_path above, exactly as before, so every part of the app
    # that shows "the photo" keeps working unchanged. Any further photos are
    # stored here as a JSON list of file names, e.g. '["a1b2.jpg", "c3d4.jpg"]'
    # (see text_utils.report_photos for the one function that reads both).
    # None for a report with a single photo, for every video report, and for
    # every report submitted before this feature existed.
    extra_photos_json: Optional[str] = None
    # What the AI found in EACH photo of a report with several photos -- a JSON
    # list, one entry per photo, saved when an Engineer runs the analysis. The
    # photo with the MOST SERIOUS result is put first: it becomes photo_path,
    # and its results are the ones stored in the report-level model1_* /
    # model2_* / model3_* fields above (so every page that shows "the AI's
    # answer" keeps working unchanged). The others follow in the order the
    # Inspector took them. None for a single-photo report and for every video.
    photo_results_json: Optional[str] = None
    # The suspected cause of the crack, in the Inspector's own words -- an
    # optional field on the New Report wizard's Context step, since an
    # Inspector may not always know or want to guess at the cause. Shown to
    # the Engineer (read-only, for context) alongside their own separate
    # engineer_cause below -- added in the same round as engineer_cause,
    # section 91, once it turned out the Inspector's cause note wasn't being
    # shown to the Engineer at all before this.
    cause: Optional[str] = None
    # The Engineer's own determination of cause, filled in on the Report
    # Review screen -- kept as its OWN field rather than overwriting the
    # Inspector's cause above, so neither one is ever silently lost. Same
    # AI-suggestion pattern as treatment below (PROJECT_LOG.md section 91).
    engineer_cause: Optional[str] = None
    # The Engineer's proposed treatment/solution, filled in on the Report
    # Review screen alongside their remarks and status.
    treatment: Optional[str] = None
    # A short, plain-English summary of what the AI analysis found, written
    # so someone without an engineering background (an Inspector, or anyone
    # else reading the report) can understand it -- AI-suggested via a
    # "Suggest with AI" button on the Report Review screen, same Gemini-based
    # pattern as Cause and Treatment above, then reviewed/edited by the
    # Engineer and saved alongside their decision. None until the Engineer
    # generates and saves one.
    ai_summary: Optional[str] = None
    engineer_remarks: Optional[str] = None
    reviewed_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    reviewed_at: Optional[datetime] = None

    # Engineer's Follow-up Scan (see engineer/routes.py + inspector/routes.py)
    # -- a second, later photo of the same spot, to check whether the crack
    # has gotten worse. The Engineer REQUESTS a follow-up (this timestamp);
    # the Inspector is the one who actually takes/uploads the new photo, from
    # their own Report Detail page, once they see the request. One follow-up
    # round per report at a time (requesting again clears the previous one's
    # result and starts a fresh round), matching Figma's single before/after
    # design.
    follow_up_requested_at: Optional[datetime] = None
    follow_up_photo_path: Optional[str] = None
    follow_up_created_at: Optional[datetime] = None
    follow_up_ai_label: Optional[str] = None
    follow_up_ai_confidence: Optional[float] = None
    # Model 3 result for the follow-up photo, same meaning as model3_severity
    # above -- computed the moment the Inspector submits the follow-up photo
    # (that submission is itself the deliberate trigger, same as Model 1
    # already worked here before Model 3 existed).
    follow_up_severity: Optional[str] = None
    # Same meaning as model3_confidence above, for the follow-up photo.
    follow_up_severity_confidence: Optional[float] = None
    follow_up_annotated_photo_path: Optional[str] = None
    # The Engineer's call after reviewing the before/after comparison --
    # "Escalated" or "Keep Monitoring". None until they decide.
    follow_up_decision: Optional[str] = None

    # Engineer's verification of the AI's findings (Report Review, "Verify AI
    # Findings" step). The AI's own answers (model1_label, model2_label,
    # model3_severity above) are NEVER overwritten -- the Engineer's own call
    # on each is stored here, separately, so both can always be shown side by
    # side ("AI: Medium, Engineer: High"). None until the Engineer verifies.
    engineer_crack_presence: Optional[str] = None   # "Cracked" / "Uncracked"
    engineer_crack_type: Optional[str] = None       # Road only; a crack type or "None of these"
    engineer_severity: Optional[str] = None         # "Low" / "Medium" / "High"
    verification_note: Optional[str] = None
    verified_at: Optional[datetime] = None
