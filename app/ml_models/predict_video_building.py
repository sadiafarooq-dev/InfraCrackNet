# Building Video Pipeline -- runs Model 3 across a whole drone/walkthrough
# video of a BUILDING and picks ONE result to save on the report.
#
# Plain-language version: this is the same idea already proven and tested in
# Data/scripts/test_video_pipeline_5_with_location.py (see PROJECT_LOG.md
# sections 66 and 67) -- now wired into the real app instead of living only
# in a standalone test script. Nothing new was trained here. It's:
#
#   1. Model 3 (predict_severity, already trained -- see predict_severity.py)
#      run on frames sampled across the video, same as every test before it.
#   2. A plain, disclosed TIMING-BASED GUESS of which floor/height each frame
#      was taken at (assumes the drone climbs steadily from ground to roof
#      over the video's length) -- not a measurement, not a model. See the
#      research summary in PROJECT_LOG.md section 67 for why no real "geo
#      model" can do this instead.
#   3. A "safe zone" filter that ignores detections too close to ground
#      level or the roof -- added after section 66 found the trained model
#      mistaking a balcony drainage bracket for a crack right at roof level.
#      Frames outside the safe zone are never trusted, even if Model 3
#      thinks it found something there.
#
# This ROUTES version differs from the test script in exactly one way: the
# test script prints every frame's result to the screen so a person can
# read through the whole video's frame-by-frame behaviour. This version has
# no screen to print to -- it needs to hand ONE result back to the New
# Report wizard, so it picks the single most severe TRUSTED frame (or, if
# none was trusted, a plain "no confident crack found" result) and saves
# that one frame + its outline permanently. Every other frame it looks at
# along the way is a temporary file, deleted before this function returns.
#
# Roads are NOT handled here on purpose -- a road has no floors to climb, so
# this whole floor/height idea doesn't apply to it (see
# Data/scripts/test_video_pipeline_road_1.py's own comment on this). Road
# video support is intentionally left for later, once a real road-depicting
# demo video exists to test it against (see PROJECT_LOG.md section 67's
# "what's left" list).

import os
import shutil
import tempfile
import uuid

import cv2

from ml_models.predict_severity import predict_severity, model_file_exists

# How often to sample the video. 1 frame per second is a reasonable
# trade-off for a real upload (a 30-second walkthrough is 30 Model 3 calls,
# not 60+) -- the test scripts used a tighter 0.5s interval since they were
# only ever run once by hand to inspect the results closely.
FRAME_INTERVAL_SECONDS = 1.0

# Same disclosed assumptions as test_video_pipeline_5_with_location.py --
# not measured, not trained, just a normal typical story height and a
# one-floor safety buffer at the ground and the roof.
FLOOR_HEIGHT_METERS = 3.0
GROUND_BUFFER_FLOORS = 1.0
ROOF_BUFFER_FLOORS = 1.0

SEVERITY_RANK = {"Low": 1, "Medium": 2, "High": 3}


def _estimate_floor(timestamp, duration, total_floors):
    """Same honest, disclosed timing-based guess as the test scripts:
    assumes a steady climb from ground floor to roof across the whole
    video. Not a measurement -- a plain assumption, disclosed to the user
    everywhere this number is shown."""
    if duration <= 0:
        return 0.0
    fraction = min(max(timestamp / duration, 0.0), 1.0)
    return fraction * total_floors


def _in_safe_zone(floor_estimate, total_floors):
    return GROUND_BUFFER_FLOORS <= floor_estimate <= (total_floors - ROOF_BUFFER_FLOORS)


def _estimate_height_meters(floor_estimate):
    return floor_estimate * FLOOR_HEIGHT_METERS


