# Database setup
from sqlmodel import SQLModel, Session, create_engine, select

from models.user import User
from models.report import Report
from models.audit_log import AuditLogEntry
from models.app_settings import AppSettings
from models.project import Project, ProjectInspector, ProjectEngineer

DATABASE_URL = "sqlite:///./infracracknet.db"
engine = create_engine(DATABASE_URL, echo=False)


def ensure_missing_columns():
    """Adds any column a model has gained that an older infracracknet.db
    file doesn't have yet (same job as migrate_db.py, but automatic, so just
    restarting the server is enough). Only ever ADDS missing columns -- never
    deletes or changes existing data."""
    for table in SQLModel.metadata.sorted_tables:
        with engine.begin() as conn:
            existing = {row[1] for row in conn.exec_driver_sql(f'PRAGMA table_info("{table.name}")')}
        if not existing:
            continue  # table doesn't exist yet; create_all makes it fresh
        for column in table.columns:
            if column.name in existing:
                continue
            col_type = column.type.compile(engine.dialect)
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}'
                    )
                print(f"[database] Added missing column {table.name}.{column.name}")
            except Exception as exc:  # never stop the server from starting over this
                print(f"[database] Could not add {table.name}.{column.name}: {exc}")


def init_db():
    SQLModel.metadata.create_all(engine)
    ensure_missing_columns()
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
