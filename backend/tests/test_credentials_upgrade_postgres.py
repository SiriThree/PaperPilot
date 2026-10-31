"""D's released schema-3/scrypt deployment upgrades to B credentials without reseeding."""

import os
from pathlib import Path
import sys
from uuid import uuid4

from argon2 import PasswordHasher
import psycopg
import pytest

from app.data.adapter import PostgresData
from app.data.database import MIGRATIONS, PostgresDatabase
from app.data.provider import disposable_database
from app.data.settings import DataSettings
from app.passwords import hash_password
from app.schemas import ProjectCreate
from app.security.provision import install
from test_parallel_migrations_postgres import install_baseline, BASELINES

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from local_deployment import upgrade_database  # noqa: E402

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        os.getenv("PAPERPILOT_PHASE5A_ISOLATED") != "1", reason="Explicit disposable server required"
    ),
]


@pytest.mark.parametrize("wrong_password", [False, True])
def test_legacy_credentials_upgrade_is_explicit_and_preserves_business(wrong_password):
    with disposable_database("paperpilot_d_credentials_") as config:
        db = PostgresDatabase(config)
        try:
            install_baseline(db, BASELINES["index_recovery"])
            user_id, password = uuid4(), "A strong legacy deployment password"
            with psycopg.connect(**config.conninfo(), autocommit=True) as admin:
                install(admin)
                admin.execute(
                    (MIGRATIONS.parents[1] / "security/migrations/004_password_auth.sql").read_text(
                        encoding="utf-8"
                    )
                )
                with admin.transaction():
                    admin.execute(
                        "INSERT INTO users(user_id,username,display_name) VALUES(%s,'owner','Owner')",
                        (user_id,),
                    )
                    admin.execute(
                        "INSERT INTO paperpilot_auth.accounts(user_id,password_hash,credential_version) VALUES(%s,%s,%s)",
                        (user_id, hash_password(password), uuid4()),
                    )
            data = PostgresData(db, DataSettings())
            project = data.create_project(user_id, ProjectCreate(name="Keep this project"), uuid4())
            if wrong_password:
                with pytest.raises(ValueError):
                    upgrade_database(config, user_id, "incorrect password")
                with db.transaction() as conn:
                    assert (
                        conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()["v"] == 3
                    )
                return
            upgrade_database(config, user_id, password)
            with db.transaction() as conn:
                assert conn.execute("SELECT max(version) AS v FROM schema_migrations").fetchone()["v"] == 7
                row = conn.execute(
                    "SELECT password_hash,disabled FROM users WHERE user_id=%s", (user_id,)
                ).fetchone()
                assert PasswordHasher().verify(row["password_hash"], password) and not row["disabled"]
                assert (
                    conn.execute(
                        "SELECT count(*) AS n FROM audit_events WHERE action='auth.credential.migrated'"
                    ).fetchone()["n"]
                    == 1
                )
            assert data.projects(user_id)[0].project_id == project.project_id
        finally:
            db.close()
