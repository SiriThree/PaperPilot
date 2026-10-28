import importlib.util
from pathlib import Path


def load_exporter():
    spec = importlib.util.spec_from_file_location(
        "security_evidence_exporter",
        Path(__file__).resolve().parents[2] / "scripts/export_security_evidence.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_tests_cannot_be_marked_passed(tmp_path):
    module = load_exporter()
    path = tmp_path / "empty.xml"
    path.write_text("<testsuites><testsuite /></testsuites>")
    assert all(group["status"] == "not_verified" for group in module.summarize(path))


def test_skipped_and_failed_checks_remain_visible(tmp_path):
    module = load_exporter()
    path = tmp_path / "results.xml"
    path.write_text(
        '<testsuites><testsuite><testcase name="test_unauthenticated_and_request_id">'
        '<skipped /></testcase><testcase name="test_gateway_down_has_no_fallback">'
        '<failure message="PRIVATE_DETAIL" /></testcase></testsuite></testsuites>'
    )
    groups = {group["id"]: group for group in module.summarize(path)}
    assert groups["anonymous"]["status"] == "not_verified" and groups["anonymous"]["skipped"] == 1
    assert groups["gateway_boundary"]["status"] == "failed" and groups["gateway_boundary"]["failed"] == 1
    assert "PRIVATE_DETAIL" not in str(groups)


def test_report_counts_parameterized_checks_without_losing_group_coverage(tmp_path):
    module = load_exporter()
    path = tmp_path / "results.xml"
    path.write_text(
        '<testsuites><testsuite><testcase name="test_unauthenticated_and_request_id" />'
        '<testcase name="test_project_id_substitution_denied[papers]" />'
        '<testcase name="test_project_id_substitution_denied[reports]" />'
        "</testsuite></testsuites>"
    )
    groups = {group["id"]: group for group in module.summarize(path)}
    assert groups["anonymous"]["status"] == "passed" and groups["anonymous"]["passed"] == 1
    assert groups["project_scope"]["passed"] == 2 and groups["project_scope"]["status"] == "not_verified"
