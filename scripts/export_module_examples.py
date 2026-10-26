"""Execute deterministic Mock C/D examples. No network, database or model required."""

import argparse
import asyncio
import inspect
import json
import sys
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "backend", ROOT / "backend/tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ai_contracts.harness import build_mock_harness as build_ai  # noqa: E402
from security_contracts.harness import build_mock_harness as build_security  # noqa: E402
from app.adapters.data_rules import canonical  # noqa: E402
from app.adapters.ports import AIPort, SecurityPort  # noqa: E402
from app.errors import AppError  # noqa: E402
from app.schemas import AuditEvent, QuestionRequest  # noqa: E402

OUTPUT = ROOT / "contracts/module-port-examples.json"


def build_examples():
    c, d = build_ai(), build_security()
    sequence = count(1)
    examples = {
        "mock": True,
        "contract": "existing AIPort and SecurityPort",
        "methods": {"AIPort": {}, "SecurityPort": {}},
        "scenarios": {},
    }

    def record(port, name, inputs, call):
        output = call()
        if inspect.isawaitable(output):
            output = asyncio.run(output)
        examples["methods"][port.__name__][name] = {
            "signature": str(inspect.signature(getattr(port, name))),
            "input": inputs,
            "output": output,
        }

    def error(call):
        try:
            output = call()
            if inspect.isawaitable(output):
                asyncio.run(output)
        except AppError as exc:
            return {"status": exc.status, "code": exc.code, "message": exc.message}
        raise AssertionError("Expected a documented Mock failure")

    question = QuestionRequest(question=c.query.query)
    with patch("app.adapters.mock_ai.uuid4", side_effect=lambda: UUID(int=next(sequence))):
        record(
            AIPort,
            "process",
            {"ctx": c.ctx, "task_id": c.task_id, "file": c.file},
            lambda: c.ai.process(c.ctx, c.task_id, c.file),
        )
        record(AIPort, "search", {"ctx": c.ctx, "query": c.query}, lambda: c.ai.search(c.ctx, c.query))
        record(
            AIPort,
            "answer",
            {"ctx": c.ctx, "question": question, "sources": [c.source]},
            lambda: c.ai.answer(c.ctx, question, [c.source]),
        )
        record(
            AIPort,
            "extract",
            {"ctx": c.ctx, "paper_id": c.source.paper_id, "sources": [c.source]},
            lambda: c.ai.extract(c.ctx, c.source.paper_id, [c.source]),
        )
        record(
            AIPort,
            "draft",
            {"ctx": c.ctx, "question": question, "sources": [c.source]},
            lambda: c.ai.draft(c.ctx, question, [c.source]),
        )
    record(
        SecurityPort,
        "authorize",
        {"ctx": d.ctx, "action": "audit.read", "role": "owner"},
        lambda: d.security.authorize(d.ctx, "audit.read", "owner"),
    )
    record(
        SecurityPort,
        "execute",
        {"ctx": d.ctx, "question": d.normal_question},
        lambda: d.security.execute(d.ctx, d.normal_question),
    )
    examples["scenarios"]["ai_unavailable"] = {
        "input": {
            "ctx": c.ctx,
            "question": QuestionRequest(question=c.failure_question),
            "sources": [c.source],
        },
        "error": error(
            lambda: c.failing_ai.answer(c.ctx, QuestionRequest(question=c.failure_question), [c.source])
        ),
    }
    examples["scenarios"]["insufficient_evidence"] = {
        "input": {"ctx": c.ctx, "question": question, "sources": []},
        "output": asyncio.run(c.ai.answer(c.ctx, question, [])),
    }
    examples["scenarios"]["owner_required"] = {
        "input": {"ctx": d.member_ctx, "action": "audit.read", "role": "member"},
        "error": error(lambda: d.security.authorize(d.member_ctx, "audit.read", "member")),
    }
    for name, port, prompt in (
        ("rejected", d.security, d.rejected_question),
        ("failed", d.failing_security, d.failure_question),
    ):
        examples["scenarios"][name] = {
            "input": {"ctx": d.ctx, "question": prompt},
            "output": asyncio.run(port.execute(d.ctx, prompt)),
        }
    examples["scenarios"]["gateway_unavailable"] = {
        "input": {"ctx": d.ctx, "question": d.unavailable_question},
        "error": error(lambda: d.unavailable_security.execute(d.ctx, d.unavailable_question)),
    }
    event = AuditEvent(
        event_id=UUID(int=10),
        timestamp=datetime(2026, 10, 26, tzinfo=UTC),
        request_id=d.ctx.request_id,
        actor_id=d.ctx.user_id,
        project_id=d.ctx.project_id,
        action="contract.gateway",
        resource_type="secure_query",
        decision="allowed",
        policy_id="mock-intents-v1",
        execution_status="executed",
        duration_ms=0,
        mock=True,
    )
    d.data.append_audit(event)
    audit = d.data.audit(
        d.ctx,
        action="contract.gateway",
        decision=None,
        actor_id=None,
        since=None,
        until=None,
        offset=0,
        limit=20,
    )
    examples["audit"] = {"append_audit": {"event": event}, "read_back": audit}
    return json.loads(canonical(examples))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    actual = build_examples()
    if options.check:
        if json.loads(OUTPUT.read_text(encoding="utf-8")) != actual:
            raise SystemExit("C/D examples differ; regenerate with this script")
        print("C/D examples match executed Mock ports")
    else:
        OUTPUT.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("Exported 5 C methods, 2 D methods and failure/audit examples (Mock only)")


if __name__ == "__main__":
    main()
