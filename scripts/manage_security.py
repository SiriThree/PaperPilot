"""Explicit PostgreSQL security lifecycle. Passwords/DSNs are read only from named environment variables."""

import argparse
import os
from pathlib import Path
import re
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.security.provision import (
    create_login,
    install,
    revoke_login,
    rotate_login,
    upgrade_business_registry,
)  # noqa: E402


def secret(name):
    if not name or re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None or not os.environ.get(name):
        raise ValueError("A named secret environment variable is missing")
    return os.environ[name]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "install",
            "business-upgrade",
            "reader-create",
            "audit-create",
            "business-create",
            "rotate",
            "revoke",
        ],
    )
    parser.add_argument(
        "--admin-dsn-env", required=True, help="Name of the administrator DSN environment variable"
    )
    parser.add_argument("--role")
    parser.add_argument("--project", type=UUID)
    parser.add_argument(
        "--password-env", help="Name of the new password environment variable; never the password itself"
    )
    parser.add_argument(
        "--existing-views",
        action="store_true",
        help="Install only the lifecycle registry over already installed D views",
    )
    options = parser.parse_args()
    try:
        import psycopg

        if options.command not in ("install", "business-upgrade") and not options.role:
            raise ValueError("An explicit managed role is required")
        if options.command == "reader-create" and options.project is None:
            raise ValueError("An explicit project is required")
        if options.project is not None and options.command != "reader-create":
            raise ValueError("Project binding is only accepted for reader creation")
        if options.existing_views and options.command != "install":
            raise ValueError("Existing views flag is only accepted for install")
        with psycopg.connect(secret(options.admin_dsn_env), autocommit=True) as connection:
            if options.command == "install":
                install(connection, existing_views=options.existing_views)
            elif options.command == "business-upgrade":
                with connection.transaction():
                    upgrade_business_registry(connection)
            elif options.command in ("reader-create", "audit-create", "business-create"):
                create_login(
                    connection,
                    options.role,
                    secret(options.password_env),
                    project_id=options.project,
                    business=options.command == "business-create",
                )
            elif options.command == "rotate":
                rotate_login(connection, options.role, secret(options.password_env))
            else:
                revoke_login(connection, options.role)
    except Exception:
        # A rotation/revocation may have committed before a disconnect or cleanup failure.
        print(
            "Security operation not confirmed; inspect login/registry state before retrying. No secrets displayed.",
            file=sys.stderr,
        )
        return 1
    print("Security operation confirmed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
