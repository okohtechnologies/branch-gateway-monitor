"""Password hashing (argon2), session tokens (JWT), login throttling."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

COOKIE = "ipmon_session"
_hasher = PasswordHasher()
# Verified against when the email is unknown, so timing does not reveal valid emails.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def make_token(secret: str, email: str, role: str, hours: int, jti: str | None = None) -> str:
    """``jti`` is the server-side session id; the token is only honoured while that session is active."""
    exp = datetime.now(timezone.utc) + timedelta(hours=hours)
    claims = {"sub": email, "role": role, "exp": exp}
    if jti is not None:
        claims["jti"] = jti
    return jwt.encode(claims, secret, algorithm="HS256")


def read_token(secret: str, token: str | None) -> dict | None:
    if not token:
        return None
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    # Tokens issued before sessions existed carry no session id: they can no longer be revoked, so reject them.
    return claims if claims.get("jti") else None


class LoginThrottle:
    """Blocks a client after ``limit`` failures within ``window`` seconds."""

    def __init__(self, limit: int = 5, window: float = 300.0):
        self.limit, self.window = limit, window
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._recent(key)) >= self.limit

    def record_failure(self, key: str) -> None:
        self._recent(key)
        self._failures.setdefault(key, []).append(time.monotonic())

    def retry_after(self, key: str) -> int:
        """Seconds until a blocked client may try again (0 if not blocked)."""
        recent = self._recent(key)
        if len(recent) < self.limit:
            return 0
        # Unblocked once enough old failures age out to drop below the limit.
        expires = recent[len(recent) - self.limit] + self.window
        return max(1, int(expires - time.monotonic()) + 1)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
