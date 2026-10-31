"""Fixed-cost stdlib scrypt, following OWASP's N=2^17/r=8/p=1 alternative to Argon2id."""

import hashlib
import hmac
import re
import secrets
import threading

from app.errors import AppError

_workers = threading.BoundedSemaphore(2)


def _derive(password, salt):
    if not _workers.acquire(blocking=False):
        raise AppError(429, "AUTH_BUSY", "登录繁忙，请稍后重试")
    try:
        return hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=2**17, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32
        )
    finally:
        _workers.release()


def hash_password(password):
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise ValueError("Password must contain between 15 and 128 characters")
    salt = secrets.token_bytes(16)
    digest = _derive(password, salt)
    return "scrypt-v1$" + salt.hex() + "$" + digest.hex()


def verify_password(password, encoded):
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        return False
    if (
        not isinstance(encoded, str)
        or re.fullmatch(r"scrypt-v1\$[a-f0-9]{32}\$[a-f0-9]{64}", encoded) is None
    ):
        return False
    _, salt, expected = encoded.split("$")
    return hmac.compare_digest(_derive(password, bytes.fromhex(salt)), bytes.fromhex(expected))
