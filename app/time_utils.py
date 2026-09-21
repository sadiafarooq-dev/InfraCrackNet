
from datetime import datetime, timedelta

PKT_OFFSET = timedelta(hours=5)


def to_local(dt):
    """Converts one stored UTC datetime to Pakistan time for display.
    Passing None back out as None (rather than raising) keeps this safe to
    use directly inside a Jinja expression on an Optional field."""
    if dt is None:
        return None
    return dt + PKT_OFFSET


def now_local() -> datetime:
    """Pakistan-time 'now', for the few places that stamp a display-only
    string directly in Python (e.g. the PDF report's "Generated on" line)
    rather than storing a datetime and formatting it later."""
    return datetime.utcnow() + PKT_OFFSET


def register_localtime(templates):
    """Adds the `localtime` filter to one Jinja2Templates instance, e.g.:
        {{ (report.created_at | localtime).strftime("%b %d, %Y") }}
    Called once per Jinja2Templates() instance across the app (see each
    routes.py module and main.py)."""
    templates.env.filters["localtime"] = to_local
