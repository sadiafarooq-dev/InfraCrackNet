# Auth routes
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from database import get_session
from models.user import User
from models.app_settings import get_settings
from models.audit_log import log_action
from auth.utils import hash_password, verify_password, generate_employee_code, generate_reset_token, get_current_user
from email_utils import send_email
from time_utils import register_localtime
from asset_version import register_asset_version

RESET_TOKEN_VALID_MINUTES = 30


def _reset_link_for(user, request) -> str:
    return str(request.url_for("reset_password_page")) + f"?token={user.reset_token}"


def _send_reset_email(user, request) -> bool:
    """Tries to genuinely email the reset link. Returns True if it really
    went out, False if email isn't configured on this computer yet (see
    email_utils.py) -- in which case the Check Your Email screen falls
    back to showing the link directly, same as before this was added."""
    link = _reset_link_for(user, request)
    body = (
        f"Hi {user.name},\n\n"
        "Someone asked to reset the password for your InfraCrackNet account.\n"
        "Click the link below to set a new password (it expires in 30 minutes):\n\n"
        f"{link}\n\n"
        "If you didn't request this, you can safely ignore this email.\n"
    )
    return send_email(user.email, "Reset your InfraCrackNet password", body)

router = APIRouter()
templates = Jinja2Templates(directory="templates")
register_localtime(templates)
register_asset_version(templates)


# Login page
@router.get("/login")
def login_page(request: Request, signed_up: Optional[str] = None, current_user=Depends(get_current_user)):
    # current_user is only non-None if someone who's already signed in somehow
    # lands back on this page -- that lets the navbar show their real avatar
    # instead of a "Sign Up" button that wouldn't make sense for them.
    context = {"user": current_user}
    if signed_up:
        context["success"] = "Account created! Sign in below with your new password."
    return templates.TemplateResponse(request, "login.html", context)


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    user = session.exec(select(User).where(User.email == email)).first()

    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong email or password.", "user": current_user}
        )

    request.session["user_id"] = user.id
    request.session["role"] = user.role

    if user.role in ("Inspector", "Engineer"):
       
        if not user.has_seen_onboarding:
            return RedirectResponse("/onboarding/1", status_code=303)
        if user.role == "Inspector":
            return RedirectResponse("/inspector/dashboard", status_code=303)
        return RedirectResponse("/engineer/dashboard", status_code=303)
    return RedirectResponse("/admin/dashboard", status_code=303)


# Sign up page
@router.get("/signup")
def signup_page(request: Request, current_user=Depends(get_current_user)):
    # current_user lets the navbar show a real avatar instead of a "Sign In"
    # button, for the rare case someone who's already signed in lands back
    # on this page -- same reasoning as the Sign In page (section 116).
    return templates.TemplateResponse(request, "signup.html", {"user": current_user})


@router.post("/signup")
def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    registration_code: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    session: Session = Depends(get_session),
    current_user=Depends(get_current_user),
):
    form_back = {
        "name": name,
        "email": email,
        "role": role,
        "user": current_user,
    }

    current_code = get_settings(session).registration_code
    if registration_code.strip() != current_code:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "That registration code is not correct. Check with your admin.", **form_back},
        )

    if role not in ("Inspector", "Engineer"):
        return templates.TemplateResponse(
            request, "signup.html", {"error": "Please choose Inspector or Engineer.", **form_back}
        )

    if password != confirm_password:
        return templates.TemplateResponse(
            request, "signup.html", {"error": "Passwords do not match.", **form_back}
        )

    existing = session.exec(select(User).where(User.email == email)).first()
    if existing:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {"error": "An account with that email already exists. Please sign in instead.", **form_back},
        )

    employee_code = generate_employee_code(role, session)
    new_user = User(
        name=name,
        email=email,
        role=role,
        employee_code=employee_code,
        password_hash=hash_password(password),
    )
    session.add(new_user)
    log_action(session, actor_name=name, action="Created Account",
               details=f"Signed up as {role.lower()} ({employee_code})")
    session.commit()

    return RedirectResponse("/login?signed_up=1", status_code=303)


# Forgot Password l
@router.get("/forgot-password")
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html")


@router.post("/forgot-password")
def forgot_password_submit(
    request: Request,
    email: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.email == email)).first()

    if not user:
        return templates.TemplateResponse(
            request,
            "forgot_password.html",
            {"error": "No account was found with that email.", "email": email},
        )

    user.reset_token = generate_reset_token()
    user.reset_token_expires = datetime.utcnow() + timedelta(minutes=RESET_TOKEN_VALID_MINUTES)
    session.add(user)
    session.commit()
    session.refresh(user)

    sent = _send_reset_email(user, request)

    return RedirectResponse(
        f"/forgot-password/check-email?email={quote(email)}&sent={'1' if sent else '0'}",
        status_code=303,
    )

@router.get("/forgot-password/check-email")
def check_email_page(
    request: Request,
    email: str,
    sent: str = "0",
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.email == email)).first()
    really_sent = sent == "1"
    reset_link = None
    if user and user.reset_token and not really_sent:
        reset_link = _reset_link_for(user, request)

    return templates.TemplateResponse(
        request,
        "check_email.html",
        {"email": email, "reset_link": reset_link, "really_sent": really_sent},
    )


@router.post("/forgot-password/resend")
def forgot_password_resend(
    request: Request,
    email: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.email == email)).first()
    sent = False
    if user:
        user.reset_token = generate_reset_token()
        user.reset_token_expires = datetime.utcnow() + timedelta(minutes=RESET_TOKEN_VALID_MINUTES)
        session.add(user)
        session.commit()
        session.refresh(user)
        sent = _send_reset_email(user, request)

    return RedirectResponse(
        f"/forgot-password/check-email?email={quote(email)}&sent={'1' if sent else '0'}",
        status_code=303,
    )


@router.get("/reset-password", name="reset_password_page")
def reset_password_page(request: Request, token: str, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.reset_token == token)).first()

    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        return templates.TemplateResponse(
            request,
            "forgot_password.html",
            {"error": "That reset link is invalid or has expired. Please request a new one."},
        )

    return templates.TemplateResponse(request, "set_new_password.html", {"token": token})


@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.exec(select(User).where(User.reset_token == token)).first()

    if not user or not user.reset_token_expires or user.reset_token_expires < datetime.utcnow():
        return templates.TemplateResponse(
            request,
            "forgot_password.html",
            {"error": "That reset link is invalid or has expired. Please request a new one."},
        )

    if password != confirm_password:
        return templates.TemplateResponse(
            request,
            "set_new_password.html",
            {"error": "Passwords do not match.", "token": token},
        )

    user.password_hash = hash_password(password)
    user.reset_token = None
    user.reset_token_expires = None
    session.add(user)
    session.commit()

    return RedirectResponse("/reset-password/success", status_code=303)


@router.get("/reset-password/success")
def password_changed_page(request: Request):
    return templates.TemplateResponse(request, "password_changed.html")


# Logout
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
