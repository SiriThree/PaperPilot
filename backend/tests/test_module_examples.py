import inspect
import json
import re
import runpy
from pathlib import Path

import pytest

from app.adapters.ports import AIPort, SecurityPort
from app.schemas import (
    AnswerResult,
    AuditEvent,
    AuditList,
    DraftResult,
    EvidenceResult,
    ProcessResult,
    SearchResult,
    SecureQueryResult,
)
from signature_contracts import assert_signature_matches

ROOT = Path(__file__).resolve().parents[2]


def test_module_examples_match_executed_ports_and_frozen_signatures():
    document = json.loads((ROOT / "contracts/module-port-examples.json").read_text(encoding="utf-8"))
    build = runpy.run_path(str(ROOT / "scripts/export_module_examples.py"))["build_examples"]
    generated = build()
    assert document["mock"] is True
    for port in (AIPort, SecurityPort):
        methods = {name for name in vars(port) if not name.startswith("_")}
        assert set(document["methods"][port.__name__]) == methods
        for name in methods:
            example = document["methods"][port.__name__][name]
            signature = inspect.signature(getattr(port, name))
            assert_signature_matches(example["signature"], signature)
            generated_example = generated["methods"][port.__name__][name]
            assert_signature_matches(generated_example["signature"], signature)
            generated_example["signature"] = example["signature"]
            signature.bind(None, **example["input"])
    assert document == generated
    models = {
        "process": ProcessResult,
        "search": SearchResult,
        "answer": AnswerResult,
        "extract": EvidenceResult,
        "draft": DraftResult,
    }
    for name, model in models.items():
        model.model_validate(document["methods"]["AIPort"][name]["output"])
    SecureQueryResult.model_validate(document["methods"]["SecurityPort"]["execute"]["output"])
    assert document["methods"]["SecurityPort"]["authorize"]["output"] is None
    AuditEvent.model_validate(document["audit"]["append_audit"]["event"])
    AuditList.model_validate(document["audit"]["read_back"])


@pytest.mark.parametrize("member", ["a", "c", "d"])
def test_handoff_json_examples_match_executed_contract_examples(member):
    """Keep copyable handoff payloads tied to the actual executed fixtures."""
    data = json.loads((ROOT / "contracts/data-port-examples.json").read_text(encoding="utf-8"))
    modules = json.loads((ROOT / "contracts/module-port-examples.json").read_text(encoding="utf-8"))
    document = (ROOT / f"docs/member-{member}-handoff.md").read_text(encoding="utf-8")
    actual = [json.loads(value) for value in re.findall(r"```json\s*\n(.*?)\n```", document, re.DOTALL)]
    if member == "a":
        method = data["methods"]["create_project"]
        expected = [
            method["success"]["input"],
            method["success"]["output"],
            method["missing_or_error"]["error"],
        ]
    elif member == "c":
        method = modules["methods"]["AIPort"]["answer"]
        expected = [method["input"], method["output"], modules["scenarios"]["ai_unavailable"]["error"]]
    else:
        method = modules["methods"]["SecurityPort"]["execute"]
        expected = [
            method["input"],
            method["output"],
            modules["scenarios"]["rejected"]["output"],
            modules["scenarios"]["gateway_unavailable"]["error"],
            modules["audit"]["append_audit"]["event"],
        ]
    assert actual == expected
