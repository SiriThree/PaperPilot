"""Opt-in startup of the actual configured AI adapter with a disposable PostgreSQL database."""

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from app.ai.adapter import RealAI
from app.config import Settings
from app.data.database import PostgresDatabase
from app.data.provider import disposable_database
from app.dev_composition import create_app

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_REAL_AI_STARTUP_ISOLATED") != "1"
        or os.getenv("PAPERPILOT_PHASE5A_ISOLATED") != "1",
        reason="Requires explicit isolated PostgreSQL and real AI startup opt-in",
    ),
]


def test_actual_ai_and_local_embedding_startup(monkeypatch, tmp_path):
    with disposable_database("paperpilot_real_ai_start_") as config:
        db = PostgresDatabase(config)
        try:
            db.migrate()
        finally:
            db.close()
        for key, value in {
            "PAPERPILOT_DATA_HOST": config.host,
            "PAPERPILOT_DATA_PORT": str(config.port),
            "PAPERPILOT_DATA_USER": config.user,
            "PAPERPILOT_DATA_PASSWORD": config.password,
            "PAPERPILOT_DATA_DBNAME": config.dbname,
        }.items():
            monkeypatch.setenv(key, value)
        app = create_app(
            Settings(
                environment="test", a_mode="persistent", c_mode="real", d_mode="mock", storage_dir=tmp_path
            )
        )
        with TestClient(app) as client:
            assert client.get("/api/v1/health").json()["modes"] == {
                "a": "persistent",
                "c": "real",
                "d": "mock",
            }
            ai = app.state.adapters.ai
            assert isinstance(ai, RealAI)
            assert ai.settings.embedding_backend == "fastembed"
            vectors = asyncio.run(ai.embedder.embed(["A fictional local embedding startup check."]))
            assert len(vectors) == 1 and len(vectors[0]) == ai.settings.embedding_dimensions
