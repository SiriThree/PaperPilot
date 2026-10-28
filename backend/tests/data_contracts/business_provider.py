"""A's shared contracts with a provisioned non-owner business login, on disposable databases."""

from contextlib import contextmanager
from dataclasses import replace
import secrets
from uuid import uuid4

import psycopg

from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.data.provider import build_harness as admin_harness
from app.security.provision import create_login, install


@contextmanager
def build_harness(*, tmp_path):
    with admin_harness(tmp_path=tmp_path) as harness:
        admin = harness.data
        role, password = "pp_business_" + uuid4().hex, secrets.token_urlsafe(32)
        with psycopg.connect(**admin.database.config.conninfo(), autocommit=True) as connection:
            install(connection)
            create_login(connection, role, password, business=True)
        config = replace(admin.database.config, user=role, password=password)
        databases = []

        def reopen():
            database = PostgresDatabase(config)
            databases.append(database)
            return PostgresData(database, admin.settings, before_commit=admin._before_commit)

        harness.data = reopen()
        harness.reopen = reopen
        try:
            yield harness
        finally:
            for database in databases:
                database.close()
