# Database setup
from sqlmodel import SQLModel, Session, create_engine, select

from models.user import User
from models.report import Report
from models.audit_log import AuditLogEntry
from models.message import Message
from models.app_settings import AppSettings

DATABASE_URL = "sqlite:///./infracracknet.db"
engine = create_engine(DATABASE_URL, echo=False)


def init_db():
    SQLModel.metadata.create_all(engine)
    seed_demo_users()


def seed_demo_users():
    with Session(engine) as session:
        existing = session.exec(select(User)).first()
        if existing:
            return

        demo_users = [
            User(name="Ali Raza", email="ali.raza@example.com", role="Inspector", employee_code="INS-0156"),
            User(name="Fatima Noor", email="fatima.noor@example.com", role="Engineer", employee_code="ENG-0089"),
            User(name="Bilal Ahmed", email="bilal.ahmed@example.com", role="Inspector", employee_code="INS-0161"),
            User(name="Sana Malik", email="sana.malik@example.com", role="Engineer", employee_code="ENG-0093"),
            User(name="Sadia Farooq", email="sadiaafarooqfyp@gmail.com", role="Admin", employee_code="ADM-0004"),
        ]
        session.add_all(demo_users)
        session.commit()


def get_session():
    with Session(engine) as session:
        yield session
