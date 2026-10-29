"""Explicit, read-only preflight using the configured business database login."""

import argparse
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.data.audit_boundary import verify_audit_boundary  # noqa: E402
from app.data.database import ConnectionConfig  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Check the audit database privilege boundary")
    parser.add_argument("--trusted-owner", required=True, help="Independently reviewed NOLOGIN owner role")
    args = parser.parse_args()
    try:
        config = ConnectionConfig.from_env()
        with psycopg.connect(**config.conninfo()) as connection:
            connection.read_only = True
            verify_audit_boundary(connection, trusted_owner=args.trusted_owner)
    except Exception:
        # Driver errors can contain connection details; never print them here.
        print(
            "Audit boundary verification failed; check operator configuration and permissions.",
            file=sys.stderr,
        )
        return 1
    print("Audit boundary verified; application project authorization remains required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
