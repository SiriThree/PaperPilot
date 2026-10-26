"""Explicit historical test composition. Never registered by the business application."""

import os
from pathlib import Path

from app.adapters.data_binding import DataBinding
from app.config import Settings
from app.main import create_app
from app.persistence.adapter import SQLiteData
from app.persistence.database import SQLiteDatabase


def build_legacy_binding(settings, path):
    settings.validate()
    if settings.environment != "test":
        raise RuntimeError("Historical database fixture requires isolated test mode")
    database = SQLiteDatabase(path)
    database.migrate()
    database.seed_development_users()
    return DataBinding(SQLiteData(database, lease_seconds=1), database.ready)


def create_legacy_app():
    settings = Settings.from_env()
    # The test must explicitly supply a temporary path. Never use the user's runtime file.
    path = Path(os.environ["PAPERPILOT_TEST_DATABASE_PATH"])
    return create_app(settings, data_binding=build_legacy_binding(settings, path))
