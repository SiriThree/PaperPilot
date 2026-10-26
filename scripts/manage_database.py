"""Explicit, additive SQLite lifecycle commands. Never resets data."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.persistence.database import SQLiteDatabase  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "seed-demo", "check"])
    parser.add_argument(
        "--database", type=Path, required=True, help="Explicit SQLite path; never inferred from credentials"
    )
    options = parser.parse_args()
    database = SQLiteDatabase(options.database)
    if options.command == "migrate":
        database.migrate()
    elif options.command == "seed-demo":
        database.seed_development_users()
    else:
        database.ready()
    print(f"SQLite {options.command}: ok (development only)")


if __name__ == "__main__":
    main()
