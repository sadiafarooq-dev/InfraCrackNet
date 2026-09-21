# Model 3 -- Crack Outline + Severity -- runs the trained model on one photo
#
# Plain-language version: this file loads the AI model we trained on Colab
# (see notebooks/train_crack_segmentation_round4_colab.ipynb and
# PROJECT_LOG.md section 59 for the final, accepted result) and gives the
# rest of the web app one simple function to call:
#
#     predict_severity("path/to/photo.jpg", "path/to/save/outline.jpg")
#         -> ("Medium", True)
#
# Model 3 traces the crack's actual shape (not just a box, like Model 2's
# object-detection boxes) -- and we turn that traced shape into one simple,
# real, NOT-trained number, exactly as planned back in the project's early
# design (PROJECT_LOG.md section 11):
#   - Severity ("Low"/"Medium"/"High") -- how much of the photo the traced
#     crack shape covers. The 1%/3% cutoffs below are a simple, disclosed,
#     rule-based choice (not researched or trained) -- easy to adjust later
#     if real-world testing suggests different numbers make more sense.
#
# (An earlier version of this file also returned a pixel-based "Length
# Estimate". That was removed on request -- it was honestly disclosed as not
# a real-world measurement the whole time it existed, but it was more
# confusing than useful, so it's gone now, not just hidden.)
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


def predict_severity(image_path: str, annotated_output_path: str = None):
    """
    Traces the crack's shape in one photo and returns
    (severity_label, annotated_saved):

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

    Returns (None, False) only if the trained model file isn't in place, so
    the web app can show "Not yet available" instead of crashing.
    """
    if not model_file_exists():
        return None, False

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
            return "Analysis error", False
        height, width = image.shape[:2]
        image_area = height * width

        masks = result.masks
        boxes = result.boxes
        if masks is None or boxes is None or len(boxes) == 0:
            return "No crack shape detected", False

        keep_indices = [i for i in range(len(boxes)) if float(boxes.conf[i]) >= MIN_CONFIDENCE]
        if not keep_indices:
            return "No crack shape detected", False

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
            return "No crack shape detected", False

        coverage_percent = (crack_area / image_area) * 100
        if coverage_percent < LOW_MAX_PERCENT:
            severity_label = "Low"
        elif coverage_percent < MEDIUM_MAX_PERCENT:
            severity_label = "Medium"
        else:
            severity_label = "High"

        annotated_saved = False
        if annotated_output_path:
            overlay = image.copy()
            overlay[combined_mask > 0] = (44, 111, 231)  # BGR -- the app's orange accent color
            blended = cv2.addWeighted(overlay, 0.45, image, 0.55, 0)
            annotated_saved = bool(cv2.imwrite(annotated_output_path, blended))

        return severity_label, annotated_saved

    except Exception:
        # Anything unexpected (a corrupted photo, an odd model output, etc)
        # -- shown honestly as "Analysis error" instead of crashing the
        # whole Report Review page with a generic 500 error.
        return "Analysis error", False
