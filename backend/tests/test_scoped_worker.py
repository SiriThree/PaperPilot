"""Worker audit forwarding validates the server-configured project and actor."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.data.database import ConnectionConfig
from app.data.scoped_business import ScopedBusinessConfig, ScopedResultWorker
from app.data.scoped_worker import ScopedWorkerData
from app.errors import AppError
from app.schemas import AuditEvent


def test_worker_audit_scope_is_checked_before_sink():
    user, project = uuid4(), uuid4()
    config = ScopedBusinessConfig(
        ConnectionConfig("127.0.0.1", 5432, "fixture", "fixture-secret", "fixture"),
        user,
        project,
        "function_owner",
        "binding_owner",
    )
    worker = ScopedResultWorker(config)
    recorded = []
    data = ScopedWorkerData(worker, recorded.append)
    event = AuditEvent(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        request_id=uuid4(),
        actor_id=user,
        project_id=project,
        action="process.completed",
        resource_type="process",
        decision="allowed",
        execution_status="completed",
        duration_ms=0,
    )
    try:
        data.append_audit(event)
        assert recorded == [event]
        for changed in ({"actor_id": uuid4()}, {"project_id": uuid4()}, {"project_id": None}):
            with pytest.raises(AppError):
                data.append_audit(event.model_copy(update=changed))
        assert recorded == [event]
    finally:
        worker.close()
