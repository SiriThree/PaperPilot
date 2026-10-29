"""Day-zero data defaults, independent of B's Mock application factory."""

import os
from dataclasses import dataclass

# mock_ai.py generates no embeddings. This is a synthetic preflight dimension only.
# 待 C 确认模型后改此行；已有 vector 列及数据须迁移、重新嵌入，不能只改常量。
EMBEDDING_DIM = 3
DEFAULT_LEASE_SECONDS = 120
# Demo policy: retain idempotency records indefinitely; no automatic purge.
IDEMPOTENCY_RETENTION_SECONDS: int | None = None


@dataclass(frozen=True)
class DataSettings:
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    idempotency_retention_seconds: int | None = IDEMPOTENCY_RETENTION_SECONDS
    max_automatic_attempts: int = 3

    def __post_init__(self):
        if type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 3600:
            raise ValueError("PAPERPILOT_LEASE_SECONDS must be an integer between 1 and 3600")
        if self.idempotency_retention_seconds is not None:
            raise ValueError("Demo idempotency records must not expire automatically")
        if type(self.max_automatic_attempts) is not int or not 1 <= self.max_automatic_attempts <= 100:
            raise ValueError("PAPERPILOT_MAX_AUTOMATIC_ATTEMPTS must be between 1 and 100")

    @classmethod
    def from_env(cls):
        value = os.getenv("PAPERPILOT_LEASE_SECONDS", str(DEFAULT_LEASE_SECONDS))
        try:
            seconds = int(value)
            attempts = int(os.getenv("PAPERPILOT_MAX_AUTOMATIC_ATTEMPTS", "3"))
        except ValueError:
            raise ValueError("PAPERPILOT_LEASE_SECONDS must be an integer between 1 and 3600") from None
        return cls(lease_seconds=seconds, max_automatic_attempts=attempts)
