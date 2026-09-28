# Public marketing pages -- no login required, but if someone happens to
# be logged in while viewing them, the navbar shows their real avatar/name
# instead of "Sign In".
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

from auth.utils import get_current_user

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


@router.get("/terms")
def terms_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "terms.html", {"user": user})


@router.get("/help")
def help_page(request: Request, user=Depends(get_current_user)):
    return templates.TemplateResponse(request, "help.html", {"user": user})
