# Password hashing
import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# Who is logged in
from fastapi import Request, Depends
from sqlmodel import Session, select

from database import get_session
from models.user import User


def get_current_user(request: Request, session: Session = Depends(get_session)):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return session.get(User, user_id)

def generate_employee_code(role, session: Session):
    prefix = {"Inspector": "INS", "Engineer": "ENG", "Admin": "ADM"}[role]
    existing = session.exec(select(User).where(User.role == role)).all()
    next_number = len(existing) + 1
    return f"{prefix}-{next_number:04d}"



import secrets


def generate_reset_token():
    return secrets.token_urlsafe(24)
