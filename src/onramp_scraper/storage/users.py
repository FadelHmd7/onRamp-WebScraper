"""Users: the seeded admin and viewer logins.

``seed_users`` runs in the ``migrate`` service on every start. It creates
missing users and updates the password hash when the password in the
environment changed, so rotating a password is: change the variable,
``docker compose up``. A user's role always comes from the seed.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp_scraper.passwords import hash_password, verify_password
from onramp_scraper.storage.models import User


@dataclass(frozen=True)
class SeedUser:
    username: str
    password: str
    role: str


def get_user(session: Session, username: str) -> User | None:
    return session.scalars(select(User).where(User.username == username)).one_or_none()


def seed_users(session: Session, users: list[SeedUser]) -> list[str]:
    """Create or update each user. Returns what changed, for the log."""
    changes = []
    for seed in users:
        user = get_user(session, seed.username)
        if user is None:
            session.add(
                User(
                    username=seed.username,
                    password_hash=hash_password(seed.password),
                    role=seed.role,
                )
            )
            changes.append(f"created {seed.role} {seed.username!r}")
            continue
        if not verify_password(user.password_hash, seed.password):
            user.password_hash = hash_password(seed.password)
            changes.append(f"updated the password of {seed.username!r}")
        if user.role != seed.role:
            user.role = seed.role
            changes.append(f"set {seed.username!r} to role {seed.role}")
    return changes
