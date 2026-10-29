"""Explicitly set the first password of an existing user; never resets passwords."""

import argparse
import getpass

from app.data.authentication import PostgresSessions
from app.data.database import ConnectionConfig, PostgresDatabase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", required=True)
    parser.add_argument("--confirm-database", required=True, help="Explicit database name from operator")
    parser.add_argument("--replace-existing", action="store_true", help="Reset password and revoke sessions")
    args = parser.parse_args()
    config = ConnectionConfig.from_env()
    if args.confirm_database != config.dbname:
        raise SystemExit("Database confirmation does not match configured database")
    password = getpass.getpass("Initial password: ")
    confirm = getpass.getpass("Repeat password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match")
    database = PostgresDatabase(config)
    try:
        database.ready()
        sessions = PostgresSessions(database)
        if args.replace_existing:
            sessions.replace_password(args.username, password)
        else:
            sessions.initialize_password(args.username, password)
    finally:
        database.close()
    print("Password initialized")


if __name__ == "__main__":
    main()
