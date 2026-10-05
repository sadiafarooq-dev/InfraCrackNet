# User model
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str
    role: str
    employee_code: str
    password_hash: Optional[str] = None
    status: str = "Active"

    # Forgot Password: a one-time code + when it stops being valid.
    # Both get cleared again the moment the password is actually reset.
    reset_token: Optional[str] = None
    reset_token_expires: Optional[datetime] = None

    # Profile picture the user uploaded on the Profile page (a filename
    # inside the shared /uploads folder, same as report photos). None
    # until they upload one -- the navbar/sidebar/profile page all fall
    # back to a plain initials circle when this is empty.
    profile_photo_path: Optional[str] = None

    # Cover/banner photo shown behind the avatar at the top of the Profile
    # page -- same idea as profile_photo_path above (a filename inside the
    # shared /uploads folder). None until they upload one, in which case
    # the Profile page falls back to the plain navy banner with the
    # crack-line pattern drawn into it.
    cover_photo_path: Optional[str] = None

    # Whether this person has been through the 3-screen Welcome tour yet
    # (see shared/routes.py) -- shown once, right after their very first
    # login, then never again. Used for both Inspector and Engineer (each
    # sees their own version of the tour's wording); not used for Admin.
    has_seen_onboarding: bool = False

    # The last time this person opened their real Notifications page (see
    # shared/routes.py) -- used to tell which notifications are new.
    notifications_seen_at: Optional[datetime] = None

    @property
    def initials(self) -> str:
        """Two-letter fallback shown in the avatar circle when there's no
        photo yet, e.g. "Ahmed Khan" -> "AK". Matches the real Figma design,
        which shows initials in every Profile mockup."""
        parts = [p for p in self.name.split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][0].upper()
        return (parts[0][0] + parts[-1][0]).upper()
