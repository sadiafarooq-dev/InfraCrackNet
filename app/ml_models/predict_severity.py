# Model 3 -- Crack Outline + Severity -- runs the trained model on one photo
#
# Plain-language version: this file loads the AI model we trained on Colab
# (see notebooks/train_crack_segmentation_round4_colab.ipynb and
# PROJECT_LOG.md section 59 for the final, accepted result) and gives the
# rest of the web app one simple function to call:
#
#     predict_severity("path/to/photo.jpg", "path/to/save/outline.jpg")
#         -> ("Medium", True, 78.4, 12.3, 0.8, 123, 15, 4820)
#
# Model 3 traces the crack's actual shape (not just a box, like Model 2's
# object-detection boxes) -- and we turn that traced shape into simple,
# real, NOT-trained numbers, exactly as planned back in the project's early
# design (PROJECT_LOG.md section 11):
#   - Severity ("Low"/"Medium"/"High") -- how much of the photo the traced
#     crack shape covers. The 1%/3% cutoffs below are a simple, disclosed,
#     rule-based choice (not researched or trained) -- easy to adjust later
#     if real-world testing suggests different numbers make more sense.
#   - crack_length_cm / crack_width_cm -- a REAL, physical measurement of
#     that same traced shape, in centimetres, using a printed ArUco marker
#     in the photo as a scale reference (see aruco_measurement.py and
#     PROJECT_LOG.md section 72). Both None unless a marker was genuinely
#     found in this specific photo -- never a fabricated guess.
#   - crack_length_px / crack_width_px / crack_area_px -- the brought-back
#     PIXEL-ONLY version of the same shape measurement (see
#     aruco_measurement.py's measure_crack_pixels and PROJECT_LOG.md
#     section 75), shown whenever there's a traced crack shape AT ALL, no
#     marker required -- so a video report or a photo with no marker in
#     frame still gets a real, honestly-labeled-as-pixel-only number
#     instead of nothing.
#
# (An earlier version of this file returned a pixel-based "Length Estimate"
# with no real-world scale reference, which was removed on request for
# being more confusing than useful even though it was always honestly
# disclosed as pixel-only. crack_length_px/crack_width_px/crack_area_px
# above are that same idea, brought back -- this time shown clearly
# labeled and alongside the real marker-based cm numbers rather than in
# their place, per PROJECT_LOG.md section 75.)
#
# Unlike Model 2 (roads only -- see PROJECT_LOG.md section 17), Model 3 runs
# on BOTH Road and Building photos -- it was trained on crack shapes from
# both, so there's no surface-type gating here.
#
# It only loads the model file once (the first time it's needed), then
# reuses it for every photo after that, so the web app stays fast.

import os

MODEL3_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "models", "model3_crack_segmentation",
    "crack_segmentation_round4_best.pt",
)

# A detection below this confidence is treated as "nothing found" rather
# than forced into a guess -- same honest cutoff idea as Model 2.
MIN_CONFIDENCE = 0.25

# How much of the photo the traced crack shape has to cover to count as
# Medium/High, instead of Low. Simple, disclosed, rule-based cutoffs -- not
# researched or trained on real data, just a reasonable first pass.
LOW_MAX_PERCENT = 1.0
MEDIUM_MAX_PERCENT = 3.0

_model = None


def model_file_exists() -> bool:
    return os.path.exists(MODEL3_PATH)


def _load_model():
    global _model
    if _model is not None:
        return _model

    # Imported here rather than at the top of the file, so the rest of the
    # app still runs even before `pip install -r requirements.txt` has been
    # re-run to pick up the `ultralytics`/`opencv-python`/`numpy` packages --
    # model_file_exists() is what the caller checks first either way.
    from ultralytics import YOLO

    _model = YOLO(MODEL3_PATH)
    return _model


