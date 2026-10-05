# InfraCrackNet web app
import os


from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from database import init_db
from time_utils import register_localtime
from asset_version import register_asset_version
from public.routes import router as public_router
from auth.routes import router as auth_router
from inspector.routes import router as inspector_router
from engineer.routes import router as engineer_router
from admin.routes import router as admin_router
from shared.routes import router as shared_router

app = FastAPI()

error_templates = Jinja2Templates(directory="templates")
register_localtime(error_templates)
register_asset_version(error_templates)


# Shared - 404 Page
@app.exception_handler(StarletteHTTPException)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return error_templates.TemplateResponse(request, "404.html", {}, status_code=404)
   
    return error_templates.TemplateResponse(request, "generic_error.html", {}, status_code=exc.status_code)


# Shared - Generic Error State
@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    return error_templates.TemplateResponse(request, "generic_error.html", {}, status_code=500)

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory="static"), name="static")

os.makedirs(os.path.join("..", "uploads"), exist_ok=True)
app.mount("/uploads", StaticFiles(directory=os.path.join("..", "uploads")), name="uploads")

app.include_router(public_router)
app.include_router(auth_router)
app.include_router(inspector_router)
app.include_router(engineer_router)
app.include_router(admin_router)
app.include_router(shared_router)


# Startup
@app.on_event("startup")
def on_startup():
    init_db()
