"""Registration doubles prove composition, not AI quality or SQL protection."""

import pytest
from fastapi.testclient import TestClient

from app.adapters.factory import create_adapters
from app.adapters.mock_ai import MockAI
from app.adapters.mock_security import MockSecurity
from app.config import Settings
from app.main import create_app


class PortDouble:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)


@pytest.mark.parametrize("member", ["c", "d"])
def test_unregistered_real_module_never_falls_back(member):
    with pytest.raises(RuntimeError, match="not registered"):
        create_adapters(Settings(**{f"{member}_mode": "real"}))


@pytest.mark.parametrize(
    "member,mock_type,key", [("c", MockAI, "ai_factory"), ("d", MockSecurity, "security_factory")]
)
def test_mock_mode_rejects_unused_factory_and_real_rejects_mock(member, mock_type, key):
    with pytest.raises(RuntimeError, match="Explicit"):
        create_adapters(Settings(), **{key: mock_type})
    with pytest.raises(RuntimeError, match="initialization failed"):
        create_adapters(Settings(**{f"{member}_mode": "real"}), **{key: mock_type})


@pytest.mark.parametrize("member,key", [("c", "ai_factory"), ("d", "security_factory")])
@pytest.mark.parametrize("failure", ["exception", "incomplete"])
def test_module_initialization_errors_are_safe(member, key, failure):
    def broken(data):
        if failure == "exception":
            raise RuntimeError("PRIVATE_SDK_CONFIG_DETAIL")
        return object()

    with pytest.raises(RuntimeError) as error:
        create_adapters(Settings(**{f"{member}_mode": "real"}), **{key: broken})
    assert "PRIVATE_SDK_CONFIG_DETAIL" not in str(error.value)


def test_registered_modules_receive_the_same_data_port_and_reach_business_api():
    received = []

    def ai(data):
        received.append(data)
        return PortDouble(MockAI(data))

    def security(data):
        received.append(data)
        return PortDouble(MockSecurity(data))

    app = create_app(
        Settings(environment="test", c_mode="real", d_mode="real"), ai_factory=ai, security_factory=security
    )
    assert received == [app.state.adapters.data, app.state.adapters.data]
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        health = client.get("/api/v1/health").json()
        assert "registered_ai_provider_unverified" in health["limitations"]
        assert "registered_security_provider_unverified" in health["limitations"]
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        project = client.get("/api/v1/projects").json()["items"][0]["project_id"]
        assert client.post(f"/api/v1/projects/{project}/search", json={"query": "test"}).status_code == 200
        assert (
            client.post(
                f"/api/v1/projects/{project}/secure-queries", json={"question": "不支持的问题"}
            ).json()["decision"]
            == "rejected"
        )


def test_registering_modules_does_not_enable_production_development_identity():
    with pytest.raises(RuntimeError, match="Production authentication"):
        create_app(Settings(environment="production", a_mode="real", c_mode="real", d_mode="real"))
