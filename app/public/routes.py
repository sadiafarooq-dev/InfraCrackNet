# Public marketing pages -- no login required, but if someone happens to
# be logged in while viewing them, the navbar shows their real avatar/name
# instead of "Sign In".
import os

from fastapi import APIRouter, Request, Depends, Form
from fastapi.templating import Jinja2Templates

from auth.utils import get_current_user
from asset_version import register_asset_version
from email_utils import send_email

router = APIRouter()
templates = Jinja2Templates(directory=["templates", "public/templates"])
# This was missing until now -- every OTHER section of the app (Inspector,
# Engineer, Admin, Auth) already calls this, which is what makes
# base.html's stylesheet link bust old cached CSS automatically (see
# asset_version.py's own notes -- this is the exact fix for the "my CSS
# change isn't showing up" problem). The public marketing pages (this
# file) never got it, so a visitor's browser could in theory go on showing
# old styling on Landing/About/How It Works/Terms/Help/Contact even after
# a real CSS update, the same bug asset_version.py was built to prevent
# everywhere else.
register_asset_version(templates)


@router.get("/")
def landing_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "landing.html", {"user": user})


@router.get("/about")
def about_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "about.html", {"user": user})


@router.get("/how-it-works")
def how_it_works_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "how_it_works.html", {"user": user})


@router.get("/terms")
def terms_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "terms.html", {"user": user})


@router.get("/help")
def help_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "help.html", {"user": user})


@router.get("/contact-us")
def contact_us_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "contact_us.html", {"user": user})


@router.post("/contact-us")
def contact_us_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    subject: str = Form(...),
    message: str = Form(...),
    user=Depends(get_current_user),
):
    """Emails the message straight to whichever inbox the reset-password
    emails already go through (SMTP_USER in .env -- see email_utils.py) --
    no separate "contact messages" table, this is just a mail forward. If
    email isn't set up on this computer yet, the visitor is told honestly
    instead of being shown a false "sent" confirmation."""
    body = (
        f"New message from the InfraCrackNet Contact Us page.\n\n"
        f"From: {name} <{email}>\n"
        f"Subject: {subject}\n\n"
        f"{message}\n"
    )
    support_inbox = os.environ.get("SMTP_USER")
    sent = bool(support_inbox) and send_email(support_inbox, f"[InfraCrackNet Contact] {subject}", body)

    context = {"user": user}
    if sent:
        context["sent"] = True
    else:
        context["error"] = (
            "We couldn't send your message right now -- please email us directly at "
            "support@infracracknet.com instead."
        )
    return templates.TemplateResponse(request, "contact_us.html", context)
