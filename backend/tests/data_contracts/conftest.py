from importlib import import_module
from uuid import uuid4

import pytest

from app.adapters.ports import Context
from app.schemas import ProjectCreate
from .harness import DataHarness, build_mock_harness


@pytest.fixture
def contract_harness(request, tmp_path):
    provider = request.config.getoption("--data-contract-provider")
    if provider is None:
        yield build_mock_harness()
        return
    module_name, separator, name = provider.partition(":")
    if not separator:
        pytest.fail("--data-contract-provider must be module:factory")
    # Provider owns disposable database setup/cleanup; never infer a URL from application settings.
    factory = getattr(import_module(module_name), name)
    with factory(tmp_path=tmp_path) as harness:
        assert isinstance(harness, DataHarness) and harness.kind == "database"
        yield harness


@pytest.fixture
def data(contract_harness):
    return contract_harness.data


@pytest.fixture
def ctx(contract_harness):
    project = contract_harness.data.create_project(
        contract_harness.owner.user_id, ProjectCreate(name="数据契约测试"), uuid4()
    )
    return Context(contract_harness.owner.user_id, project.project_id, uuid4())


@pytest.fixture
def other_ctx(contract_harness):
    project = contract_harness.data.create_project(
        contract_harness.owner.user_id, ProjectCreate(name="同一用户的另一项目"), uuid4()
    )
    return Context(contract_harness.owner.user_id, project.project_id, uuid4())


@pytest.fixture
def real_harness(contract_harness):
    if contract_harness.kind != "database":
        pytest.skip("当前为 Mock；请显式选择隔离数据库 provider 验证持久化、数据库并发和租约恢复")
    return contract_harness
