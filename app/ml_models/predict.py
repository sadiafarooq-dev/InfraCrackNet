# Model 1 -- Crack Presence -- runs the trained model on one photo
#
# Plain-language version: this file loads the AI model we trained on Kaggle/Colab
# (see notebooks/train_crack_presence_round4_colab.ipynb and PROJECT_LOG.md section
# on Model 1) and gives the rest of the web app one simple function to call:
#
#     predict_crack_presence("path/to/photo.jpg")  ->  ("Cracked", 92.3)
#
# It only loads the model file once (the first time it's needed), then reuses it
# for every photo after that, so the web app stays fast.

import os

import torch
import torch.nn as nn
from torchvision.models import efficientnet_b0
from torchvision import transforms
from PIL import Image

# Where the trained model file lives on disk (same folder Sadia's other trained
# models live in -- see the project's models/ folder).
MODEL1_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "models", "model1_crack_presence", "crack_presence_best.pt"
)

_device = torch.device("cpu")
_model = None  # loaded lazily, once

# Must exactly match the resizing/normalizing used during training (see the
# "eval_transform" cell in the training notebook) -- if this doesn't match,
# the model's answers become unreliable.
_transform = transforms.Compose(
    [
        transforms.Resize((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ]
)


def model_file_exists() -> bool:
    return os.path.exists(MODEL1_PATH)


def _load_model():
    global _model
    if _model is not None:
        return _model

    # Same architecture used for training: EfficientNet-B0 with its final layer
    # swapped for a single yes/no (crack or not) output.
    model = efficientnet_b0(weights=None)
    in_features = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(in_features, 1)

    state_dict = torch.load(MODEL1_PATH, map_location=_device)
    model.load_state_dict(state_dict)
    model.eval()

    _model = model
    return _model


def predict_crack_presence(image_path: str):
    """
    Looks at one photo and returns (label, confidence_percent), e.g. ("Cracked", 92.3).

    If the trained model file isn't in place yet, returns (None, None) so the web
    app can show "Pending analysis" instead of crashing.
    """
    if not model_file_exists():
        return None, None

    model = _load_model()
    img = Image.open(image_path).convert("RGB")
    x = _transform(img).unsqueeze(0).to(_device)

    with torch.no_grad():
        prob = torch.sigmoid(model(x).squeeze(1)).item()

    label = "Cracked" if prob > 0.5 else "Uncracked"
    confidence = prob if prob > 0.5 else (1 - prob)
    return label, round(confidence * 100, 1)
