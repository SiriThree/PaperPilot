from importlib import import_module

import pytest

from .harness import SecurityHarness, build_mock_harness


@pytest.fixture
def security_harness(request, tmp_path):
    provider = request.config.getoption("--security-contract-provider")
    if provider is None:
        yield build_mock_harness()
        return
    module, separator, name = provider.partition(":")
    if not separator:
        pytest.fail("--security-contract-provider must be module:factory")
    with getattr(import_module(module), name)(tmp_path=tmp_path) as harness:
        assert isinstance(harness, SecurityHarness) and not harness.is_mock
        yield harness