def analyze_building_video(video_path: str, total_floors: int, upload_dir: str):
    """
    Looks through a building video and returns ONE result for the report:

        {
            "photo_path": "<uuid>.jpg",                       # saved in upload_dir
            "model3_severity": "Low"/"Medium"/"High"/"No crack shape detected"/None,
            "model3_confidence": float (0-100) or None,        # see predict_severity.py
            "model3_annotated_photo_path": "<uuid>_outline.jpg" or None,
            "estimated_floor": float or None,
            "estimated_height_meters": float or None,
            "crack_length_px": int or None,   # pixel-only, see PROJECT_LOG.md section 75
            "crack_width_px": int or None,
            "crack_area_px": int or None,
            "frames_checked": int,
        }

    model3_severity is None only if the trained Model 3 file isn't in place
    at all (same meaning as predict_severity's own None return) -- the
    frame is still saved so the report has a photo either way.

    Returns None only if the video file itself couldn't be opened (not a
    valid video, wrong format, etc) -- the caller should treat this as an
    upload error and not create a report.
    """
    if not os.path.exists(video_path):
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    total_frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = (total_frame_count / fps) if fps else 0
    frame_step = max(1, round(fps * FRAME_INTERVAL_SECONDS))

    model3_available = model_file_exists()

    temp_dir = tempfile.mkdtemp(prefix="infracracknet_video_")
    try:
        frame_index = 0
        saved_index = 0
        # Every sampled frame that stayed in the safe zone -- used as a
        # fallback representative frame if nothing trusted is ever found.
        safe_zone_frames = []
        # Every frame where Model 3 found a confident shape AND it was in
        # the safe zone -- the pool this function actually picks its one
        # answer from.
        trusted_hits = []

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if frame_index % frame_step == 0:
                saved_index += 1
                timestamp = frame_index / fps
                floor_estimate = _estimate_floor(timestamp, duration, total_floors)
                height_estimate = _estimate_height_meters(floor_estimate)
                safe = _in_safe_zone(floor_estimate, total_floors)

                frame_path = os.path.join(temp_dir, f"frame_{saved_index:04d}.jpg")
                cv2.imwrite(frame_path, frame)

                record = {
                    "frame_path": frame_path,
                    "timestamp": timestamp,
                    "floor_estimate": floor_estimate,
                    "height_estimate": height_estimate,
                }

                if safe:
                    safe_zone_frames.append(record)

                if model3_available and safe:
                    annotated_path = os.path.join(temp_dir, f"frame_{saved_index:04d}_outline.jpg")
                    # detect_marker=False -- real cm measurements
                    # (PROJECT_LOG.md section 72) are scoped to single photo
                    # reports only, not video frames (no realistic way to
                    # expect a printed marker to stay in frame here, and this
                    # runs once per sampled frame, not once per video). The
                    # pixel-only values (section 75) still come through,
                    # since those never needed a marker.
                    (severity_label, annotated_saved, confidence, _, _,
                     crack_length_px, crack_width_px, crack_area_px) = predict_severity(
                        frame_path, annotated_path, detect_marker=False
                    )
                    has_shape = severity_label not in (None, "No crack shape detected", "Analysis error")
                    if has_shape:
                        trusted_hits.append({
                            **record,
                            "severity_label": severity_label,
                            "confidence": confidence,
                            "annotated_path": annotated_path if annotated_saved else None,
                            "crack_length_px": crack_length_px,
                            "crack_width_px": crack_width_px,
                            "crack_area_px": crack_area_px,
                        })

            frame_index += 1

        cap.release()
        frames_checked = saved_index

        if trusted_hits:
            # Most severe first; earliest timestamp breaks a tie, so the
            # result is stable rather than picking arbitrarily.
            best = sorted(
                trusted_hits,
                key=lambda r: (-SEVERITY_RANK.get(r["severity_label"], 0), r["timestamp"]),
            )[0]

            photo_filename = f"{uuid.uuid4().hex}.jpg"
            shutil.copyfile(best["frame_path"], os.path.join(upload_dir, photo_filename))

            annotated_filename = None
            if best["annotated_path"] and os.path.exists(best["annotated_path"]):
                annotated_filename = f"{uuid.uuid4().hex}_outline.jpg"
                shutil.copyfile(best["annotated_path"], os.path.join(upload_dir, annotated_filename))

            return {
                "photo_path": photo_filename,
                "model3_severity": best["severity_label"],
                "model3_confidence": best["confidence"],
                "model3_annotated_photo_path": annotated_filename,
                "estimated_floor": round(best["floor_estimate"], 1),
                "estimated_height_meters": round(best["height_estimate"], 1),
                "crack_length_px": best["crack_length_px"],
                "crack_width_px": best["crack_width_px"],
                "crack_area_px": best["crack_area_px"],
                "frames_checked": frames_checked,
            }

        # No trusted crack shape anywhere in the safe zone -- a real, honest
        # result (same idea as predict_severity's own "No crack shape
        # detected"), not an error. Save a representative frame from the
        # middle of the safe zone so the report still has a real photo from
        # the video, instead of no image at all.
        representative_pool = safe_zone_frames or [{
            "frame_path": os.path.join(temp_dir, "frame_0001.jpg"),
            "timestamp": 0,
            "floor_estimate": None,
            "height_estimate": None,
        }]
        middle = representative_pool[len(representative_pool) // 2]

        photo_filename = f"{uuid.uuid4().hex}.jpg"
        if os.path.exists(middle["frame_path"]):
            shutil.copyfile(middle["frame_path"], os.path.join(upload_dir, photo_filename))
        else:
            # Genuinely no frames were ever read from the video (a 0-length
            # or unreadable file) -- can't save a photo that doesn't exist.
            return None

        return {
            "photo_path": photo_filename,
            "model3_severity": "No crack shape detected" if model3_available else None,
            "model3_confidence": None,
            "model3_annotated_photo_path": None,
            "estimated_floor": (
                round(middle["floor_estimate"], 1) if middle["floor_estimate"] is not None else None
            ),
            "estimated_height_meters": (
                round(middle["height_estimate"], 1) if middle["height_estimate"] is not None else None
            ),
            # No traced crack shape anywhere trusted in this video (that's
            # exactly why this branch was reached), so nothing to measure
            # in pixels either -- same reasoning as model3_severity being
            # "No crack shape detected" here, not a separate limitation.
            "crack_length_px": None,
            "crack_width_px": None,
            "crack_area_px": None,
            "frames_checked": frames_checked,
        }

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
