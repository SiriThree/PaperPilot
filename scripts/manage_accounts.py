"""Operator-only account provisioning. Password values stay in named secret variables/files."""

import argparse
from pathlib import Path
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.accounts import AccountAdmin  # noqa: E402
from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402
from app.data.settings import DataSettings  # noqa: E402
from app.secrets import read_secret  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "password", "disable"])
    parser.add_argument("--username")
    parser.add_argument("--display-name")
    parser.add_argument("--user", type=UUID)
    parser.add_argument("--password-env")
    options = parser.parse_args()
    database = None
    try:
        database = PostgresDatabase(ConnectionConfig.from_env())
        admin = AccountAdmin(database, DataSettings.from_env())
        password = None
        if options.command != "disable":
            if not options.password_env or re.fullmatch(r"[A-Z][A-Z0-9_]*", options.password_env) is None:
                raise ValueError("An explicit password secret reference is required")
            password = read_secret(options.password_env)
        if options.command == "create":
            if not options.username or not options.display_name:
                raise ValueError("Account name and display name are required")
            user_id = admin.create_account(options.username, options.display_name, password)
            print("Account created:", user_id)
        elif options.command == "password" and options.user is not None:
            admin.change_password(options.user, password)
            print("Password changed; previous sessions revoked")
        elif options.command == "disable" and options.user is not None:
            admin.disable_account(options.user)
            print("Account disabled; previous sessions revoked")
        else:
            raise ValueError("An explicit user UUID is required")
    except Exception:
        print(
            "Account operation not confirmed; inspect account/audit state before retrying. No secrets displayed.",
            file=sys.stderr,
        )
        return 1
    finally:
        if database is not None:
            database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
