"""Explicit, additive PostgreSQL lifecycle commands for member A's data layer.

Reads PAPERPILOT_DATA_HOST/PORT/USER/PASSWORD/DBNAME (never hard-coded credentials).
Never resets data; refuses an unrecognized non-empty database, mirroring the SQLite tool.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402
from app.data.settings import DataSettings  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "seed-demo", "seed-demo-project", "check"])
    options = parser.parse_args()
    database = PostgresDatabase(ConnectionConfig.from_env(os.environ))
    manifest = None
    try:
        if options.command == "migrate":
            database.migrate()
        elif options.command == "seed-demo":
            database.seed_development_users()
        elif options.command == "seed-demo-project":
            from app.data.demo_seed import seed_demo_project

            manifest = seed_demo_project(database, log=lambda message: None)
        else:
            database.ready()
    finally:
        database.close()
    settings = DataSettings.from_env()
    print(
        f"PostgreSQL {options.command}: ok (lease={settings.lease_seconds}s,"
        f" idempotency_retention={settings.idempotency_retention_seconds})"
    )
    if manifest is not None:
        import json

        print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
