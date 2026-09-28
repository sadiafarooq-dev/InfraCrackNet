# Real cm crack measurements, via a printed ArUco marker
#
# Plain-language version: a photo alone has no sense of real-world scale --
# a crack that looks huge in a close-up photo might actually be tiny, and a
# crack that looks small in a wide shot might be huge. To get a REAL
# centimetre measurement (not a guess), the Inspector places one printed
# marker (a small black-and-white square, a bit like a simplified QR code)
# next to the crack before taking the photo. Since we know exactly how big
# that marker really is (see MARKER_SIZE_CM below), and we can measure
# exactly how big it LOOKS in the photo (in pixels), we can work out how
# many pixels equal one real centimetre in THIS SPECIFIC PHOTO -- then use
# that same ratio to turn the crack's traced pixel shape (which Model 3
# already produces, for the Severity calculation) into a real length and
# width in centimetres.
#
# This is genuine, well-established computer vision (OpenCV's "ArUco"
# marker system -- the same kind of technology used in robotics and
# augmented-reality apps for exactly this kind of scale/position
# reference), not a trained AI model. There's no "learning" involved here at
# all, just geometry: find the marker's 4 corners in the photo, measure its
# sides in pixels, divide by its known real-world size.
#
# See PROJECT_LOG.md section 72 for the full plain-language writeup of the
# marker-based cm measurement itself, print_aruco_marker.html for the
# actual marker image to print out and use in the field, section 74
# for how the crack's own length/width (below) is now measured along its
# real, curving shape instead of a straight-line box, and section 75 for
# measure_crack_pixels (also below) -- the brought-back PIXEL-ONLY version
# of this same measurement, for a report where no marker was found (or a
# video, where a marker was never attempted), so something real is still
# shown instead of nothing.

import cv2
import numpy as np
from skimage.morphology import skeletonize

# The specific marker "family" and ID this project looks for. ArUco markers
# come in different "dictionaries" (different total numbers of unique
# markers, different grid sizes/complexity) -- 4x4_50 is a small, simple,
# widely-supported one, and this project only ever needs ONE fixed marker
# (ID 0), so the smallest dictionary that's easy to print clearly is the
# right choice -- no need for anything fancier.
MARKER_DICT_TYPE = cv2.aruco.DICT_4X4_50
MARKER_ID = 0

# The real, physical size of the PRINTED marker's outer black square, in
# centimetres (one side, not the diagonal). This is a disclosed ASSUMPTION,
# not something the app can verify by itself -- it assumes the marker was
# printed exactly the size print_aruco_marker.html asks for (5cm x 5cm), at
# 100% print scale (i.e. "Fit to page" / "Shrink to fit" turned OFF in the
# printer settings, since either of those would silently resize it), OR
# shown on a phone screen using that same page's Option B, sized for that
# specific phone.
#
# If it doesn't come out at exactly 5cm, measure it yourself (a ruler, or
# any object of a known real size, works) and change this one number to
# match -- every measurement this file computes is only ever as accurate
# as this constant, since it's the one real-world fact the whole
# calculation is built on.
MARKER_SIZE_CM = 5.0

# A real, tested field note: the marker needs a bit of plain white space
# around it to be detected reliably -- a marker with NO margin at all
# (cropped exactly flush to its own black edge) was confirmed in this
# project's own testing to NOT be detected, since the detector discards
# candidate shapes that touch the edge of the photo. This is a non-issue as
# long as the marker sits somewhere in the middle of the photo with the
# usual surrounding scene around it (the road, the wall, the rest of the
# printed page, or the phone's own bezel) -- just don't crop or hold the
# marker flush against the photo's own edge.


