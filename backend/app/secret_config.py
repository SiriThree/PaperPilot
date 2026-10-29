"""Opt-in protected file inputs for local test credentials.

POSIX files require strict owner/mode checks. Windows files require a protected,
current-user-only DACL. This is not a secrets service or online rotation mechanism.
"""

import os
import stat
import sys


def secret_value(environ, key, *, limit=65536):
    value = environ.get(key, "")
    path = environ.get(key + "_FILE", "")
    if value and path:
        raise RuntimeError("Conflicting secret configuration")
    if not path:
        return value
    if os.name == "nt" and sys.platform == "win32":
        from app.windows_secret import read_windows_secret

        return read_windows_secret(path, limit)
    if os.name != "posix" or not os.path.isabs(path) or not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("Protected secret file is unavailable on this platform")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("unprotected secret file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(limit + 1)
        if not raw or len(raw) > limit or b"\x00" in raw:
            raise ValueError("invalid secret file")
        text = raw.decode("utf-8")
        return text[:-2] if text.endswith("\r\n") else text[:-1] if text.endswith("\n") else text
    except (OSError, ValueError, UnicodeError):
        raise RuntimeError("Protected secret file could not be read safely") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
