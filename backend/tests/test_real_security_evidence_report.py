"""Exporter accounting only; synthetic XML here is never published as real evidence."""

import ast
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


@pytest.fixture
def exporter():
    path = Path(__file__).resolve().parents[2] / "scripts/export_real_security_evidence.py"
    spec = importlib.util.spec_from_file_location("real_evidence_exporter", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_report(module, tmp_path, *, missing=None, source="current_checkout", outcome=None):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    properties = ET.SubElement(suite, "properties")
    for name, value in {
        "security_source": source,
        "security_postgres_a_commit": "a" * 40,
        "security_postgres_server_version": "160015",
        "security_postgres_image": "pgvector/pgvector@sha256:" + "b" * 64,
    }.items():
        ET.SubElement(properties, "property", name=name, value=value)
    for _, files in module.GROUPS.values():
        for filename in files:
            definitions = ast.parse((module.ROOT / "backend/tests" / filename).read_text(encoding="utf-8"))
            for function in definitions.body:
                if not isinstance(
                    function, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) or not function.name.startswith("test_"):
                    continue
                count = module.MINIMUMS.get(function.name, 1) - (function.name == missing)
                for i in range(count):
                    case = ET.SubElement(
                        suite,
                        "testcase",
                        classname="backend.tests." + Path(filename).stem,
                        name=f"{function.name}[{i}]",
                    )
                    if outcome and function.name == "test_normal_ast_variants_execute_under_restricted_login":
                        ET.SubElement(case, outcome, message="PRIVATE_SQL_OR_CREDENTIAL")
    path = tmp_path / "synthetic-accounting.xml"
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def test_complete_accounting_requires_verified_current_source(exporter, tmp_path):
    groups, metadata = exporter.summarize(fixture_report(exporter, tmp_path), "a" * 40)
    assert metadata["verified"] and all(g["status"] == "passed" for g in groups)
    assert groups[0]["passed"] >= 109


@pytest.mark.parametrize(
    "missing",
    [
        "test_attack_is_rejected_in_real_configured_chain",
        "test_normal_ast_variants_execute_under_restricted_login",
    ],
)
def test_partial_parameterized_suite_cannot_pass(exporter, tmp_path, missing):
    groups, _ = exporter.summarize(fixture_report(exporter, tmp_path, missing=missing), "a" * 40)
    assert groups[0]["status"] == "not_verified"


def test_pinned_reference_cannot_masquerade_as_merged_source(exporter, tmp_path):
    groups, metadata = exporter.summarize(
        fixture_report(exporter, tmp_path, source="pinned_reference"), "a" * 40
    )
    assert not metadata["verified"] and all(g["status"] == "not_verified" for g in groups)


def test_failure_outside_selected_groups_blocks_passed_badges(exporter, tmp_path):
    path = fixture_report(exporter, tmp_path)
    tree = ET.parse(path)
    case = ET.SubElement(
        tree.getroot().find("testsuite"), "testcase", classname="other.module", name="other_test"
    )
    ET.SubElement(case, "failure", message="PRIVATE_DETAIL")
    tree.write(path, encoding="utf-8")
    groups, _ = exporter.summarize(path, "a" * 40)
    assert all(g["status"] == "not_verified" for g in groups)


@pytest.mark.parametrize("outcome,status", [("failure", "failed"), ("skipped", "not_verified")])
def test_failures_and_skips_are_visible_without_private_details(exporter, tmp_path, outcome, status):
    groups, _ = exporter.summarize(fixture_report(exporter, tmp_path, outcome=outcome), "a" * 40)
    assert groups[0]["status"] == status
    assert "PRIVATE" not in str(groups)