def detect_marker_pixels_per_cm(image):
    """
    Looks for the printed ArUco marker (MARKER_ID, in MARKER_DICT_TYPE) in a
    photo (a real, already-loaded OpenCV/numpy image, e.g. from
    cv2.imread -- the same image predict_severity.py already has loaded for
    the Severity calculation, reused here rather than re-read from disk).

    Returns a real number -- how many pixels equal one real centimetre, in
    THIS SPECIFIC PHOTO -- if the marker was found, or None if it wasn't. A
    None result is a completely normal, expected outcome when the marker
    simply wasn't included in the shot -- not an error.
    """
    dictionary = cv2.aruco.getPredefinedDictionary(MARKER_DICT_TYPE)
    parameters = cv2.aruco.DetectorParameters()
    detector = cv2.aruco.ArucoDetector(dictionary, parameters)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    corners, ids, _ = detector.detectMarkers(gray)

    if ids is None or len(ids) == 0:
        return None

    ids_flat = ids.flatten()
    if MARKER_ID not in ids_flat:
        return None

    marker_index = list(ids_flat).index(MARKER_ID)
    marker_corners = corners[marker_index][0]  # the 4 (x, y) corner points, in order

    # Average all 4 side lengths rather than trusting just one -- this makes
    # the ratio more forgiving of the marker being slightly tilted towards
    # the camera instead of perfectly face-on, which is the realistic case
    # (asking an Inspector to hold the camera perfectly square-on to the
    # marker every single time isn't practical in the field).
    side_lengths = [
        float(np.linalg.norm(marker_corners[i] - marker_corners[(i + 1) % 4]))
        for i in range(4)
    ]
    avg_side_px = sum(side_lengths) / 4

    if avg_side_px <= 0:
        return None

    return avg_side_px / MARKER_SIZE_CM


def _skeleton_path_length(skeleton):
    """
    Adds up the real pixel-to-pixel distances along a skeletonized crack
    shape -- 1.0 for a horizontal/vertical step between neighbouring
    skeleton pixels, about 1.41 (the square root of 2) for a diagonal step
    -- which follows the crack's actual bends and curves, rather than a
    single straight line across its bounding box.

    Every pixel only looks at 4 of its 8 neighbours (right, down,
    down-right, down-left), which finds every real connection in the
    skeleton exactly once without counting anything twice. If the skeleton
    branches (a Y-shaped or forked crack, for example), every branch gets
    added in, which is the honest, disclosed choice: a branching crack
    really is longer, end to end, than a single straight one would be.
    """
    ys, xs = np.nonzero(skeleton)
    coords = set(zip(ys.tolist(), xs.tolist()))
    total = 0.0
    for y, x in coords:
        for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
            if (y + dy, x + dx) in coords:
                total += 1.0 if (dy == 0 or dx == 0) else 1.4142135623730951
    return total


def _measure_crack_bounding_box_px(crack_mask):
    """
    The original, simpler measurement this project used at first: the
    smallest possible rotated rectangle that fully contains the traced
    crack shape (OpenCV's minAreaRect) -- its LONGER side treated as
    length, its SHORTER side as width. A straight-line, corner-to-corner
    estimate, not a follow-every-bend measurement. Raw pixels -- no cm
    conversion here (see the two thin wrappers right below this).

    Kept on as a fallback for the rare case where a confidently-detected
    crack shape is too small or blob-like to have a meaningful centerline
    (see _measure_crack_shape_px below) -- better to give a real, if
    simpler, number than none at all.
    """
    points = cv2.findNonZero(crack_mask)
    if points is None or len(points) == 0:
        return None, None

    (_, _), (w_px, h_px), _ = cv2.minAreaRect(points)
    length_px = max(w_px, h_px)
    width_px = min(w_px, h_px)
    return length_px, width_px


def _measure_crack_bounding_box(crack_mask, pixels_per_cm):
    """Thin cm-converting wrapper around _measure_crack_bounding_box_px
    above -- kept under its original name so measure_crack below (the real
    cm measurement) didn't need to change at all for this round."""
    length_px, width_px = _measure_crack_bounding_box_px(crack_mask)
    if length_px is None:
        return None, None
    return round(length_px / pixels_per_cm, 1), round(width_px / pixels_per_cm, 1)


def _measure_crack_shape_px(crack_mask):
    """
    Does the actual shape measurement -- skeletonize + distance transform,
    same real geometry PROJECT_LOG.md section 74 describes -- and returns
    it in RAW PIXELS, with no cm conversion at all. Shared by measure_crack
    (converts this to cm, when a marker's pixels_per_cm ratio is available)
    and measure_crack_pixels (returns this as-is, for when no marker/ratio
    is available -- see section 75). Keeping the shape math in exactly one
    place means the real cm numbers and the pixel-only numbers always agree
    with each other -- same underlying measurement, just converted or not.

    Returns (length_px, width_px), or (None, None) if there's no shape to
    measure at all.
    """
    if crack_mask is None or not np.any(crack_mask):
        return None, None

    binary = crack_mask > 0
    skeleton = skeletonize(binary)

    length_px = _skeleton_path_length(skeleton)
    if length_px <= 1.0:
        return _measure_crack_bounding_box_px(crack_mask)

    dist_transform = cv2.distanceTransform(crack_mask, cv2.DIST_L2, 5)
    skeleton_widths_px = dist_transform[skeleton] * 2
    skeleton_widths_px = skeleton_widths_px[skeleton_widths_px > 0]
    if len(skeleton_widths_px) == 0:
        return _measure_crack_bounding_box_px(crack_mask)
    width_px = float(np.median(skeleton_widths_px))

    if width_px <= 0:
        return _measure_crack_bounding_box_px(crack_mask)

    return length_px, width_px


