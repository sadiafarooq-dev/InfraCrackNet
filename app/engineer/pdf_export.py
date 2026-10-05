
import os
from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from time_utils import to_local, now_local
from text_utils import clean_points, verification_rows, report_photos, photo_result_lines


NAVY = (0x1E / 255, 0x3A / 255, 0x5F / 255)
ORANGE = (0xE7 / 255, 0x6F / 255, 0x2C / 255)
GREY = (0x4B / 255, 0x55 / 255, 0x63 / 255)
BORDER = (0xE5 / 255, 0xE7 / 255, 0xEB / 255)


def _hex(h):
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))


# Status pill colors (fill, text). They match the pills on the preview page.
STATUS_PILLS = {
    "Submitted": (_hex("#E5E7EB"), _hex("#374151")),
    "Under Review": (_hex("#DCEBF7"), _hex("#1E3A5F")),
    "Reviewed": (_hex("#FCEFC7"), _hex("#7A5A00")),
    "Resolved": (_hex("#D9F2E3"), _hex("#1E6B3F")),
}

NO_REMARKS = "No remarks have been recorded for this report yet."


def build_report_pdf(report, reviewer, photo_full_path: str) -> bytes:

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 0.75 * inch
    bottom_limit = margin + 0.35 * inch  # keep text clear of the footer
    page_no = 1

    def draw_footer():
        c.setStrokeColorRGB(*BORDER)
        c.setLineWidth(0.8)
        c.line(margin, margin - 4, width - margin, margin - 4)
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(*GREY)
        c.drawString(margin, margin - 18, f"InfraCrackNet · RPT-{report.id:04d}")
        c.drawRightString(width - margin, margin - 18, f"Page {page_no}")

    # -- Accent bar across the top (navy with an orange end) --
    bar_h = 6
    c.setFillColorRGB(*NAVY)
    c.rect(0, height - bar_h, width * 0.75, bar_h, stroke=0, fill=1)
    c.setFillColorRGB(*ORANGE)
    c.rect(width * 0.75, height - bar_h, width * 0.25, bar_h, stroke=0, fill=1)

    # -- Letterhead --
    c.setFillColorRGB(*NAVY)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, height - margin, "InfraCrackNet")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(*GREY)
    c.drawRightString(
        width - margin, height - margin + 2,
        f"Generated {now_local().strftime('%b %d, %Y')}",
    )
    c.setStrokeColorRGB(*BORDER)
    c.setLineWidth(1.2)
    c.line(margin, height - margin - 10, width - margin, height - margin - 10)

    # -- Title --
    c.setFillColorRGB(*NAVY)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(margin, height - margin - 45, f"Inspection Report · RPT-{report.id:04d}")

    # -- Photo --
    y_photo_top = height - margin - 65
    photo_h = 2.6 * inch
    if photo_full_path and os.path.exists(photo_full_path):
        try:
            img = ImageReader(photo_full_path)
            c.drawImage(
                img, margin, y_photo_top - photo_h,
                width=width - 2 * margin, height=photo_h,
                preserveAspectRatio=True, anchor='n', mask='auto',
            )
        except Exception:

            pass

    # -- More photos: a report with several photos shows every one of them as a
    #    small numbered picture under the main photo. --
    photos = report_photos(report) if report.source_type != "video" else []
    thumbs_extra = 0
    if len(photos) > 1:
        upload_dir = os.path.dirname(photo_full_path) if photo_full_path else ""
        box = 0.95 * inch
        gap = 8
        label_y = y_photo_top - photo_h - 14
        c.setFont("Helvetica-Bold", 8)
        c.setFillColorRGB(*GREY)
        c.drawString(margin, label_y, f"ALL {len(photos)} PHOTOS")
        row_y = label_y - 8 - box
        x = margin
        for number, name in enumerate(photos, start=1):
            c.setFillColorRGB(0.93, 0.94, 0.96)
            c.roundRect(x, row_y, box, box, 4, stroke=0, fill=1)
            try:
                c.drawImage(
                    ImageReader(os.path.join(upload_dir, name)), x, row_y,
                    width=box, height=box, preserveAspectRatio=True, anchor='c', mask='auto',
                )
            except Exception:
                pass
            c.setFillColorRGB(*NAVY)
            c.roundRect(x + 4, row_y + 4, 14, 12, 6, stroke=0, fill=1)
            c.setFillColorRGB(1, 1, 1)
            c.setFont("Helvetica-Bold", 7.5)
            c.drawCentredString(x + 11, row_y + 7, str(number))
            x += box + gap
        thumbs_extra = 8 + box + 16

    # -- Detail grid --
    grid_y = y_photo_top - photo_h - 30 - thumbs_extra
    cols = [
        ("Location", report.location),
        ("Status", report.status),
        ("Reviewed By", reviewer.name if reviewer else "Not yet reviewed"),
        ("Submitted", to_local(report.created_at).strftime("%b %d, %Y")),
    ]
    col_w = (width - 2 * margin) / len(cols)
    col_x = margin
    for label, value in cols:
        c.setFont("Helvetica", 8)
        c.setFillColorRGB(*GREY)
        c.drawString(col_x, grid_y, label.upper())
        if label == "Status":
            fill, ink = STATUS_PILLS.get(value, STATUS_PILLS["Submitted"])
            c.setFont("Helvetica-Bold", 9)
            pill_w = stringWidth(str(value), "Helvetica-Bold", 9) + 16
            c.setFillColorRGB(*fill)
            c.roundRect(col_x, grid_y - 19, pill_w, 15, 7.5, stroke=0, fill=1)
            c.setFillColorRGB(*ink)
            c.drawString(col_x + 8, grid_y - 14.5, str(value))
        else:
            c.setFont("Helvetica-Bold", 10)
            c.setFillColorRGB(*NAVY)
            c.drawString(col_x, grid_y - 14, str(value)[:34])
        col_x += col_w

    # -- Written sections. Each is (heading, lines); a line is (kind, text):
    #    "p" plain paragraph text, "b" first line of a bullet, "c" its
    #    continuation, "gap" a small space between bullets. A section is
    #    skipped when it is empty, except the Engineer's Notes, which always
    #    shows. --
    text_w = width - 2 * margin
    bullet_indent = 14

    def paragraph_lines(text):
        return [("p", ln) for ln in _wrap_paragraphs(text, text_w)] if text else []

    def bullet_lines(points):
        out = []
        for i, pt in enumerate(points):
            wrapped = _wrap_paragraphs(pt, text_w - bullet_indent)
            for j, ln in enumerate(wrapped):
                out.append(("b" if j == 0 else "c", ln))
            if i < len(points) - 1:
                out.append(("gap", ""))
        return out

    sections = []
    rows = verification_rows(report)
    if rows:
        sections.append((
            "AI Findings Checked by the Engineer",
            bullet_lines([f"{label}: AI: {ai or 'no answer'}, Engineer: {eng}" for label, ai, eng in rows]),
        ))
    photo_lines = photo_result_lines(report)
    if photo_lines:
        sections.append(("AI Findings by Photo", bullet_lines(photo_lines)))
    sections.append(("Engineer's Notes", paragraph_lines((report.engineer_remarks or "").strip() or NO_REMARKS)))
    sections.append(("Plain-English Summary", paragraph_lines((report.ai_summary or "").strip())))
    sections.append(("Cause", bullet_lines(clean_points(report.engineer_cause))))
    sections.append(("Treatment / Solution", bullet_lines(clean_points(report.treatment))))

    y = grid_y - 45
    for heading, lines in sections:
        if not lines:
            continue
        # Keep a heading together with at least its first two lines.
        if y - 16 - 12 * min(len(lines), 2) < bottom_limit:
            draw_footer()
            c.showPage()
            page_no += 1
            y = height - margin
        c.setFont("Helvetica-Bold", 11)
        c.setFillColorRGB(*NAVY)
        c.drawString(margin, y, heading)
        y -= 16
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(*GREY)
        for kind, line in lines:
            step = 5 if kind == "gap" else 12
            if y < bottom_limit:
                draw_footer()
                c.showPage()
                page_no += 1
                y = height - margin
                c.setFont("Helvetica", 9)
                c.setFillColorRGB(*GREY)
            if kind == "p" and line:
                c.drawString(margin, y, line)
            elif kind == "b":
                c.setFillColorRGB(*ORANGE)
                c.drawString(margin + 2, y, "\u2022")
                c.setFillColorRGB(*GREY)
                c.drawString(margin + bullet_indent, y, line)
            elif kind == "c":
                c.drawString(margin + bullet_indent, y, line)
            y -= step
        y -= 14

    draw_footer()
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def _wrap_paragraphs(text, max_width, font="Helvetica", size=9):
    """Word-wrap for reportlab's low-level canvas (which has no built-in
    paragraph flow). Returns a list of lines; a blank string marks the gap
    between two paragraphs, so the Engineer's own line breaks are kept."""
    lines = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            # Break a single very long word (e.g. a URL) so it cannot run off the page.
            while stringWidth(word, font, size) > max_width:
                cut = len(word)
                while cut > 1 and stringWidth(word[:cut], font, size) > max_width:
                    cut -= 1
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:cut])
                word = word[cut:]
            test = f"{line} {word}".strip()
            if stringWidth(test, font, size) > max_width and line:
                lines.append(line)
                line = word
            else:
                line = test
        if line:
            lines.append(line)
    return lines
