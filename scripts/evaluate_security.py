"""Reproducible D experiments on owned disposable databases; never accepts a business DSN."""

import argparse
import asyncio
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]


def raw_query(config, text, parameters=None):
    """Intentionally unsafe baseline, bounded by a fresh role and a rolled-back transaction."""
    import psycopg

    with psycopg.connect(**config.conninfo(), autocommit=True) as connection:
        connection.execute("BEGIN")
        try:
            connection.execute("SET LOCAL statement_timeout='3s'")
            cursor = connection.execute(text, parameters, prepare=False)
            columns = [column.name for column in cursor.description] if cursor.description else []
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchmany(100)] if columns else []
            rows = json.loads(json.dumps(rows, default=str))
            return {"executed": True, "columns": columns, "rows": rows, "sqlstate": None}
        except psycopg.Error as error:
            return {"executed": False, "columns": [], "rows": [], "sqlstate": error.sqlstate}
        finally:
            connection.rollback()


def p95(values):
    return sorted(values)[math.ceil(len(values) * 0.95) - 1]


def business_snapshot(connection):
    rows = connection.execute(
        "SELECT jsonb_build_array("
        "(SELECT jsonb_agg(to_jsonb(p) ORDER BY project_id) FROM public.projects p),"
        "(SELECT jsonb_agg(to_jsonb(p) ORDER BY project_id,user_id) FROM public.project_members p),"
        "(SELECT jsonb_agg(to_jsonb(p) ORDER BY metadata_id) FROM public.papers p)),"
        "to_regclass('public.security_eval_probe') IS NULL"
    ).fetchone()
    assert rows[1], "Baseline DDL escaped its rollback"
    return hashlib.sha256(json.dumps(rows[0], sort_keys=True, default=str).encode()).hexdigest()


