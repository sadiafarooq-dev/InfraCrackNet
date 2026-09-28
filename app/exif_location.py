# Real GPS location, read directly from the photo/video file itself --
# this is the fix for the location bug you found (PROJECT_LOG.md section
# 69): "Use My Current Location" (static/js/geolocation.js) was asking the
# browser "where is this device RIGHT NOW", which reflects the Inspector's
# location at UPLOAD time -- not where the photo/video was actually TAKEN.
# If an Inspector photographs a crack on-site and uploads it later from
# home, the old button would have silently recorded their home's location
# instead. This file fixes that at the source: most phone cameras already
# save their own real GPS location INSIDE the photo/video file itself, the
# moment it's taken -- this reads that, instead of asking the browser.
#
# Two honestly different code paths, because photos and videos store this
# completely differently:
#
#   - PHOTOS: read using Pillow (already a project dependency -- see
#     requirements.txt -- so nothing new to install). Works for a normal
#     JPG straight off a phone camera, as long as the phone had Location
#     turned on for its camera app. Some phones save photos as HEIC
#     instead of JPG, which Pillow can't open without an extra plugin --
#     handled the same honest way as any other photo with no GPS: this
#     just returns None, and the app falls back to the manual/device-
#     location options, same as always.
#
#   - VIDEOS: read using a free command-line tool called ffmpeg/ffprobe.
#     This is NOT a Python package (pip can't install it) -- it's a
#     separate program that has to already be installed on whatever
#     computer is actually running the app. If it isn't installed, video
#     location extraction is skipped cleanly (returns None) instead of
#     crashing the upload -- the app just falls back to the device-location
#     button instead, same as if the video simply had no GPS saved in it.
#     See PROJECT_LOG.md for how to install ffmpeg if you want this to work
#     for videos too.
#
# Either function returns (latitude, longitude) as real numbers, or None if
# no GPS location could be found in the file. None is a completely normal,
# expected result -- plenty of real photos/videos never had Location turned
# on, and every one of the AI-generated demo videos used throughout this
# project has no real GPS at all, since they were never filmed by a real
# camera in a real place.

import json
import os
import re
import shutil
import subprocess

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm"}


def extract_gps(file_path: str):
    """
    Reads the real GPS location saved inside a photo or video file, if any.
    Returns (latitude, longitude) as floats, or None if the file has no GPS
    location saved inside it (or it couldn't be read, for any reason --
    this never raises, so it's always safe to call on upload).
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return _extract_from_video(file_path)
    return _extract_from_photo(file_path)


def _extract_from_photo(file_path: str):
    try:
        from PIL import Image

        image = Image.open(file_path)
        exif = image.getexif()
        if not exif:
            return None

        # GPS info lives in its own "IFD" (a labeled sub-section of the
        # photo's EXIF data), under tag 0x8825 -- this is a standard part
        # of the JPEG/EXIF format, not anything specific to this project.
        gps_ifd = exif.get_ifd(0x8825)
        if not gps_ifd:
            return None

        lat = _dms_to_degrees(gps_ifd.get(2))   # GPSLatitude
        lat_ref = gps_ifd.get(1)                # GPSLatitudeRef: "N" or "S"
        lon = _dms_to_degrees(gps_ifd.get(4))   # GPSLongitude
        lon_ref = gps_ifd.get(3)                # GPSLongitudeRef: "E" or "W"

        if lat is None or lon is None:
            return None
        if lat_ref == "S":
            lat = -lat
        if lon_ref == "W":
            lon = -lon
        return (lat, lon)
    except Exception:
        # A photo with no EXIF data at all, a corrupted file, or a format
        # Pillow can't open (e.g. some phones' HEIC photos without the
        # extra plugin) -- all treated the same honest way: no GPS found,
        # nothing shown to the Inspector as an error.
        return None


def _dms_to_degrees(dms):
    # EXIF stores GPS coordinates as (degrees, minutes, seconds) -- this
    # turns that into one plain decimal number,
    # e.g. 33 deg, 41 min, 24 sec  ->  33.69.
    if not dms or len(dms) != 3:
        return None
    try:
        degrees, minutes, seconds = dms
        return float(degrees) + float(minutes) / 60 + float(seconds) / 3600
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _extract_from_video(file_path: str):
    # Needs ffprobe (part of the free ffmpeg toolkit) actually installed on
    # this computer -- see the honest note at the top of this file. If it
    # isn't installed, this quietly returns None instead of crashing the
    # upload -- the Inspector never sees an error either way, the app just
    # falls back to the normal device-location button.
    if not shutil.which("ffprobe"):
        return None

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path],
            capture_output=True, text=True, timeout=20,
        )
        data = json.loads(result.stdout)
        tags = (data.get("format") or {}).get("tags") or {}

        # Different phones/cameras save this under different tag names --
        # checking both honestly covers most real devices (iPhones use the
        # Apple-specific tag below; most Android phones and action cameras
        # use the plain "location" tag).
        raw = tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location")
        if not raw:
            return None
        return _parse_iso6709(raw)
    except Exception:
        return None


def _parse_iso6709(raw: str):
    # Videos store GPS as one compact text string in a standard format
    # called ISO 6709, e.g. "+33.6844+073.0479+520.000/" -- this pulls the
    # first two numbers (latitude, then longitude) out of it.
    match = re.match(r"^([+\-]\d+\.?\d*)([+\-]\d+\.?\d*)", raw)
    if not match:
        return None
    try:
        return (float(match.group(1)), float(match.group(2)))
    except ValueError:
        return None
