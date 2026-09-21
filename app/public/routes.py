# Public marketing pages -- no login required, but if someone happens to
# be logged in while viewing them (e.g. clicking "Contact Admin" from a
# sidebar), the navbar shows their real avatar/name instead of "Sign In".
from fastapi import APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from auth.utils import get_current_user
from database import get_session
from models.message import Message

router = APIRouter()
templates = Jinja2Templates(directory=["templates", "public/templates"])


@router.get("/")
def landing_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "landing.html", {"user": user})


@router.get("/about")
def about_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "about.html", {"user": user})


@router.get("/how-it-works")
def how_it_works_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "how_it_works.html", {"user": user})


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
    session: Session = Depends(get_session),
):
    # This now genuinely lands in the Admin's real Inbox (see admin/routes.py)
    # -- if the sender happens to be logged in, their real name/account is
    # attached; if not (an outside visitor), just the name/email they typed.
    # Still no real email is sent out, since there's no email service.
    msg = Message(
        sender_name=user.name if user else name,
        sender_user_id=user.id if user else None,
        subject=subject,
        body=f"{message}\n\n(Reply-to email given: {email})" if not user else message,
    )
    session.add(msg)
    session.commit()

    return templates.TemplateResponse(request, "contact_us.html", {"sent": True, "user": user})


@router.get("/terms")
def terms_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "terms.html", {"user": user})


@router.get("/help")
def help_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "help.html", {"user": user})
