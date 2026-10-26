from importlib import import_module

import pytest

from .harness import AIHarness, build_mock_harness


@pytest.fixture
def ai_harness(request, tmp_path):
    provider = request.config.getoption("--ai-contract-provider")
    if provider is None:
        yield build_mock_harness()
        return
    module, separator, name = provider.partition(":")
    if not separator:
        pytest.fail("--ai-contract-provider must be module:factory")
    with getattr(import_module(module), name)(tmp_path=tmp_path) as harness:
        assert isinstance(harness, AIHarness) and not harness.is_mock
        yield harness
