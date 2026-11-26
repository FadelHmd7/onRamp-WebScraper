"""Password hashing with Argon2id.

Passwords are stored only as Argon2id hashes (argon2-cffi defaults: salted
and memory-hard, so a stolen users table is slow to crack).
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()
# Verified when the username does not exist, so a wrong username takes as
# long as a wrong password and does not reveal which usernames exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False
