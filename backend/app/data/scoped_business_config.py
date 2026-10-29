"""Protected manifest for the explicit, local-only scoped business pilot.

This loads already-provisioned credentials; it does not create, rotate or revoke roles.
"""

import json
from dataclasses import replace
from uuid import UUID

from app.data.scoped_business import (
    ScopedBusinessConfig,
    ScopedBusinessReader,
    ScopedBusinessRouter,
    ScopedBusinessWriter,
    ScopedReportWriter,
)
from app.secret_config import secret_value

PREFIX = "PAPERPILOT_SCOPED_BUSINESS_"
PROFILES = {"read": ScopedBusinessReader, "upload": ScopedBusinessWriter, "report": ScopedReportWriter}


def scoped_business_configurations(environ, data_connection, *, forbidden_users=()):
    """Parse a protected, bounded server-owned manifest without exposing secrets."""
    allowed_keys = {
        PREFIX + name for name in ("ISOLATED_TEST", "BINDINGS_FILE", "FUNCTION_OWNER", "BINDING_OWNER")
    }
    if any(key.startswith(PREFIX) and value and key not in allowed_keys for key, value in environ.items()):
        raise RuntimeError("Unexpected scoped business configuration")
    if environ.get(PREFIX + "ISOLATED_TEST") != "1" or not environ.get(PREFIX + "BINDINGS_FILE"):
        raise RuntimeError("Explicit isolated scoped business configuration is required")
    owner = environ.get(PREFIX + "FUNCTION_OWNER", "")
    binding_owner = environ.get(PREFIX + "BINDING_OWNER", "")
    if not owner.strip() or not binding_owner.strip():
        raise RuntimeError("Explicit scoped business owners are required")
    raw = secret_value(environ, PREFIX + "BINDINGS")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = value
        return result

    try:
        entries = json.loads(raw, object_pairs_hook=unique_object)
        if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
            raise ValueError("Invalid mapping count")
        if data_connection.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Non-local target")
        forbidden = {data_connection.user, owner, binding_owner, *forbidden_users}
        mappings, identities, logins = [], set(), set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {
                "db_user",
                "db_password",
                "user_id",
                "project_id",
                "profile",
            }:
                raise ValueError("Invalid mapping fields")
            if not all(isinstance(value, str) and value.strip() for value in entry.values()):
                raise ValueError("Invalid mapping values")
            user_id, project_id = UUID(entry["user_id"]), UUID(entry["project_id"])
            login, profile = entry["db_user"], entry["profile"]
            if (
                login in forbidden
                or login in logins
                or (user_id, project_id) in identities
                or profile not in PROFILES
            ):
                raise ValueError("Duplicate or forbidden identity")
            logins.add(login)
            identities.add((user_id, project_id))
            connection = replace(data_connection, user=login, password=entry["db_password"])
            mappings.append(
                (ScopedBusinessConfig(connection, user_id, project_id, owner, binding_owner), profile)
            )
        return mappings
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise RuntimeError("Invalid isolated scoped business configuration") from None


def build_scoped_business_router(mappings):
    readers = []
    try:
        for config, profile in mappings:
            readers.append(PROFILES[profile](config))
        return ScopedBusinessRouter(readers)
    except BaseException:
        for reader in reversed(readers):
            try:
                reader.close()
            except BaseException:
                pass
        raise
