"""Run isolated Mock application checks and publish a minimal, traceable UI report."""

import hashlib
import json
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "frontend/public/security-evidence.json"
GROUPS = {
    "anonymous": (
        "匿名资源访问",
        [
            "backend/tests/test_api.py::test_unauthenticated_and_request_id",
        ],
    ),
    "project_scope": (
        "跨项目与成员授权",
        [
            "backend/tests/test_api.py::test_project_id_substitution_denied",
            "backend/tests/test_api.py::test_resource_id_substitution_denied",
            "backend/tests/test_api.py::test_report_cross_project_id_denied",
            "backend/tests/test_api.py::test_member_revocation_immediate",
            "backend/tests/test_task_recovery.py::test_task_recovery_denies_anonymous_outsider_and_cross_project",
        ],
    ),
    "gateway_boundary": (
        "网关故障与审计边界",
        [
            "backend/tests/test_api.py::test_gateway_down_has_no_fallback",
            "backend/tests/test_api.py::test_critical_audit_failure_prevents_gateway",
            "backend/tests/test_business_integration.py::test_registered_gateway_failure_never_uses_fallback_or_returns_unconfirmed_rows",
        ],
    ),
}


def summarize(xml_path):
    cases = list(ET.parse(xml_path).iter("testcase"))
    groups = []
    for key, (label, selectors) in GROUPS.items():
        names = [selector.split("::")[-1] for selector in selectors]
        selected = [case for case in cases if case.get("name", "").split("[")[0] in names]
        counts = {"passed": 0, "failed": 0, "skipped": 0}
        for case in selected:
            status = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else ("skipped" if case.find("skipped") is not None else "passed")
            )
            counts[status] += 1
        complete = all(any(case.get("name", "").split("[")[0] == name for case in selected) for name in names)
        status = (
            "failed"
            if counts["failed"]
            else ("passed" if complete and not counts["skipped"] else "not_verified")
        )
        groups.append({"id": key, "label": label, "status": status, **counts, "selectors": selectors})
    return groups


def main():
    selectors = [selector for _, values in GROUPS.values() for selector in values]
    with tempfile.TemporaryDirectory(prefix="paperpilot-security-report-") as directory:
        xml_path = Path(directory) / "results.xml"
        command = [sys.executable, "-m", "pytest", *selectors, "-q", f"--junitxml={xml_path}"]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
        if not xml_path.exists():
            raise SystemExit("No test report produced; previous evidence was not overwritten")
        groups = summarize(xml_path)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    files = [ROOT / "backend/app/auth.py", ROOT / "backend/app/services.py"]
    files.extend(sorted((ROOT / "backend/app/routes").glob("*.py")))
    files.extend(ROOT / selector.split("::")[0] for selector in selectors)
    fingerprint = hashlib.sha256()
    for path in sorted(set(files)):
        fingerprint.update(path.relative_to(ROOT).as_posix().encode())
        fingerprint.update(path.read_bytes())
    report = {
        "schema_version": 1,
        "scope": "mock_application",
        "environment": "isolated Mock / pytest",
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "worktree_dirty": dirty,
        "code_fingerprint": fingerprint.hexdigest(),
        "groups": groups,
        "not_verified": ["真实 SQL AST / 受限执行：待 D 验收", "真实数据库隔离 / 持久化恢复：待 A 验收"],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "Isolated Mock security evidence:",
        json.dumps(
            [
                {
                    "id": group["id"],
                    "status": group["status"],
                    "passed": group["passed"],
                    "failed": group["failed"],
                    "skipped": group["skipped"],
                }
                for group in groups
            ]
        ),
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
