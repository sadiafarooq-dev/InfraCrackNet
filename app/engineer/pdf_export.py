
import os
from io import BytesIO

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from time_utils import to_local, now_local


NAVY = (0x1E / 255, 0x3A / 255, 0x5F / 255)
GREY = (0x4B / 255, 0x55 / 255, 0x63 / 255)
BORDER = (0xE5 / 255, 0xE7 / 255, 0xEB / 255)


def build_report_pdf(report, reviewer, photo_full_path: str) -> bytes:
    
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    margin = 0.75 * inch

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
    c.drawString(margin, height - margin - 45, f"Inspection Report -- RPT-{report.id:04d}")

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

    # -- Detail grid --
    grid_y = y_photo_top - photo_h - 30
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
        c.setFont("Helvetica-Bold", 10)
        c.setFillColorRGB(*NAVY)
        c.drawString(col_x, grid_y - 14, str(value)[:34])
        col_x += col_w

    # -- Engineer's notes --
    text_y = grid_y - 45
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(*NAVY)
    c.drawString(margin, text_y, "Engineer's Notes")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(*GREY)
    body = report.engineer_remarks or (
        "Not yet available -- the automatic cause/treatment reference feature is still "
        "being built. This section will show the Engineer's own written remarks once a "
        "review has been saved."
    )
    _wrap_text(c, body, margin, text_y - 16, width - 2 * margin, 12)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def _wrap_text(c, text, x, y, max_width, line_height, font="Helvetica", size=9):
    """Simple word-wrap for reportlab's low-level canvas (which has no
    built-in paragraph flow the way a word processor would)."""
    words = text.split()
    line = ""
    for word in words:
        test = f"{line} {word}".strip()
        if stringWidth(test, font, size) > max_width and line:
            c.drawString(x, y, line)
            y -= line_height
            line = word
        else:
            line = test
    if line:
        c.drawString(x, y, line)
