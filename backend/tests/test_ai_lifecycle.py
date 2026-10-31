from fastapi.testclient import TestClient
import pytest

from app.adapters.mock_ai import MockAI
from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize("fail_start", [False, True])
def test_optional_ai_lifecycle_closes_even_after_startup_failure(tmp_path, fail_start):
    calls = []

    class Adapter:
        def __init__(self, data):
            self.delegate = MockAI(data)

        def __getattr__(self, name):
            return getattr(self.delegate, name)

        async def astart(self):
            calls.append("start")
            if fail_start:
                raise RuntimeError("isolated startup failure")

        async def aclose(self):
            calls.append("close")

    app = create_app(Settings(environment="test", storage_dir=tmp_path, c_mode="real"), ai_factory=Adapter)
    if fail_start:
        with pytest.raises(RuntimeError, match="isolated startup failure"):
            with TestClient(app):
                pass
    else:
        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
    assert calls == ["start", "close"]
