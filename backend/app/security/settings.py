"""Bounded, strict operator configuration; secrets are resolved from environment names only."""

import json
import os
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.security.executor import ReaderConfig


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatabaseTarget(_Config):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(strict=True, ge=1, le=65535)
    dbname: str = Field(min_length=1, max_length=63)


class Login(_Config):
    user: str = Field(pattern=r"^pp_(reader|audit)_[a-z0-9_]{1,48}$")
    password_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")


class ProjectReader(Login):
    project_id: UUID


class SecuritySettings(_Config):
    database: DatabaseTarget
    audit: Login
    readers: list[ProjectReader] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def separate_identities(self):
        identities = [self.audit, *self.readers]
        if len({reader.project_id for reader in self.readers}) != len(self.readers):
            raise ValueError("Duplicate project")
        if len({login.user for login in identities}) != len(identities):
            raise ValueError("Audit and project logins must be distinct")
        if len({login.password_env for login in identities}) != len(identities):
            raise ValueError("Each identity requires its own secret reference")
        if not self.audit.user.startswith("pp_audit_") or any(
            not reader.user.startswith("pp_reader_") for reader in self.readers
        ):
            raise ValueError("Use separately provisioned service logins")
        return self

    @classmethod
    def from_env(cls):
        try:
            path = Path(os.environ["PAPERPILOT_SECURITY_CONFIG"])
            if not path.is_absolute():
                raise ValueError("An absolute operator-controlled configuration path is required")
            with path.open("rb") as stream:
                body = stream.read(64 * 1024 + 1)
            if len(body) > 64 * 1024:
                raise ValueError("Configuration is too large")

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate configuration key")
                    result[key] = value
                return result

            return cls.model_validate(json.loads(body.decode("utf-8-sig"), object_pairs_hook=unique))
        except Exception:
            raise RuntimeError("Invalid trusted security configuration; no Mock fallback") from None

    def resolve(self, data_config):
        from app.data.database import ConnectionConfig

        try:
            if (self.database.host, self.database.port, self.database.dbname) != (
                data_config.host,
                data_config.port,
                data_config.dbname,
            ):
                raise ValueError("Business and security database targets differ")
            identities = [self.audit, *self.readers]
            if data_config.user in {login.user for login in identities}:
                raise ValueError("Business and security identities must be distinct")
            passwords = [os.environ[login.password_env] for login in identities]
            if (
                any(not password or "\x00" in password for password in passwords)
                or len(set([data_config.password, *passwords])) != len(passwords) + 1
            ):
                raise ValueError("Independent, nonempty identity secrets are required")
            audit = ConnectionConfig(
                host=self.database.host,
                port=self.database.port,
                dbname=self.database.dbname,
                user=self.audit.user,
                password=passwords[0],
                connect_timeout=3,
            )
            readers = {
                reader.project_id: ReaderConfig(
                    self.database.host, self.database.port, self.database.dbname, reader.user, password
                )
                for reader, password in zip(self.readers, passwords[1:], strict=True)
            }
            return audit, readers
        except Exception:
            raise RuntimeError("Invalid or missing security identity secrets; no Mock fallback") from None