def predict_severity(image_path: str, annotated_output_path: str = None, detect_marker: bool = True):
    """
    Traces the crack's shape in one photo and returns
    (severity_label, annotated_saved, confidence_percent, crack_length_cm,
    crack_width_cm, crack_length_px, crack_width_px, crack_area_px):

    - severity_label is one of:
        "Low" / "Medium" / "High"  -- a real, confident detection
        "No crack shape detected"  -- the model ran, but didn't find a
            confident crack shape in this particular photo. This is a real,
            honest result, not a placeholder -- with round 4's real recall
            at 43.6% (PROJECT_LOG.md section 59), the model is genuinely
            expected to miss the shape on more than half of truly cracked
            photos. Worth knowing, not a bug, if it happens often.
        "Analysis error" -- something unexpected went wrong while running
            the model on this specific photo (a corrupted file, an
            unreadable image, etc). Also real and honest -- shown instead of
            letting the whole page crash.
        None -- the trained model file isn't in place yet.
    - annotated_saved is True only if a copy of the photo, with the traced
      crack outline drawn on it, was actually written to
      annotated_output_path.
    - confidence_percent is a real number (0-100) only when severity_label
      is "Low"/"Medium"/"High" -- otherwise None. This was always computed
      internally (see MIN_CONFIDENCE below -- it's how the model decides
      which detected regions to trust in the first place) but used to be
      thrown away instead of returned. When more than one crack region was
      found and kept, this is the plain AVERAGE of their individual
      confidence scores -- a simple, disclosed choice, not a trained value.
    - crack_length_cm / crack_width_cm are real numbers (centimetres) only
      when ALL of these are true: severity_label is "Low"/"Medium"/"High"
      (a real shape was traced), detect_marker is True (the caller wants
      this -- see below), AND a printed ArUco marker was actually found
      in this specific photo (see aruco_measurement.py). Otherwise both are
      None -- never a fabricated guess. In particular, a real crack shape
      with no marker in the photo is a completely normal, expected result
      (None, None) -- it just means no real cm measurement is possible for
      THAT photo, not that anything went wrong.
    - detect_marker controls whether marker detection is even attempted --
      pass False for a video frame (see ml_models/predict_video_road.py and
      predict_video_building.py), where there's no realistic way to expect
      a marker to stay in frame across a whole video, so this round's real
      cm measurement is scoped to single photo reports only (see
      PROJECT_LOG.md section 72). Skips the extra work entirely rather than
      just discarding the result, since predict_severity already runs once
      per sampled video frame.
    - crack_length_px / crack_width_px / crack_area_px (see
      aruco_measurement.py's measure_crack_pixels and PROJECT_LOG.md
      section 75) are real numbers whenever severity_label is
      "Low"/"Medium"/"High" (a real shape was traced) -- ALWAYS attempted,
      regardless of detect_marker or whether a marker was actually found,
      since these never needed a marker in the first place. None/None/None
      whenever there's no traced shape at all (same cases crack_length_cm
      is None for that reason). Clearly NOT a real-world measurement --
      see measure_crack_pixels's own docstring for why these can't be
      compared between different photos.

    Returns (None, False, None, None, None, None, None, None) only if the
    trained model file isn't in place, so the web app can show "Not yet
    available" instead of crashing.
    """
    if not model_file_exists():
        return None, False, None, None, None, None, None, None

    try:
        # Imported here for the same "don't break the app before install"
        # reason as the ultralytics import above.
        import cv2
        import numpy as np

        model = _load_model()
        results = model.predict(image_path, verbose=False)
        result = results[0]

        image = cv2.imread(image_path)
        if image is None:
            return "Analysis error", False, None, None, None, None, None, None
        height, width = image.shape[:2]
        image_area = height * width

        masks = result.masks
        boxes = result.boxes
        if masks is None or boxes is None or len(boxes) == 0:
            return "No crack shape detected", False, None, None, None, None, None, None

        keep_indices = [i for i in range(len(boxes)) if float(boxes.conf[i]) >= MIN_CONFIDENCE]
        if not keep_indices:
            return "No crack shape detected", False, None, None, None, None, None, None

        # Combine every confident crack region into one mask -- most photos
        # only have one continuous crack, but this handles more than one
        # cleanly too.
        combined_mask = np.zeros((height, width), dtype=np.uint8)
        for i in keep_indices:
            polygon = masks.xy[i]  # (x, y) points tracing this region, in the original photo's coordinates
            if polygon is None or len(polygon) == 0:
                continue
            points = polygon.astype(np.int32).reshape((-1, 1, 2))
            cv2.fillPoly(combined_mask, [points], 255)

        crack_area = int(np.count_nonzero(combined_mask))
        if crack_area == 0:
            return "No crack shape detected", False, None, None, None, None, None, None

        coverage_percent = (crack_area / image_area) * 100
        if coverage_percent < LOW_MAX_PERCENT:
            severity_label = "Low"
        elif coverage_percent < MEDIUM_MAX_PERCENT:
            severity_label = "Medium"
        else:
            severity_label = "High"

        # The confidence number this function used to throw away -- now
        # surfaced. Plain average across every kept region, rounded to one
        # decimal place.
        confidence_percent = round(
            sum(float(boxes.conf[i]) for i in keep_indices) / len(keep_indices) * 100, 1
        )

        annotated_saved = False
        if annotated_output_path:
            overlay = image.copy()
            overlay[combined_mask > 0] = (44, 111, 231)  # BGR -- the app's orange accent color
            blended = cv2.addWeighted(overlay, 0.45, image, 0.55, 0)
            annotated_saved = bool(cv2.imwrite(annotated_output_path, blended))

        # Real cm measurements via a printed ArUco marker (PROJECT_LOG.md
        # section 72) -- only attempted for single photos (detect_marker
        # defaults to True), reusing the same loaded image and traced mask
        # rather than re-reading anything from disk.
        crack_length_cm = None
        crack_width_cm = None
        if detect_marker:
            from aruco_measurement import detect_marker_pixels_per_cm, measure_crack
            pixels_per_cm = detect_marker_pixels_per_cm(image)
            if pixels_per_cm:
                crack_length_cm, crack_width_cm = measure_crack(combined_mask, pixels_per_cm)

        # The brought-back pixel-only measurement (PROJECT_LOG.md section
        # 75) -- always attempted whenever there's a traced shape to
        # measure, regardless of detect_marker or whether a marker was
        # actually found, since this never needed one. Reuses the exact
        # same shape math as the cm measurement above (see
        # aruco_measurement.py), just without a cm conversion.
        from aruco_measurement import measure_crack_pixels
        crack_length_px, crack_width_px, crack_area_px = measure_crack_pixels(combined_mask)

        return (severity_label, annotated_saved, confidence_percent,
                crack_length_cm, crack_width_cm,
                crack_length_px, crack_width_px, crack_area_px)

    except Exception:
        # Anything unexpected (a corrupted photo, an odd model output, etc)
        # -- shown honestly as "Analysis error" instead of crashing the
        # whole Report Review page with a generic 500 error.
        return "Analysis error", False, None, None, None, None, None, None
