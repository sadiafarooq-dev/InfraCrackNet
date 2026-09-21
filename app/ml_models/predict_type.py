# Model 2 -- Road Crack Type -- runs the trained model on one road photo
#
# Plain-language version: this file loads the AI model we trained on Colab
# (see notebooks/train_road_crack_type_round3_colab.ipynb and PROJECT_LOG.md
# section 56 for the final, accepted result) and gives the rest of the web
# app one simple function to call:
#
#     predict_crack_type("path/to/photo.jpg")  ->  ("Alligator Crack", 78.4)
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
