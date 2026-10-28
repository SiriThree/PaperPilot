"""Explicit D development composition; set PAPERPILOT_D_MODE=real before starting."""

from fastapi import FastAPI

from app.config import Settings
from app.main import create_app as create_business_app
from app.security import build_security


def create_app() -> FastAPI:
    return create_business_app(Settings.from_env(), security_factory=build_security)
