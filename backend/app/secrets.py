"""Explicit environment or mounted secret file; never search for credentials."""

from pathlib import Path
import os


def read_secret(name, environ=None):
    env = os.environ if environ is None else environ
    value, filename = env.get(name), env.get(name + "_FILE")
    if bool(value) == bool(filename):
        raise ValueError("Provide exactly one secret value or file")
    if filename:
        path = Path(filename)
        if not path.is_absolute() or not path.is_file() or path.stat().st_size > 8192:
            raise ValueError("Invalid explicit secret file")
        value = path.read_text(encoding="utf-8").removesuffix("\n").removesuffix("\r")
    if not value or "\x00" in value or len(value.encode("utf-8")) > 8192:
        raise ValueError("Invalid secret value")
    return value
