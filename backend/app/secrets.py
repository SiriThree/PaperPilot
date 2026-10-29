"""Compatibility helper backed by B protected-file checks; never search for credentials."""

import os
from app.secret_config import secret_value


def read_secret(name, environ=None):
    env = os.environ if environ is None else environ
    try:
        value = secret_value(env, name, limit=8192)
        if not value or "\x00" in value or len(value.encode("utf-8")) > 8192:
            raise ValueError("Missing or invalid explicit secret")
        return value
    except RuntimeError:
        raise ValueError("Missing or invalid explicit secret") from None
