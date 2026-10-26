import logging
from collections.abc import Callable

from app.adapters.mock_ai import MockAI
from app.adapters.mock_data import MockData
from app.adapters.mock_security import MockSecurity
from app.adapters.data_binding import DataBinding
from app.adapters.ports import Adapters, AIPort, DataPort, SecurityPort
from app.config import Settings


AIFactory = Callable[[DataPort], AIPort]
SecurityFactory = Callable[[DataPort], SecurityPort]


def _module(mode, factory, data, mock_type, port, name):
    if mode == "mock":
        if factory is not None:
            raise RuntimeError(f"Explicit {name} Mock mode cannot accept a real factory")
        return mock_type(data)
    if factory is None:
        raise RuntimeError(f"{name} adapter is not registered; startup refused; no Mock fallback")
    try:
        value = factory(data)
        if isinstance(value, mock_type) or any(
            not callable(getattr(value, method, None)) for method in vars(port) if not method.startswith("_")
        ):
            raise TypeError("Incomplete adapter")
        return value
    except Exception:
        raise RuntimeError(f"{name} adapter initialization failed; no Mock fallback") from None


def create_adapters(
    settings: Settings,
    *,
    data_binding: DataBinding | None = None,
    ai_factory: AIFactory | None = None,
    security_factory: SecurityFactory | None = None,
) -> Adapters:
    settings.validate()
    if settings.a_mode == "mock":
        if data_binding is not None:
            raise RuntimeError("Explicit Mock mode cannot accept a persistent data binding")
        data = MockData()
    else:
        if data_binding is None:
            raise RuntimeError("Persistent DataPort is not registered; startup refused; no Mock fallback")
        methods = (name for name in vars(DataPort) if not name.startswith("_"))
        if (
            isinstance(data_binding.data, MockData)
            or any(not callable(getattr(data_binding.data, name, None)) for name in methods)
            or not callable(data_binding.ready)
        ):
            raise RuntimeError("Invalid persistent DataPort binding; startup refused")
        try:
            if data_binding.ready() is False:
                raise RuntimeError("Provider not ready")
        except Exception:
            raise RuntimeError("Persistent DataPort readiness failed; no Mock fallback") from None
        data = data_binding.data
    ai = _module(settings.c_mode, ai_factory, data, MockAI, AIPort, "C")
    security = _module(settings.d_mode, security_factory, data, MockSecurity, SecurityPort, "D")
    logging.getLogger("paperpilot").info(
        "adapter_modes a=%s c=%s d=%s environment=%s",
        settings.a_mode,
        settings.c_mode,
        settings.d_mode,
        settings.environment,
    )
    return Adapters(data=data, ai=ai, security=security)