def group(name, iterations):
    import psycopg
    from psycopg import sql
    from app.data.adapter import PostgresData
    from app.data.database import PostgresDatabase
    from app.security import SecurityAdapter, PostgresQueryExecutor
    from app.security.audit import compose_audit
    from app.security.provision import install, create_login
    from app.security.sql_policy import SQL_BY_QUESTION, prepare_query
    from security_contracts.attack_cases import attacks, normal_queries
    from security_contracts.postgres_provider import postgres_workspace

    records, performance = [], []
    with postgres_workspace() as pg:
        assert pg.config.dbname == "paperpilot_d_step2" and pg.config.host == "127.0.0.1"
        ctx = pg.contexts[0]
        with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
            postgres_version = admin.info.server_version
            original_business = business_snapshot(admin)
            install(admin, existing_views=True)
            role, password = "pp_business_" + uuid4().hex, secrets.token_urlsafe(32)
            create_login(admin, role, password, business=True)
            business_config = replace(pg.config, user=role, password=password)
            audit_role, audit_password = "pp_audit_" + uuid4().hex, secrets.token_urlsafe(32)
            create_login(admin, audit_role, audit_password)
            if name != "gateway_and_database":
                # Remove only the database's project binding, keeping identical view columns and app SQL.
                source = (ROOT / "backend/app/security/migrations/001_statistics_views.sql").read_text(
                    encoding="utf-8"
                )
                binding = (
                    "  AND EXISTS (\n      SELECT 1 FROM paperpilot_security.project_readers r\n"
                    "      WHERE r.login_role = SESSION_USER AND r.project_id = pp.project_id\n  )"
                )
                assert source.count(binding) == 2
                views = re.findall(r"CREATE VIEW .*?;", source.replace(binding, ""), flags=re.S)
                assert len(views) == 2
                for view in views:
                    admin.execute(view.replace("CREATE VIEW", "CREATE OR REPLACE VIEW", 1))
                admin.execute(
                    sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                        sql.Identifier(role)
                    )
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA paperpilot_security TO {}").format(sql.Identifier(role))
                )
                admin.execute(
                    sql.SQL(
                        "GRANT SELECT ON paperpilot_security.paper_statistics,paperpilot_security.paper_topics TO {}"
                    ).format(sql.Identifier(role))
                )
                admin.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(sql.Identifier(role)))
                admin.execute(
                    sql.SQL("GRANT TEMPORARY ON DATABASE paperpilot_d_step2 TO {}").format(
                        sql.Identifier(role)
                    )
                )
        database = PostgresDatabase(business_config)
        data = PostgresData(database, pg.data.settings)
        writer = compose_audit(replace(pg.config, user=audit_role, password=audit_password))
        executor = PostgresQueryExecutor(pg.readers)
        executor_ms = 0.0

        async def execute(context, plan):
            nonlocal executor_ms
            start = perf_counter()
            try:
                if name == "gateway_and_database":
                    return await executor(context, plan)
                result = await asyncio.to_thread(raw_query, business_config, plan.sql, plan.parameters)
                if not result["executed"]:
                    raise RuntimeError("Controlled execution failed")
                return result["columns"], result["rows"]
            finally:
                executor_ms = (perf_counter() - start) * 1000

        try:
            cases = attacks(pg.contexts[1].project_id)
            assert Counter(c["category"] for c in cases) == dict.fromkeys(
                ["sql_injection", "cross_project", "dangerous_sql", "prompt_injection"], 10
            )
            for case in cases:
                ctx = replace(ctx, request_id=uuid4())
                start = perf_counter()
                if name == "no_gateway":
                    result = raw_query(business_config, case["sql"])
                    success, reason = result["executed"], result["sqlstate"] or "UNSUPPORTED_SQL_EXECUTED"
                else:

                    async def generated(*args):
                        return case["sql"]

                    result = asyncio.run(
                        SecurityAdapter(
                            data,
                            candidate_generator=generated,
                            query_executor=execute,
                            audit_append=writer.append,
                        ).execute(ctx, case.get("prompt", "controlled candidate"))
                    )
                    success = result.execution_status == "executed"
                    reason = result.reason.code if result.reason else result.execution_status
                records.append(
                    {
                        "case": case["id"],
                        "request_id": str(ctx.request_id),
                        "category": case["category"],
                        "attack_success": success,
                        "reason": reason,
                        "elapsed_ms": (perf_counter() - start) * 1000,
                    }
                )
            for case in normal_queries():
                ctx = replace(ctx, request_id=uuid4())
                plan = prepare_query(case["sql"], ctx.project_id)
                # An independent A statistic is the expected result; a 200 response alone is insufficient.
                intent = dict(zip(SQL_BY_QUESTION, ["by_year", "by_topic", "missing_evidence"], strict=True))[
                    case["question"]
                ]
                expected_columns, expected_rows = pg.data.controlled_statistics(ctx, intent)

                async def generated(*args):
                    return case["sql"]

                if name == "no_gateway":
                    result = raw_query(business_config, plan.sql, plan.parameters)
                    columns, rows = result["columns"], result["rows"]
                else:
                    result = asyncio.run(
                        SecurityAdapter(
                            data,
                            candidate_generator=generated,
                            query_executor=execute,
                            audit_append=writer.append,
                        ).execute(ctx, case["question"])
                    )
                    columns, rows = result.columns, result.rows

                def normalize(rows):
                    return sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)

                records.append(
                    {
                        "case": case["id"],
                        "category": "normal",
                        "request_id": str(ctx.request_id),
                        "normal_passed": columns == expected_columns
                        and normalize(rows) == normalize(expected_rows),
                    }
                )
            bypasses = []
            raw_config = (
                replace(
                    pg.config,
                    user=pg.readers[ctx.project_id].user,
                    password=pg.readers[ctx.project_id].password,
                )
                if name == "gateway_and_database"
                else business_config
            )
            for label, candidate in [
                (
                    "foreign_view",
                    "SELECT paper_id FROM paperpilot_security.paper_statistics WHERE project_id='"
                    + str(pg.contexts[1].project_id)
                    + "'",
                ),
                ("base_table", "SELECT title FROM public.papers"),
                ("write", "UPDATE public.projects SET name='controlled bypass'"),
            ]:
                result = raw_query(raw_config, candidate)
                succeeded = result["executed"] and (bool(result["rows"]) if label != "write" else True)
                bypasses.append(
                    {
                        "case": label,
                        "attack_success": succeeded,
                        "reason": result["sqlstate"]
                        or ("DATABASE_SCOPE_FILTERED" if not succeeded else "BYPASS_EXECUTED"),
                    }
                )
            if iterations:
                adapter = SecurityAdapter(data, query_executor=execute, audit_append=writer.append)
                for question in SQL_BY_QUESTION:
                    samples = []
                    for i in range(iterations + 5):
                        ctx = replace(ctx, request_id=uuid4())
                        start = perf_counter()
                        if name == "no_gateway":
                            plan = prepare_query(SQL_BY_QUESTION[question], ctx.project_id)
                            result = raw_query(business_config, plan.sql, plan.parameters)
                            executor_ms = (perf_counter() - start) * 1000
                            assert result["executed"]
                        else:
                            result = asyncio.run(adapter.execute(ctx, question))
                            assert result.execution_status == "executed", (name, question, result.reason)
                        elapsed = (perf_counter() - start) * 1000
                        if i >= 5:
                            samples.append(
                                {
                                    "total_ms": elapsed,
                                    "request_id": str(ctx.request_id),
                                    "executor_ms": executor_ms,
                                    "gateway_added_ms": max(0, elapsed - executor_ms),
                                }
                            )
                    performance.append(
                        {
                            "question": question,
                            "samples": samples,
                            "gateway_added_p95_ms": p95([s["gateway_added_ms"] for s in samples]),
                            "total_p95_ms": p95([s["total_ms"] for s in samples]),
                        }
                    )
            attacks_run = [r for r in records if r["category"] != "normal"]
            normal = [r for r in records if r["category"] == "normal"]
            with psycopg.connect(**pg.config.conninfo(), autocommit=True) as observer:
                assert business_snapshot(observer) == original_business, (
                    "An experiment modified business data"
                )
            return {
                "configuration": name,
                "postgres_version": postgres_version,
                "business_snapshot_unchanged": True,
                "records": records,
                "direct_bypass": bypasses,
                "attack_success_rate": sum(r["attack_success"] for r in attacks_run) / len(attacks_run),
                "normal_pass_rate": sum(r["normal_passed"] for r in normal) / len(normal),
                "performance": performance,
                "data": {"projects": 2, "papers_including_removed": 4, "rows_are_synthetic": True},
            }
        finally:
            writer.close()
            database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/security-evaluation/results.json")
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    if args.iterations < 30:
        parser.error("Use at least 30 measured requests per supported question")
    os.environ["PAPERPILOT_SECURITY_CURRENT_SOURCE"] = "1"
    from security_contracts.postgres_provider import IMAGE
    from security_contracts.attack_cases import attacks, normal_queries
    from export_real_security_evidence import code_fingerprint

    dataset = {"attacks": attacks("00000000-0000-0000-0000-000000000002"), "normal": normal_queries()}
    configs = [
        group(name, args.iterations) for name in ["no_gateway", "gateway_only", "gateway_and_database"]
    ]
    complete = configs[-1]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        ),
        "dataset_sha256": hashlib.sha256(json.dumps(dataset, sort_keys=True).encode()).hexdigest(),
        "application_fingerprint": code_fingerprint(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "dataset": dataset,
        "image": IMAGE,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "physical_memory_gib": float(
            subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)",
                ],
                text=True,
            ).strip()
        )
        if os.name == "nt"
        else os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1024**3,
        "concurrency": 1,
        "cache": "five warmups per question; warm DB; new query connection each request",
        "gateway_timing": "adapter elapsed minus entire executor call; auth and critical audit database I/O retained; executor connection/ACL/query/fetch excluded",
        "candidate_source": "fixed templates and hostile candidate fixtures; no live model-generated SQL evaluated",
        "limits": [
            "synthetic small dataset; no large-data extrapolation",
            "prompt candidates are controlled attacker outputs; not a live-model resistance score",
            "baseline writes rolled back; disposable databases only",
        ],
        "configurations": configs,
    }
    report["accepted"] = (
        complete["attack_success_rate"] == 0
        and complete["normal_pass_rate"] >= 0.95
        and not any(b["attack_success"] for b in complete["direct_bypass"])
        and all(p["gateway_added_p95_ms"] <= 200 for p in complete["performance"])
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    for config in configs:
        print(
            config["configuration"],
            "attack_success_rate=",
            config["attack_success_rate"],
            "normal_pass_rate=",
            config["normal_pass_rate"],
        )
    print("Gateway added P95 ms:", [round(p["gateway_added_p95_ms"], 3) for p in complete["performance"]])
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
