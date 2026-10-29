"""uvicorn app.ai.bootstrap:create_app --factory --app-dir backend"""

from app.ai.adapter import RealAI
from app.ai.files import LocalPDFReader
from app.ai.settings import AISettings
from app.config import Settings
from app.main import create_app as create_business_app


def create_app(
    settings=None, *, ai_settings=None, data_binding=None, security_factory=None, auth_sessions=None
):
    settings = settings or Settings.from_env()
    if settings.c_mode != "real":
        raise RuntimeError("C composition requires explicit PAPERPILOT_C_MODE=real")
    configuration = ai_settings or AISettings.from_env()
    return create_business_app(
        settings,
        data_binding=data_binding,
        security_factory=security_factory,
        auth_sessions=auth_sessions,
        ai_factory=lambda data: RealAI(data, configuration, reader=LocalPDFReader(settings.storage_dir)),
    )
