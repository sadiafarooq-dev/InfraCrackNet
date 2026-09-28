# Message model -- powers Admin's real Inbox (see PROJECT_LOG.md).
# A message either comes IN to the Admin (from the public Contact Us form,
# or "Contact Admin" in any logged-in sidebar) or goes OUT from the Admin
# (Compose Message, to one specific user). `from_admin` tells the two apart
# so the same table can back one unified inbox list.
#
# Honest limit: there's no email system and no Inspector/Engineer inbox
# screen yet, so an Admin reply (or a Compose'd message) is only ever saved
# here for the record -- the person it's addressed to has no in-app way to
# see it yet. Worth knowing, not hidden.
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    from_admin: bool = False

    sender_name: str
    sender_user_id: Optional[int] = Field(default=None, foreign_key="user.id")

    # Only set on an Admin-composed (from_admin=True) message.
    recipient_name: Optional[str] = None
    recipient_user_id: Optional[int] = Field(default=None, foreign_key="user.id")

    subject: str
    body: str
    is_read: bool = False

    reply_body: Optional[str] = None
    replied_at: Optional[datetime] = None
