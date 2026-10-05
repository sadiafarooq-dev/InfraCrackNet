# Project model -- a specific site being tracked over time (e.g. "Metro
# Bridge, North Span" or "City Hall Building"), which collects many crack
# reports across its lifetime. Created and managed by Admin only (see
# admin/routes.py) -- Inspectors and Engineers never create their own, so the
# company keeps one clean, shared list instead of duplicate/ad-hoc entries.
#
# Who can see a Project's reports is controlled by two small link tables
# below (ProjectInspector / ProjectEngineer), picked by Admin when creating
# or editing a Project:
#   - An Inspector only sees the Projects they're personally assigned to when
#     starting a New Report (inspector/routes.py).
#   - An Engineer only sees reports belonging to Projects they're personally
#     assigned to, in their Review Queue / All Reports (engineer/routes.py).
#   - Admin always sees every Project and every report, regardless of
#     assignment -- Admin needs full oversight.
# One person can be assigned to more than one Project, and one Project can
# have more than one Inspector and more than one Engineer -- a real site
# often has several staff working it over time.
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    # Short free-text location/description, e.g. "Sector G-9, Main Blvd" --
    # optional, just to help Admin and the assigned staff tell similarly-
    # named Projects apart at a glance.
    location: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectInspector(SQLModel, table=True):
    """Which Inspectors are assigned to which Project -- a plain many-to-many
    link table, one row per (project, inspector) pairing."""
    project_id: int = Field(foreign_key="project.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)


class ProjectEngineer(SQLModel, table=True):
    """Same idea as ProjectInspector above, for Engineers."""
    project_id: int = Field(foreign_key="project.id", primary_key=True)
    user_id: int = Field(foreign_key="user.id", primary_key=True)
