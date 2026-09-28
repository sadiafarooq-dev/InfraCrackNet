# Audit Log model -- a real, permanent record of account and report actions,
# added in the Admin chunk (see PROJECT_LOG.md). Entries are only ever
# written going forward from when this was built; nothing from before that
# is backfilled, since there was nowhere real to read it from.
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Session


class AuditLogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    actor_name: str
    action: str
    details: str


def log_action(session: Session, actor_name: str, action: str, details: str):
    """Write one real audit log entry. Called right alongside the real
    action it's describing (account created, report reviewed, etc.) -- see
    admin/routes.py, auth/routes.py, inspector/routes.py, and
    engineer/routes.py for where each one is triggered."""
    entry = AuditLogEntry(actor_name=actor_name, action=action, details=details)
    session.add(entry)