def measure_crack(crack_mask, pixels_per_cm):
    """
    Given Model 3's traced crack shape (a binary mask -- the same
    combined_mask predict_severity.py already builds for its Severity
    calculation, reused here rather than computed twice) and a
    pixels-per-cm ratio from detect_marker_pixels_per_cm above, returns
    (length_cm, width_cm): a real-world size estimate, both rounded to 1
    decimal place.

    How it's measured (updated -- see PROJECT_LOG.md section 74 for the
    full plain-language writeup and the real tests behind this change):
    - LENGTH follows the crack's own real shape. The traced shape is
      reduced to a thin, one-pixel-wide centerline (`skeletonize`, from
      the well-established scikit-image library), and the length is the
      total distance walked along that centerline, bend for bend --
      not a single straight line between its two furthest corners. A
      bent or curved crack now measures noticeably longer than the old
      method would have shown, which is the more honest number: a crack
      that turns a corner really is longer, end to end, than the straight
      distance between its tips.
    - WIDTH is read directly off the crack's own thickness. At every
      point along that same centerline, OpenCV's distance transform gives
      how far that point sits from the nearest edge of the traced shape --
      doubling that gives the real thickness AT THAT POINT. The MEDIAN
      (not the average) of all those points is used as the final width,
      since a crack naturally tapers to a point at both ends, and a
      median isn't dragged down by those thin tips the way an average
      would be.
    - A real, disclosed fallback: if the traced shape is too small or
      blob-like to reduce to a meaningful centerline (a handful of
      pixels, essentially a dot rather than a line), this quietly falls
      back to the simpler straight-line bounding-box method above,
      rather than returning nothing -- a real, if simpler, number is
      better than none for an edge case like that.

    Returns (None, None) if there's no shape to measure, or no usable ratio
    (pixels_per_cm is None/zero -- i.e. no marker was found in this photo).
    """
    if not pixels_per_cm or pixels_per_cm <= 0:
        return None, None

    length_px, width_px = _measure_crack_shape_px(crack_mask)
    if length_px is None:
        return None, None

    length_cm = round(length_px / pixels_per_cm, 1)
    width_cm = round(width_px / pixels_per_cm, 1)
    return length_cm, width_cm


def measure_crack_pixels(crack_mask):
    """
    The brought-back PIXEL-ONLY version of measure_crack above -- see
    PROJECT_LOG.md section 75 for the full plain-language writeup of why
    this exists (in short: you asked for it back, now shown clearly
    labeled as pixel-only rather than instead of the real cm numbers, the
    way an earlier version of this project did before it was removed for
    being confusing -- see models/report.py's own note on this).

    Uses the exact same shape measurement as measure_crack (same
    skeletonize + distance-transform geometry, same fallback for a
    too-small/blob-like shape) -- just with NO cm conversion at all, so it
    works with no marker, no pixels_per_cm ratio, nothing. This is why the
    two numbers always agree: a photo WITH a marker gets the same
    underlying length_px/width_px as one without, just converted to cm in
    one case and not the other.

    Returns (length_px, width_px, area_px):
    - length_px / width_px -- rounded to the nearest whole pixel (a
      fractional pixel isn't meaningful to a person looking at this).
    - area_px -- a plain count of how many pixels the traced crack shape
      covers, the exact same number Severity itself is computed from (see
      predict_severity.py) -- not a separate calculation.

    Returns (None, None, None) if there's no traced crack shape at all.
    NEVER a real-world unit -- these are pixels, and how large a pixel is
    depends entirely on how close the photo was taken and what camera/zoom
    was used, so these numbers can't be compared between two different
    photos, only used as a rough sense of this one photo's crack.
    """
    if crack_mask is None or not np.any(crack_mask):
        return None, None, None

    length_px, width_px = _measure_crack_shape_px(crack_mask)
    if length_px is None:
        return None, None, None

    area_px = int(np.count_nonzero(crack_mask))
    return round(length_px), round(width_px), area_px
