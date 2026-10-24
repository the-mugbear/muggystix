"""The one shape an answer names a person in (2.530.0): ``{id, name,
profile_image}`` — the name as every page shows it (full name, else
username) and the profile picture's SHA-256, or null.  An answer that names a
person adopts it as pictures reach that place (presentation mode first; the
older answers still spell ``author_name`` / ``created_by_name`` their own way).
"""
from typing import Optional

from app.db.models_auth import User


def display_name(user: Optional[User]) -> Optional[str]:
    if user is None:
        return None
    return (user.full_name or "").strip() or user.username


def person_of(user: Optional[User]) -> Optional[dict]:
    if user is None:
        return None
    return {"id": user.id, "name": display_name(user), "profile_image": user.profile_image_sha256}
