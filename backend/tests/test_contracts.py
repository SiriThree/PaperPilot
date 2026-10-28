import json
import inspect
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from app.config import Settings
from app.main import create_app
from app.schemas import SecureQueryResult
from app.adapters.ports import DataPort
from signature_contracts import assert_signature_matches

ROOT = Path(__file__).resolve().parents[2]


def test_data_port_examples_cover_current_signatures():
    document = json.loads((ROOT / "contracts/data-port-examples.json").read_text(encoding="utf-8"))
    assert document["contract"] == "AB-1" and document["mock"] is True
    methods = {name for name in DataPort.__dict__ if not name.startswith("_")}
    assert set(document["methods"]) == methods
    for name, example in document["methods"].items():
        signature = inspect.signature(getattr(DataPort, name))
        assert_signature_matches(example["signature"], signature)
        assert "input" in example["success"] and "output" in example["success"]
        assert "missing_or_error" in example
        signature.bind(None, **example["success"]["input"])
        signature.bind(None, **example["missing_or_error"]["input"])


def test_openapi_matches_export(app):
    assert app.openapi() == json.loads((ROOT / "contracts/openapi.json").read_text(encoding="utf-8"))


def test_all_operations_have_valid_examples(app):
    spec = app.openapi()
    registry = Registry().with_resource(
        "urn:paperpilot",
        Resource.from_contents({"$schema": "https://json-schema.org/draft/2020-12/schema", **spec}),
    )
    covered = set()
    for file in (ROOT / "contracts/examples").glob("*.json"):
        example = json.loads(file.read_text(encoding="utf-8"))
        path, method = example["path_template"], example["method"]
        operation = spec["paths"][path][method]
        for category in ("success", "error"):
            response = example[category]["response"]
            schema = operation["responses"][str(response["status"])]["content"][response["content_type"]][
                "schema"
            ]
            wrapped = {"$ref": "urn:paperpilot" + schema["$ref"]} if "$ref" in schema else schema
            Draft202012Validator(wrapped, registry=registry, format_checker=FormatChecker()).validate(
                response["body"]
            )
        covered.add((path, method))
    expected = {(p, m) for p, ops in spec["paths"].items() for m in ops if m in {"get", "post", "delete"}}
    assert covered == expected


@pytest.mark.parametrize(
    "settings",
    [
        Settings(a_mode="invalid"),
        Settings(environment="invalid"),
        Settings(a_mode="real"),
        Settings(c_mode="real"),
        Settings(d_mode="real"),
        Settings(environment="production"),
        Settings(environment="production", a_mode="real", c_mode="real", d_mode="real"),
    ],
)
def test_unsafe_or_unimplemented_modes_refuse_start(settings):
    with pytest.raises(RuntimeError):
        create_app(settings)


def test_allowed_does_not_mean_executed():
    from uuid import uuid4

    result = SecureQueryResult(
        decision="allowed",
        execution_status="not_executed",
        reason={"code": "PENDING", "message": "Awaiting execution"},
        policy_id="test",
        request_id=uuid4(),
    )
    assert result.execution_status != "executed"


@pytest.mark.parametrize(
    "decision,status,rows",
    [("rejected", "executed", []), ("allowed", "not_executed", [{"x": 1}]), ("rejected", "failed", [])],
)
def test_gateway_invalid_state_rejected(decision, status, rows):
    from uuid import uuid4
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SecureQueryResult(
            decision=decision,
            execution_status=status,
            rows=rows,
            policy_id="test",
            request_id=uuid4(),
            reason={"code": "TEST", "message": "test"},
        )
