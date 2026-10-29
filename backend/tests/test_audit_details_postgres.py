"""Real append-only storage and replay compatibility for optional operation details."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.adapters.data_rules import fingerprint
from app.data.binding import compose_data_binding
from app.data.settings import DataSettings
from app.schemas import AuditDetails, AuditEvent, ProjectCreate
from app.adapters.ports import Context
from app.errors import AppError
from test_phase5a_persistent import database as isolated_database  # noqa: F401

pytestmark = pytest.mark.real_database


def test_details_roundtrip_replay_conflict_and_legacy_fingerprint(isolated_database):  # noqa: F811
    config, _ = isolated_database
    binding = compose_data_binding(config, DataSettings())
    try:
        data = binding.data
        user = data.user_by_name("alice")
        project = data.create_project(user.user_id, ProjectCreate(name="detail persistence"), uuid4())
        ctx = Context(user.user_id, project.project_id, uuid4())
        event = AuditEvent(
            event_id=uuid4(),
            timestamp=datetime.now(UTC),
            request_id=ctx.request_id,
            actor_id=user.user_id,
            project_id=project.project_id,
            action="secure_query.finished",
            resource_type="secure_query",
            decision="allowed",
            execution_status="executed",
            duration_ms=1,
            mock=False,
            details=AuditDetails(question="按年份统计论文数量"),
        )
        data.append_audit(event)
        data.append_audit(event)
        with pytest.raises(AppError) as failure:
            data.append_audit(event.model_copy(update={"details": AuditDetails(question="changed")}))
        assert failure.value.code == "AUDIT_EVENT_CONFLICT"
        legacy = event.model_copy(update={"event_id": uuid4(), "details": None})
        with data.database.transaction(write=True) as c:
            c.execute(
                "INSERT INTO audit_events(event_id,timestamp,request_id,actor_id,project_id,action,resource_type,decision,execution_status,duration_ms,mock,event_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    legacy.event_id,
                    legacy.timestamp,
                    legacy.request_id,
                    legacy.actor_id,
                    legacy.project_id,
                    legacy.action,
                    legacy.resource_type,
                    legacy.decision,
                    legacy.execution_status,
                    legacy.duration_ms,
                    legacy.mock,
                    fingerprint(legacy.model_dump(mode="json", exclude={"details"})),
                ),
            )
        data.append_audit(legacy)
        rows = data.audit(
            ctx, action=event.action, decision=None, actor_id=None, since=None, until=None, offset=0, limit=20
        )
        assert rows.total == 2
        assert next(r for r in rows.items if r.event_id == event.event_id).details == event.details
        assert next(r for r in rows.items if r.event_id == legacy.event_id).details is None
    finally:
        binding.close()
