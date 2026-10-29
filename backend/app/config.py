import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    a_mode: str = "mock"
    c_mode: str = "mock"
    d_mode: str = "mock"
    storage_dir: Path = Path(".runtime/uploads")
    # Retained only for explicit historical experiment fixtures, never used by B's factory.
    database_path: Path | None = None
    lease_seconds: int = 120
    allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    auth_mode: str = "development"

    def validate(self):
        if self.auth_mode not in {"development", "password"}:
            raise RuntimeError("Unsupported authentication mode")
        if self.auth_mode == "password" and self.a_mode == "mock":
            raise RuntimeError("Password authentication requires persistent data")
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
            from urllib.parse import urlsplit

            if self.auth_mode != "password":
                raise RuntimeError("Production authentication requires explicit password sessions")
            if not self.allowed_origins or any(
                urlsplit(o).scheme != "https"
                or not urlsplit(o).hostname
                or urlsplit(o).username
                or urlsplit(o).password
                or urlsplit(o).path
                or urlsplit(o).query
                or urlsplit(o).fragment
                for o in self.allowed_origins
            ):
                raise RuntimeError("Production requires explicit HTTPS origins")

    @classmethod
    def from_env(cls):
        return cls(
            environment=os.getenv("PAPERPILOT_ENV", "development"),
            a_mode=os.getenv("PAPERPILOT_A_MODE", "mock"),
            c_mode=os.getenv("PAPERPILOT_C_MODE", "mock"),
            d_mode=os.getenv("PAPERPILOT_D_MODE", "mock"),
            auth_mode=os.getenv("PAPERPILOT_AUTH_MODE", "development"),
            storage_dir=Path(os.getenv("PAPERPILOT_STORAGE_DIR", ".runtime/uploads")),
            allowed_origins=tuple(
                os.getenv("PAPERPILOT_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(
                    ","
                )
            ),
        )
