"""Database-neutral composition seam. A owns initialization and resource lifecycle."""

from collections.abc import Callable
from dataclasses import dataclass

from app.adapters.ports import DataPort


@dataclass(frozen=True)
class DataBinding:
    data: DataPort
    ready: Callable[[], bool | None]
    close: Callable[[], None] | None = None
