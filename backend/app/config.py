import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    a_mode: str = "mock"
    c_mode: str = "mock"
    d_mode: str = "mock"
    auth_mode: str = "development"
    auth_database: str = "shared"
    auth_lock_owner: str = ""
    session_idle_seconds: int = 1800
    session_absolute_seconds: int = 28800
    auth_login_limit: int = 10
    auth_login_window_seconds: int = 300
    security_execution: str = "disabled"
    audit_preflight: str = "disabled"
    business_privilege_preflight: str = "disabled"
    audit_trusted_owner: str = ""
    scoped_audit: str = "disabled"
    storage_dir: Path = Path(".runtime/uploads")
    # Retained only for explicit historical experiment fixtures, never used by B's factory.
    database_path: Path | None = None
    lease_seconds: int = 120
    allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    def validate(self):
        if self.scoped_audit not in {"disabled", "isolated"}:
            raise RuntimeError("Unsupported scoped audit mode")
        if self.scoped_audit == "isolated" and (
            self.environment != "test"
            or self.a_mode not in {"persistent", "real"}
            or self.auth_mode != "credentials"
        ):
            raise RuntimeError(
                "Scoped audit requires isolated test mode, persistent data and credential authentication"
            )
        if self.business_privilege_preflight not in {"disabled", "required"}:
            raise RuntimeError("Unsupported business privilege preflight mode")
        if self.business_privilege_preflight == "required" and (
            self.environment != "test" or self.audit_preflight != "required"
        ):
            raise RuntimeError("Business grant preflight requires isolated test and audit preflight")
        if self.audit_preflight not in {"disabled", "required"}:
            raise RuntimeError("Unsupported audit preflight mode")
        if self.audit_preflight == "required" and (
            self.a_mode not in {"persistent", "real"} or not self.audit_trusted_owner.strip()
        ):
            raise RuntimeError("Audit preflight requires persistent data and an independently trusted owner")
        if self.auth_database not in {"shared", "isolated"}:
            raise RuntimeError("Unsupported authentication database mode")
        if self.auth_database == "isolated" and (
            self.environment != "test"
            or self.auth_mode != "credentials"
            or self.a_mode not in {"persistent", "real"}
            or not self.auth_lock_owner.strip()
        ):
            raise RuntimeError(
                "Isolated authentication database requires test mode, credential authentication and a trusted lock owner"
            )
        if self.auth_mode not in {"development", "credentials"}:
            raise RuntimeError("Authentication mode must be explicitly configured")
        if self.auth_mode == "development" and self.environment not in {"development", "test"}:
            raise RuntimeError("Production authentication unavailable; development identity forbidden")
        if self.auth_mode == "credentials" and self.a_mode not in {"persistent", "real"}:
            raise RuntimeError("Credential authentication requires persistent data")
        if not 60 <= self.session_idle_seconds <= self.session_absolute_seconds <= 604800:
            raise RuntimeError("Invalid session timeout configuration")
        if not 1 <= self.auth_login_limit <= 1000 or not 10 <= self.auth_login_window_seconds <= 86400:
            raise RuntimeError("Invalid login rate limit configuration")
        if not self.allowed_origins or any(not origin or "*" in origin for origin in self.allowed_origins):
            raise RuntimeError("Explicit trusted origins required")
        if self.security_execution not in {"disabled", "candidate"}:
            raise RuntimeError("Unsupported security execution mode")
        if self.security_execution == "candidate" and (
            self.environment != "test" or self.a_mode not in {"persistent", "real"} or self.d_mode != "real"
        ):
            raise RuntimeError("Candidate execution requires test environment and real data and security")
        if self.environment not in {"development", "test", "production"}:
            raise RuntimeError("Unsupported application environment")
        modes = (self.a_mode, self.c_mode, self.d_mode)
        if self.a_mode not in {"mock", "persistent", "real"} or any(
            m not in {"mock", "real"} for m in (self.c_mode, self.d_mode)
        ):
            raise RuntimeError("A mode must be mock/persistent/real; C/D mode must be mock/real")
        if self.environment == "production" and "mock" in modes:
            raise RuntimeError("Production forbids Mock adapters")
        if self.environment == "production":
            raise RuntimeError(
                "Production authentication and deployment are not implemented; startup refused"
            )

    @classmethod
    def from_env(cls):
        return cls(
            environment=os.getenv("PAPERPILOT_ENV", "development"),
            a_mode=os.getenv("PAPERPILOT_A_MODE", "mock"),
            c_mode=os.getenv("PAPERPILOT_C_MODE", "mock"),
            d_mode=os.getenv("PAPERPILOT_D_MODE", "mock"),
            auth_mode=os.getenv("PAPERPILOT_AUTH_MODE", "unconfigured"),
            auth_database=os.getenv("PAPERPILOT_AUTH_DATABASE", "shared"),
            auth_lock_owner=os.getenv("PAPERPILOT_AUTH_LOCK_OWNER", ""),
            session_idle_seconds=int(os.getenv("PAPERPILOT_SESSION_IDLE_SECONDS", "1800")),
            session_absolute_seconds=int(os.getenv("PAPERPILOT_SESSION_ABSOLUTE_SECONDS", "28800")),
            auth_login_limit=int(os.getenv("PAPERPILOT_AUTH_LOGIN_LIMIT", "10")),
            auth_login_window_seconds=int(os.getenv("PAPERPILOT_AUTH_LOGIN_WINDOW_SECONDS", "300")),
            security_execution=os.getenv("PAPERPILOT_SECURITY_EXECUTION", "disabled"),
            audit_preflight=os.getenv("PAPERPILOT_AUDIT_PREFLIGHT", "disabled"),
            business_privilege_preflight=os.getenv("PAPERPILOT_BUSINESS_PRIVILEGE_PREFLIGHT", "disabled"),
            audit_trusted_owner=os.getenv("PAPERPILOT_AUDIT_TRUSTED_OWNER", ""),
            scoped_audit=os.getenv("PAPERPILOT_SCOPED_AUDIT", "disabled"),
            storage_dir=Path(os.getenv("PAPERPILOT_STORAGE_DIR", ".runtime/uploads")),
            allowed_origins=tuple(
                os.getenv("PAPERPILOT_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(
                    ","
                )
            ),
        )
