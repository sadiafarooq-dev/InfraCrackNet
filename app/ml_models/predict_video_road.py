# Road Video Pipeline -- runs Model 2 (crack TYPE) across a whole dashcam/
# car-mounted-camera video of a ROAD and picks ONE frame, with labeled boxes
# drawn on it, to save on the report.
#
# Plain-language version: this is the road-side counterpart to
# predict_video_building.py (see PROJECT_LOG.md sections 66-68 for how that
# one came about). It follows the same basic shape -- sample frames across
# the video, run a trained model on each one, pick the single best result --
# but the actual AI step and the "which frame wins" rule are both different,
# because roads and buildings need different things:
#
#   - Buildings needed Model 3 (crack shape/severity) plus an honest guess
#     of which FLOOR each frame was taken at, since a building is tall and
#     the drone/walkthrough climbs it.
#   - A road is flat -- there's no floor to guess. What a road video needs
#     instead is what inspired this feature in the first place (see the
#     vialytics demo video discussion in PROJECT_LOG.md): labeled boxes
#     drawn on the frame, showing every road-damage TYPE Model 2 found
#     (pothole, alligator crack, longitudinal crack, etc), the same way a
#     real road-survey tool would show it.
#
# What this function actually does, frame by frame:
#   1. Model 2 (predict_crack_type_boxes, already trained -- see
#      predict_type.py) runs on the frame, returning EVERY confident
#      detection it finds, each with its own label, confidence, and pixel
#      box -- not just the single top one.
#   2. Whichever frame contains the single highest-confidence detection,
#      across the whole video, is picked as the "winning" frame -- a plain,
#      disclosed rule (not trained, not a guess): the moment the AI was most
#      sure about something is judged the clearest, most useful frame to
#      show. Ties are broken by whichever frame came first.
#   3. On that one winning frame, Model 3 (predict_severity, the same
#      severity/outline model every Road photo report already uses) is also
#      run, so a road video report ends up with the same two things a normal
#      Road photo report has -- Crack Type AND Severity -- not just boxes.
#   4. A copy of the winning frame with every detection's box and label
#      drawn on it is saved (model2_annotated_photo_path), alongside the
#      plain unmarked frame (photo_path) and Model 3's traced-outline copy
#      (model3_annotated_photo_path) -- three images total, each showing a
#      different part of the analysis, same idea as the building pipeline
#      saving both a plain frame and an outlined one.
#
# If no frame anywhere in the video has any confident detection, that's
# shown honestly (same as every other "the AI didn't find anything
# confident" result in this project) -- a representative frame from partway
# through the video is still saved, just with no boxes.

import os
import shutil
import tempfile
import uuid

import cv2

from ml_models.predict_type import predict_crack_type_boxes, model_file_exists as model2_file_exists
from ml_models.predict_severity import predict_severity

# Same sampling rate as the building pipeline -- once a second is a
# reasonable trade-off between catching real damage and not re-running the
# model dozens of extra times on a real upload.
FRAME_INTERVAL_SECONDS = 1.0

# Colors used to draw each detection's box -- BGR (OpenCV's order), cycled
# through if a frame has more detection types than colors. Chosen to be
# easy to tell apart, echoing the multi-color box style from the vialytics
# demo video that first inspired this feature.
BOX_COLORS = [
    (44, 111, 231),   # orange -- this app's own accent color
    (231, 189, 44),   # teal
    (170, 90, 200),   # pink/purple
    (90, 200, 120),   # green
]


