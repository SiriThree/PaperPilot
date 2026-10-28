import pytest

from app.data.settings import DEFAULT_LEASE_SECONDS, DataSettings


def test_demo_defaults_and_lease_override(monkeypatch):
    monkeypatch.delenv("PAPERPILOT_LEASE_SECONDS", raising=False)
    assert DataSettings.from_env().lease_seconds == DEFAULT_LEASE_SECONDS == 120
    assert DataSettings.from_env().idempotency_retention_seconds is None
    monkeypatch.setenv("PAPERPILOT_LEASE_SECONDS", "300")
    assert DataSettings.from_env().lease_seconds == 300


@pytest.mark.parametrize("value", ["0", "-1", "3601", "abc", "1.5", ""])
def test_invalid_lease_fails_explicitly(monkeypatch, value):
    monkeypatch.setenv("PAPERPILOT_LEASE_SECONDS", value)
    with pytest.raises(ValueError, match="PAPERPILOT_LEASE_SECONDS"):
        DataSettings.from_env()


def test_demo_rejects_automatic_expiry():
    with pytest.raises(ValueError, match="must not expire"):
        DataSettings(idempotency_retention_seconds=86400)
