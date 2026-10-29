"""Run current-checkout security checks on disposable PostgreSQL and publish counts, never raw SQL/errors."""

import ast
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "frontend/public/security-evidence-postgres.json"
GROUPS = {
    "sql_execution": ("SQL 允许清单、受限执行及资源清理", ["test_security_executor_postgres.py"]),
    "database_scope": ("PostgreSQL 项目隔离与统计契约", ["test_security_postgres_contract.py"]),
    "audit_identity": (
        "关键审计、身份生命周期及业务降权",
        ["test_security_identities_postgres.py", "test_business_login.py"],
    ),
    "recovery_integration": (
        "持久化索引、任务恢复及 A/C/D 联调",
        [
            "test_persistent_index.py",
            "test_runtime_recovery.py",
            "test_security_team_integration.py",
            "test_security_bootstrap_postgres.py",
        ],
    ),
}
MINIMUMS = {
    "test_attack_is_rejected_in_real_configured_chain": 44,
    "test_calling_executor_directly_cannot_bypass_plan_validation": 44,
    "test_normal_ast_variants_execute_under_restricted_login": 21,
}


def code_fingerprint():
    paths = [
        p
        for folder in ("backend/app", "backend/tests")
        for p in (ROOT / folder).rglob("*")
        if p.suffix in {".py", ".sql"}
    ]
    paths.extend(
        [
            Path(__file__),
            ROOT / "scripts/verify_integration.py",
            ROOT / "scripts/reindex_paper.py",
            ROOT / "pyproject.toml",
            ROOT / "frontend/src/components/SecurityEvidencePanel.vue",
            ROOT / "frontend/src/pages/SecurityPage.vue",
        ]
    )
    paths.extend(ROOT.glob("requirements*.lock"))
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def source_metadata(tree, commit):
    def values(name):
        return {p.get("value", "") for p in tree.iter("property") if p.get("name") == name}

    versions = values("security_postgres_server_version")
    images = values("security_postgres_image")
    valid = (
        values("security_source") == {"current_checkout"}
        and values("security_postgres_a_commit") == {commit}
        and bool(versions)
        and all(v.isdecimal() and 160000 <= int(v) < 170000 for v in versions)
        and len(images) == 1
        and re.fullmatch(r"pgvector/pgvector@sha256:[a-f0-9]{64}", next(iter(images))) is not None
    )
    return {
        "verified": valid,
        "checkout": "current_checkout" if valid else "unverified",
        "postgres_versions": sorted(versions) if valid else [],
        "image": next(iter(images)) if valid else "",
    }


def summarize(path, commit):
    tree = ET.parse(path)
    metadata = source_metadata(tree, commit)
    cases = list(tree.iter("testcase"))
    run_failed = any(c.find("failure") is not None or c.find("error") is not None for c in cases)
    groups = []
    for key, (label, files) in GROUPS.items():
        selected, complete = [], True
        for filename in files:
            module = "backend.tests." + Path(filename).stem
            subset = [c for c in cases if c.get("classname") == module]
            selected.extend(subset)
            names = Counter(c.get("name", "").split("[")[0] for c in subset)
            definitions = ast.parse((ROOT / "backend/tests" / filename).read_text(encoding="utf-8"))
            expected = [
                node.name
                for node in definitions.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
            ]
            complete &= bool(expected) and all(names[n] >= MINIMUMS.get(n, 1) for n in expected)
        counts = {"passed": 0, "failed": 0, "skipped": 0}
        for case in selected:
            status = (
                "failed"
                if case.find("failure") is not None or case.find("error") is not None
                else "skipped"
                if case.find("skipped") is not None
                else "passed"
            )
            counts[status] += 1
        status = (
            "failed"
            if counts["failed"]
            else "passed"
            if complete and metadata["verified"] and not counts["skipped"] and not run_failed
            else "not_verified"
        )
        groups.append(
            {
                "id": key,
                "label": label,
                "status": status,
                **counts,
                "selectors": ["backend/tests/" + f for f in files],
            }
        )
    return groups, metadata


def main():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    before = code_fingerprint()
    report_path = ROOT / (".runtime/real-security-evidence-" + uuid4().hex[:12] + ".xml")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_integration.py"),
            "--business-login",
            "backend/tests",
            "--report",
            str(report_path),
        ],
        cwd=ROOT,
    )
    if not report_path.exists():
        raise SystemExit("No report produced; previous evidence was not overwritten")
    if (
        before != code_fingerprint()
        or commit != subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    ):
        raise SystemExit("Sources changed during verification; previous evidence was not overwritten")
    groups, metadata = summarize(report_path, commit)
    totals = Counter(
        "failed"
        if c.find("failure") is not None or c.find("error") is not None
        else "skipped"
        if c.find("skipped") is not None
        else "passed"
        for c in ET.parse(report_path).iter("testcase")
    )
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    report = {
        "schema_version": 2,
        "scope": "postgres_security",
        "environment": "隔离 PostgreSQL 16 · 真实 A/C/D · 本地模型替身",
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": commit,
        "worktree_dirty": dirty,
        "code_fingerprint": before,
        "source": metadata,
        "python": sys.version.split()[0],
        "groups": groups,
        "verification_totals": {key: totals[key] for key in ("passed", "failed", "skipped")},
        "junit_report": report_path.relative_to(ROOT).as_posix(),
        "not_verified": [
            "生产认证与正式业务库部署未验收",
            "外部模型质量、大规模性能及底表原生 RLS 未验收",
            "这是离线代码快照记录，不证明当前运行服务具备相同配置",
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "PostgreSQL evidence:",
        [(g["id"], g["status"], g["passed"], g["failed"], g["skipped"]) for g in groups],
    )
    return result.returncode or (0 if all(g["status"] == "passed" for g in groups) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
