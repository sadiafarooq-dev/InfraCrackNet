# Model 2 -- Road Crack Type -- runs the trained model on one road photo
#
# Plain-language version: this file loads the AI model we trained on Colab
# (see notebooks/train_road_crack_type_round3_colab.ipynb and PROJECT_LOG.md
# section 56 for the final, accepted result) and gives the rest of the web
# app two simple functions to call:
#
#     predict_crack_type("path/to/photo.jpg")  ->  ("Alligator Crack", 78.4)
#
#     predict_crack_type_boxes("path/to/photo.jpg")  ->  [
#         {"label": "Alligator Crack", "confidence": 78.4, "box": [120, 80, 340, 260]},
#         {"label": "Pothole", "confidence": 55.1, "box": [10, 300, 90, 360]},
#     ]
#
# predict_crack_type() only ever kept the SINGLE most confident detection --
# fine for a normal photo report, where there's only ever one "Crack Type"
# row to fill in. predict_crack_type_boxes() is the new one, added for the
# road video-with-boxes feature (PROJECT_LOG.md section 70): it keeps EVERY
# confident detection Model 2 finds in a photo, plus each one's exact pixel
# box, so the app can draw labeled boxes on a frame the way the vialytics
# demo video (that first inspired this feature) does -- not just report a
# single type. Nothing new was trained for this; it's the same model,
# handing back more of what it was already computing internally and
# throwing away (the same pattern as Model 3's confidence number in section
# 69).
#
# Model 2 only ever looked at ROAD photos during training -- it has nothing
# useful to say about a building. See PROJECT_LOG.md section 17 for why
# buildings get presence + severity only, not a crack-type breakdown. It's
# the caller's job to only ask this for Road reports (see the
# _crack_type_result() helper in inspector/routes.py and engineer/routes.py).
#
# It only loads the model file once (the first time it's needed), then
# reuses it for every photo after that, so the web app stays fast.

import os

MODEL2_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "models", "model2_road_crack_type",
    "road_crack_type_round3_best.pt",
)

# A detection below this confidence is treated as "nothing found" rather
# than forced into a guess -- a plain, real cutoff, not a trained value.
MIN_CONFIDENCE = 0.25

_model = None  # loaded lazily, once


def model_file_exists() -> bool:
    return os.path.exists(MODEL2_PATH)


def _load_model():
    global _model
    if _model is not None:
        return _model

    # Imported here rather than at the top of the file, so the rest of the
    # app still runs even before `pip install -r requirements.txt` has been
    # re-run to pick up the new `ultralytics` package -- model_file_exists()
    # is what the caller checks first either way.
    from ultralytics import YOLO

    _model = YOLO(MODEL2_PATH)
    return _model


def predict_crack_type(image_path: str):
    """
    Looks at one road photo and returns (label, confidence_percent) for the
    single most confident road-damage type it finds, e.g.
    ("Alligator Crack", 78.4).

    Returns (None, None) only if the trained model file isn't in place yet,
    so the web app can show "Not yet available" instead of crashing.

    If the model file IS in place but doesn't find anything it's confident
    about in this particular photo, that's a genuine, real result -- it
    returns ("No damage type detected", None) rather than guessing.
    """
    if not model_file_exists():
        return None, None

    model = _load_model()
    results = model.predict(image_path, verbose=False)

    best_label = None
    best_conf = 0.0
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        for box in boxes:
            conf = float(box.conf[0])
            if conf > best_conf:
                best_conf = conf
                best_label = result.names[int(box.cls[0])]

    if best_label is None or best_conf < MIN_CONFIDENCE:
        return "No damage type detected", None

    # "Alligator_Crack" -> "Alligator Crack" for display
    return best_label.replace("_", " "), round(best_conf * 100, 1)


def predict_crack_type_boxes(image_path: str):
    """
    Looks at one road photo and returns a list of EVERY confident road-
    damage detection Model 2 finds, most confident first -- not just the
    single winner predict_crack_type() above keeps. Each entry looks like:

        {"label": "Alligator Crack", "confidence": 78.4, "box": [x1, y1, x2, y2]}

    "box" is the detection's bounding box in plain pixel coordinates of the
    ORIGINAL photo (x1,y1 = top-left corner, x2,y2 = bottom-right corner) --
    exactly what's needed to draw a labeled rectangle on the image.

    Returns an empty list [] if the model ran fine but found nothing it was
    confident about -- a real, honest result (see predict_crack_type()'s own
    note on this), not an error.

    Returns None only if the trained model file isn't in place yet, so the
    caller can tell "model missing" apart from "model ran, found nothing" --
    the same distinction predict_crack_type() makes with its (None, None).
    """
    if not model_file_exists():
        return None

    model = _load_model()
    results = model.predict(image_path, verbose=False)

    detections = []
    for result in results:
        boxes = result.boxes
        if boxes is None:
            continue
        for box in boxes:
            conf = float(box.conf[0])
            if conf < MIN_CONFIDENCE:
                continue
            label = result.names[int(box.cls[0])].replace("_", " ")
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            detections.append({
                "label": label,
                "confidence": round(conf * 100, 1),
                "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            })

    detections.sort(key=lambda d: d["confidence"], reverse=True)
    return detections
