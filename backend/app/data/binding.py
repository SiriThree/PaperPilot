"""Member A's trusted composition entry: DataBinding assembly and app factory.

Usage (PowerShell, from the repository root):

    $env:PAPERPILOT_DATA_PASSWORD='...'   # no default; refused when empty
    $env:PAPERPILOT_DATA_DBNAME='paperpilot'
    $env:PAPERPILOT_A_MODE='persistent'
    .\\.venv\\Scripts\\python.exe -m uvicorn app.data.binding:create_app --factory --app-dir backend ...

Migrations and seed users stay explicit: run scripts/manage_postgres.py first.
Initialization failures are sanitized; no connection strings, credentials or SQL leak.
"""

import logging

from app.adapters.data_binding import DataBinding
from app.config import Settings
from app.data.adapter import PostgresData
from app.data.database import ConnectionConfig, PostgresDatabase
from app.data.settings import DataSettings
from app.errors import AppError
from app.main import create_app as create_business_app

LOGGER = logging.getLogger("paperpilot")


def compose_data_binding(config: ConnectionConfig, settings: DataSettings) -> DataBinding:
    database = PostgresDatabase(config)
    data = PostgresData(database, settings)

    def check_ready():
        try:
            return database.ready()
        except AppError:
            raise
        except Exception:
            raise AppError(503, "DATA_UNAVAILABLE", "数据存储暂不可用") from None

    def close_resources():
        database.close()

    return DataBinding(data=data, ready=check_ready, close=close_resources)


def create_app(settings: Settings | None = None):
    """Compose B's application with the persistent PostgreSQL data binding."""
    try:
        binding = compose_data_binding(ConnectionConfig.from_env(), DataSettings.from_env())
    except AppError:
        raise
    except Exception:
        LOGGER.critical("data_binding_initialization_failed")
        raise RuntimeError("Data binding initialization failed; startup refused") from None
    try:
        return create_business_app(settings or Settings.from_env(), data_binding=binding)
    except BaseException:
        try:
            if binding.close is not None:
                binding.close()
        except Exception:
            LOGGER.warning("data_provider_cleanup_failed")
        raise
