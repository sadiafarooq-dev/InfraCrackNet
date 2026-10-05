# Small shared helpers for the Engineer's Cause/Treatment points and the
# "AI vs Engineer" verification lines, used by the Engineer pages, the
# Inspector's Report Detail page and the PDF export.

import json
import re


def clean_points(text):
    """Turns saved Cause/Treatment text (one point per line, or an older
    paragraph) into a clean list of points: bullet marks and numbering at the
    start of a line are removed and empty lines dropped."""
    points = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        cleaned = re.sub(r"^[\s\-\*•·]+", "", line)
        cleaned = re.sub(r"^\d+[\.\)]\s+", "", cleaned).strip()
        if cleaned:
            points.append(cleaned)
    return points


def verification_rows(report):
    """The AI's answer next to the Engineer's verified answer, as a list of
    (label, ai_answer, engineer_answer) -- empty if the Engineer has not
    verified this report yet. A question the Engineer skipped (for example
    type and severity on an Uncracked surface) is left out."""
    if report.verified_at is None:
        return []
    rows = []
    if report.source_type != "video":
        rows.append(("Crack Presence", report.model1_label, report.engineer_crack_presence))
    if report.surface_type == "Road":
        rows.append(("Crack Type", report.model2_label, report.engineer_crack_type))
    rows.append(("Severity", report.model3_severity, report.engineer_severity))
    return [row for row in rows if row[2]]


def report_photos(report):
    """Every photo of a report as a list of file names, the main photo first.
    A report made before several photos were allowed (or any video report)
    just gives back its one photo_path. Used wherever a page shows photos."""
    photos = [report.photo_path] if report.photo_path else []
    extra = getattr(report, "extra_photos_json", None)
    if extra:
        try:
            more = json.loads(extra)
        except ValueError:
            more = []
        photos += [name for name in more if isinstance(name, str) and name]
    return photos


def photo_result_lines(report):
    """One plain sentence per photo of a report with several photos, saying
    what the AI found in it (the most serious photo first, marked as the one
    used for the overall result). Empty for a single-photo or video report, or
    one whose analysis has not been run."""
    raw = getattr(report, "photo_results_json", None)
    if not raw or getattr(report, "analysis_run_at", None) is None:
        return []
    try:
        results = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(results, list) or len(results) < 2:
        return []
    is_road = report.surface_type == "Road"
    lines = []
    for index, r in enumerate(results, start=1):
        tag = " (used for the overall result)" if index == 1 else ""
        if r.get("failed"):
            lines.append(f"Photo {index}{tag}: could not be analyzed")
            continue
        parts = [f"Crack Presence {r.get('ai_label') or 'not available'}",
                 f"Severity {r.get('severity') or 'not available'}"]
        if is_road:
            parts.append(f"Crack Type {r.get('type_label') or 'not available'}")
        lines.append(f"Photo {index}{tag}: " + ", ".join(parts))
    return lines


def register_text_helpers(templates):
    """Adds the `points` filter and the `verification_rows` function to one
    Jinja2Templates instance, e.g. {% for pt in report.treatment|points %}.
    Called next to register_localtime wherever a page needs them."""
    templates.env.filters["points"] = clean_points
    templates.env.globals["verification_rows"] = verification_rows
    templates.env.globals["report_photos"] = report_photos
    templates.env.globals["photo_result_lines"] = photo_result_lines