def _draw_boxes(frame, detections, output_path):
    """Draws every detection's box + "Label 82%" caption on a copy of the
    frame and saves it. Plain OpenCV drawing -- no AI involved in this
    function, just visualizing what Model 2 already found."""
    annotated = frame.copy()
    label_colors = {}
    next_color = 0
    for det in detections:
        if det["label"] not in label_colors:
            label_colors[det["label"]] = BOX_COLORS[next_color % len(BOX_COLORS)]
            next_color += 1
        color = label_colors[det["label"]]
        x1, y1, x2, y2 = [int(round(v)) for v in det["box"]]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

        caption = f'{det["label"]} {det["confidence"]:.0f}%'
        (text_w, text_h), _ = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        label_y = y1 - 6 if y1 - 6 > text_h else y1 + text_h + 6
        cv2.rectangle(annotated, (x1, label_y - text_h - 4), (x1 + text_w + 6, label_y + 2), color, -1)
        cv2.putText(annotated, caption, (x1 + 3, label_y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

    return bool(cv2.imwrite(output_path, annotated))


def analyze_road_video(video_path: str, upload_dir: str):
    """
    Looks through a road video and returns ONE result for the report:

        {
            "photo_path": "<uuid>.jpg",                         # plain frame, saved in upload_dir
            "model2_label": "Alligator Crack"/"No damage type detected"/None,
            "model2_confidence": float (0-100) or None,
            "model2_annotated_photo_path": "<uuid>_boxes.jpg" or None,
            "model2_detections": [ {"label", "confidence", "box"}, ... ],  # every detection in the winning frame
            "model3_severity": "Low"/"Medium"/"High"/"No crack shape detected"/None,
            "model3_confidence": float (0-100) or None,
            "model3_annotated_photo_path": "<uuid>_outline.jpg" or None,
            "crack_length_px": int or None,   # pixel-only, see PROJECT_LOG.md section 75
            "crack_width_px": int or None,
            "crack_area_px": int or None,
            "frames_checked": int,
        }

    model2_label is None only if Model 2's trained file isn't in place at
    all (same meaning as predict_crack_type's own None return). The frame
    is still saved either way, so the report always has a photo.

    Returns None only if the video file itself couldn't be opened (not a
    valid video, wrong format, etc) -- the caller should treat this as an
    upload error and not create a report, same as the building pipeline.
    """
    if not os.path.exists(video_path):
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None

    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    frame_step = max(1, round(fps * FRAME_INTERVAL_SECONDS))

    model2_available = model2_file_exists()

    temp_dir = tempfile.mkdtemp(prefix="infracracknet_roadvideo_")
    try:
        frame_index = 0
        saved_index = 0
        # Every sampled frame, kept so a representative one can be picked if
        # nothing is ever confidently detected.
        all_frames = []
        # Every sampled frame where Model 2 found at least one confident
        # detection -- the pool this function picks its one "winning" frame
        # from.
        detected_frames = []

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if frame_index % frame_step == 0:
                saved_index += 1
                timestamp = frame_index / fps
                frame_path = os.path.join(temp_dir, f"frame_{saved_index:04d}.jpg")
                cv2.imwrite(frame_path, frame)

                record = {"frame_path": frame_path, "timestamp": timestamp}
                all_frames.append(record)

                if model2_available:
                    detections = predict_crack_type_boxes(frame_path)
                    if detections:
                        detected_frames.append({**record, "detections": detections})

            frame_index += 1

        cap.release()
        frames_checked = saved_index

        if detected_frames:
            # The frame containing the single highest-confidence detection
            # wins -- earliest timestamp breaks a tie, so the result is
            # stable rather than picking arbitrarily (same tie-break idea
            # as the building pipeline's severity ranking).
            best = sorted(
                detected_frames,
                key=lambda r: (-r["detections"][0]["confidence"], r["timestamp"]),
            )[0]
            top_detection = best["detections"][0]

            frame_image = cv2.imread(best["frame_path"])

            photo_filename = f"{uuid.uuid4().hex}.jpg"
            shutil.copyfile(best["frame_path"], os.path.join(upload_dir, photo_filename))

            boxes_filename = f"{uuid.uuid4().hex}_boxes.jpg"
            boxes_full_path = os.path.join(upload_dir, boxes_filename)
            boxes_saved = frame_image is not None and _draw_boxes(frame_image, best["detections"], boxes_full_path)
            if not boxes_saved:
                boxes_filename = None

            # Model 3 (severity/outline) runs on the same winning frame, so
            # a road video report ends up with both halves a normal Road
            # photo report has -- Crack Type (Model 2, above) and Severity.
            severity_annotated_filename = f"{uuid.uuid4().hex}_outline.jpg"
            severity_annotated_full_path = os.path.join(upload_dir, severity_annotated_filename)
            # detect_marker=False -- real cm measurements (PROJECT_LOG.md
            # section 72) are scoped to single photo reports only, since
            # there's no realistic way to expect a printed marker to stay in
            # frame across a whole video. The two cm values are always None
            # here either way, so they're discarded -- but the pixel-only
            # values (PROJECT_LOG.md section 75) are kept, since those never
            # needed a marker in the first place.
            (severity_label, severity_saved, severity_confidence, _, _,
             crack_length_px, crack_width_px, crack_area_px) = predict_severity(
                best["frame_path"], severity_annotated_full_path, detect_marker=False
            )
            if not severity_saved:
                severity_annotated_filename = None

            return {
                "photo_path": photo_filename,
                "model2_label": top_detection["label"],
                "model2_confidence": top_detection["confidence"],
                "model2_annotated_photo_path": boxes_filename,
                "model2_detections": best["detections"],
                "model3_severity": severity_label,
                "model3_confidence": severity_confidence,
                "model3_annotated_photo_path": severity_annotated_filename,
                "crack_length_px": crack_length_px,
                "crack_width_px": crack_width_px,
                "crack_area_px": crack_area_px,
                "frames_checked": frames_checked,
            }

        # No confident detection anywhere in the video -- a real, honest
        # result (same idea as predict_crack_type's own "No damage type
        # detected"), not an error. Save a representative frame from the
        # middle of the video so the report still has a real photo from the
        # video, instead of no image at all.
        representative_pool = all_frames or [{
            "frame_path": os.path.join(temp_dir, "frame_0001.jpg"),
            "timestamp": 0,
        }]
        middle = representative_pool[len(representative_pool) // 2]

        photo_filename = f"{uuid.uuid4().hex}.jpg"
        if os.path.exists(middle["frame_path"]):
            shutil.copyfile(middle["frame_path"], os.path.join(upload_dir, photo_filename))
        else:
            # Genuinely no frames were ever read from the video (a 0-length
            # or unreadable file) -- can't save a photo that doesn't exist.
            return None

        # Model 3 still gets a chance to give a severity opinion on the
        # representative frame, even though Model 2 found no damage type --
        # the two models are independent, so one finding nothing doesn't
        # mean the other will too.
        severity_annotated_filename = f"{uuid.uuid4().hex}_outline.jpg"
        severity_annotated_full_path = os.path.join(upload_dir, severity_annotated_filename)
        # detect_marker=False -- same scope decision as above (real cm
        # measurements are photo-only, see PROJECT_LOG.md section 72). The
        # pixel-only values (section 75) still come through when there's a
        # traced shape to measure, same as the winning-frame branch above.
        (severity_label, severity_saved, severity_confidence, _, _,
         crack_length_px, crack_width_px, crack_area_px) = predict_severity(
            middle["frame_path"], severity_annotated_full_path, detect_marker=False
        ) if os.path.exists(middle["frame_path"]) else (None, False, None, None, None, None, None, None)
        if not severity_saved:
            severity_annotated_filename = None

        return {
            "photo_path": photo_filename,
            "model2_label": "No damage type detected" if model2_available else None,
            "model2_confidence": None,
            "model2_annotated_photo_path": None,
            "model2_detections": [],
            "model3_severity": severity_label,
            "model3_confidence": severity_confidence,
            "model3_annotated_photo_path": severity_annotated_filename,
            "crack_length_px": crack_length_px,
            "crack_width_px": crack_width_px,
            "crack_area_px": crack_area_px,
            "frames_checked": frames_checked,
        }

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
