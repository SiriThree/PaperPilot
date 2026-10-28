"""K01-K15 positive/negative constraint probes on the real migrated schema.

Creates a disposable database, runs every probe in its own transaction (negative probes
must fail at statement time or COMMIT, positive probes must commit), then drops it.
Writes a machine-readable record to docs/day2-constraint-verification.json.

Usage (PowerShell, repository root, after start-postgres-day0.ps1):
    .\\.venv\\Scripts\\python.exe scripts/verify_constraints_day2.py
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402

REPORT = ROOT / "docs" / "day2-constraint-verification.json"
DB_PREFIX = "paperpilot_constraints_"


def admin_config():
    values = {}
    path = ROOT / ".runtime" / "day0.env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values.setdefault(key.strip(), value.strip())
    password = os.getenv("PAPERPILOT_TEST_DATA_PASSWORD") or values.get("PAPERPILOT_DAY0_PASSWORD")
    if not password:
        raise RuntimeError("Run start-postgres-day0.ps1 or set PAPERPILOT_TEST_DATA_PASSWORD first")
    return ConnectionConfig(
        host=os.getenv("PAPERPILOT_TEST_DATA_HOST", "127.0.0.1"),
        port=int(os.getenv("PAPERPILOT_TEST_DATA_PORT", values.get("PAPERPILOT_DAY0_PORT", "54326"))),
        user=os.getenv("PAPERPILOT_TEST_DATA_USER", "postgres"),
        password=password,
        dbname="postgres",
    )


def uid():
    return str(uuid.uuid4())


def base_entities(conn):
    """One user, one project with its owner row; returns identifiers."""
    user, other, project = uid(), uid(), uid()
    name_a, name_b = "probe-a-" + uid()[:10], "probe-b-" + uid()[:10]
    conn.execute(
        "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,'A'),(%s,%s,'B')",
        (user, name_a, other, name_b),
    )
    conn.execute(
        "INSERT INTO projects(project_id, name, owner_id) VALUES(%s,'约束探针项目',%s)", (project, user)
    )
    conn.execute(
        "INSERT INTO project_members(project_id, user_id, role) VALUES(%s,%s,'owner')", (project, user)
    )
    return {"user": user, "other": other, "project": project, "name": name_a}


def imported_paper(conn, ids, *, digest=None):
    """A live project paper with metadata, status pending."""
    metadata, paper = uid(), uid()
    digest = digest or uid().replace("-", "") * 2
    conn.execute(
        "INSERT INTO papers(metadata_id, project_id, title) VALUES(%s,%s,'探针论文')",
        (metadata, ids["project"]),
    )
    conn.execute(
        "INSERT INTO project_papers(paper_id, project_id, metadata_id, imported_by, digest, storage_key)"
        " VALUES(%s,%s,%s,%s,%s,'probe/paper.pdf')",
        (paper, ids["project"], metadata, ids["user"], digest),
    )
    return paper


def claimed_task(conn, ids, paper_id, *, kind="process", attempts=1, status="processing"):
    """Task plus a claimed attempt; optionally a succeeded attempt and derived paper state."""
    task = uid()
    conn.execute(
        "INSERT INTO tasks(task_id, project_id, actor_id, request_id, kind, status, attempts, payload,"
        " payload_fingerprint) VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
        (
            task,
            ids["project"],
            ids["user"],
            uid(),
            kind,
            status,
            attempts,
            json_scope_payload(paper_id) if kind == "process" else '{"paper_ids": []}',
            "0" * 64,
        ),
    )
    conn.execute(
        "INSERT INTO task_papers(task_id, paper_id, project_id) VALUES(%s,%s,%s)",
        (task, paper_id, ids["project"]),
    )
    conn.execute(
        "INSERT INTO task_attempts(task_id, attempt_no, project_id, actor_id, request_id, status,"
        " lease_expires_at) VALUES(%s,%s,%s,%s,%s,'processing', now() + interval '120 seconds')",
        (task, attempts, ids["project"], ids["user"], uid()),
    )
    if status == "succeeded":
        conn.execute(
            "UPDATE task_attempts SET status='succeeded', finished_at=now() WHERE task_id=%s"
            " AND attempt_no=%s",
            (task, attempts),
        )
        conn.execute("UPDATE tasks SET status='succeeded' WHERE task_id=%s", (task,))
        conn.execute(
            "UPDATE project_papers SET status='processing' WHERE paper_id=%s", (paper_id,)
        )
    return task


def json_scope_payload(paper_id):
    import json

    return json.dumps({"paper_ids": [paper_id]})


def chunk(conn, ids, paper_id):
    chunk_id = uid()
    conn.execute(
        "INSERT INTO chunks(chunk_id, project_id, paper_id, page_number, excerpt, synthetic)"
        " VALUES(%s,%s,%s,1,'探针片段',false)",
        (chunk_id, ids["project"], paper_id),
    )
    return chunk_id


PROBES = []


def probe(rule, name, positive, setup=None):
    def decorate(function):
        PROBES.append((rule, name, positive, function))
        return function

    return decorate


# ---- K01 user and project field constraints ---------------------------------


@probe("K01", "重复用户名被拒绝", False)
def k01_duplicate_username(conn, ids):
    conn.execute(
        "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,'X')", (uid(), ids["name"])
    )


@probe("K01", "合法用户与项目字段可提交", True)
def k01_valid_user_project(conn, ids):
    conn.execute(
        "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,'OK')",
        (uid(), "probe-ok-" + uid()[:10]),
    )
    conn.execute(
        "INSERT INTO projects(project_id, name, description, owner_id) VALUES(%s,'合法名称','',%s)",
        (uid(), ids["user"]),
    )
    conn.execute(
        "INSERT INTO project_members(project_id, user_id, role) VALUES( (SELECT project_id FROM projects"
        " WHERE owner_id=%s AND name='合法名称'), %s, 'owner')",
        (ids["user"], ids["user"]),
    )


@probe("K01", "超长用户名/项目名被拒绝", False)
def k01_overlong_fields(conn, ids):
    conn.execute(
        "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,'X')", (uid(), "u" * 65)
    )


# ---- K02 owner membership ----------------------------------------------------


@probe("K02", "项目与负责人同事务提交", True)
def k02_valid_owner(conn, ids):
    pass  # base_entities already pairs the project with its owner member row


@probe("K02", "无负责人成员的项目提交被拒绝", False)
def k02_ownerless_project(conn, ids):
    user = uid()
    conn.execute(
        "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,'C')",
        (user, "probe-c-" + uid()[:10]),
    )
    conn.execute(
        "INSERT INTO projects(project_id, name, owner_id) VALUES(%s,'无负责人',%s)", (uid(), user)
    )
    # No project_members row: the deferred composite FK must fail the COMMIT.


@probe("K02", "第二个负责人被拒绝", False)
def k02_second_owner(conn, ids):
    conn.execute(
        "INSERT INTO project_members(project_id, user_id, role) VALUES(%s,%s,'owner')",
        (ids["project"], ids["other"]),
    )


# ---- K03 owner immutability ---------------------------------------------------


@probe("K03", "负责人不可转让", False)
def k03_owner_transfer(conn, ids):
    conn.execute("UPDATE projects SET owner_id=%s WHERE project_id=%s", (ids["other"], ids["project"]))


@probe("K03", "负责人成员行不可删除或降级", False)
def k03_owner_demote(conn, ids):
    conn.execute(
        "UPDATE project_members SET role='member' WHERE project_id=%s AND user_id=%s",
        (ids["project"], ids["user"]),
    )


# ---- K04 import uniqueness and pairing ----------------------------------------


@probe("K04", "合法导入元数据与项目论文成对提交", True)
def k04_valid_import(conn, ids):
    imported_paper(conn, ids)


@probe("K04", "项目内同摘要重复导入被拒绝", False)
def k04_duplicate_digest(conn, ids):
    digest = uid().replace("-", "") * 2
    imported_paper(conn, ids, digest=digest)
    metadata = uid()
    conn.execute(
        "INSERT INTO papers(metadata_id, project_id, title) VALUES(%s,%s,'重复摘要')",
        (metadata, ids["project"]),
    )
    conn.execute(
        "INSERT INTO project_papers(paper_id, project_id, metadata_id, imported_by, digest, storage_key)"
        " VALUES(%s,%s,%s,%s,%s,'probe/again.pdf')",
        (uid(), ids["project"], metadata, ids["user"], digest),
    )


@probe("K04", "无项目关联的书目元数据提交被拒绝", False)
def k04_orphan_metadata(conn, ids):
    conn.execute(
        "INSERT INTO papers(metadata_id, project_id, title) VALUES(%s,%s,'孤儿元数据')",
        (uid(), ids["project"]),
    )
    # No project_papers row: K04 deferred trigger must fail the COMMIT.


# ---- K05 project attribution composite FKs -------------------------------------


@probe("K05", "跨项目引用资源被复合外键拒绝", False)
def k05_cross_project_reference(conn, ids):
    other_user, other_project = uid(), uid()
    conn.execute(
        "INSERT INTO users(user_id, username, display_name) VALUES(%s,%s,'D')",
        (other_user, "probe-d-" + uid()[:10]),
    )
    conn.execute(
        "INSERT INTO projects(project_id, name, owner_id) VALUES(%s,'另一项目',%s)",
        (other_project, other_user),
    )
    conn.execute(
        "INSERT INTO project_members(project_id, user_id, role) VALUES(%s,%s,'owner')",
        (other_project, other_user),
    )
    paper = imported_paper(conn, ids)
    conn.execute(
        "INSERT INTO task_papers(task_id, paper_id, project_id) VALUES(%s,%s,%s)",
        (uid(), paper, other_project),
    )


# ---- K06 value domains -----------------------------------------------------------


@probe("K06", "非法摘要/年份/状态被拒绝", False)
def k06_bad_values(conn, ids):
    metadata = uid()
    conn.execute(
        "INSERT INTO papers(metadata_id, project_id, title, year) VALUES(%s,%s,'坏年份',5000)",
        (metadata, ids["project"]),
    )


@probe("K06", "小写十六进制摘要与合法年份可提交", True)
def k06_good_values(conn, ids):
    imported_paper(conn, ids, digest="0123456789abcdef" * 4)


# ---- K07 chunk immutability --------------------------------------------------------


@probe("K07", "已提交片段不可原位修改或删除", False)
def k07_chunk_mutation(conn, ids):
    paper = imported_paper(conn, ids)
    chunk_id = chunk(conn, ids, paper)
    conn.execute("UPDATE chunks SET excerpt='篡改' WHERE chunk_id=%s", (chunk_id,))


# ---- K08 attempts and results --------------------------------------------------------


@probe("K08", "同任务第二个在途尝试被拒绝", False)
def k08_second_processing_attempt(conn, ids):
    paper = imported_paper(conn, ids)
    task = claimed_task(conn, ids, paper)
    conn.execute(
        "INSERT INTO task_attempts(task_id, attempt_no, project_id, actor_id, request_id, status,"
        " lease_expires_at) VALUES(%s,2,%s,%s,%s,'processing', now() + interval '120 seconds')",
        (task, ids["project"], ids["user"], uid()),
    )


@probe("K08", "成功结果必须挂在已成功的尝试上", False)
def k08_result_without_succeeded_attempt(conn, ids):
    paper = imported_paper(conn, ids)
    task = claimed_task(conn, ids, paper, status="processing")
    conn.execute("UPDATE tasks SET status='succeeded' WHERE task_id=%s", (task,))
    conn.execute(
        "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, paper_id, payload,"
        " result_fingerprint) VALUES(%s,%s,'process',1,%s,%s::jsonb,%s)",
        (task, ids["project"], paper, json_scope_payload(paper), "1" * 64),
    )
    # Attempt 1 is still 'processing'; the deferred trigger must fail the COMMIT.


@probe("K08", "成功任务与成功尝试及结果一致提交", True)
def k08_consistent_success(conn, ids):
    paper = imported_paper(conn, ids)
    task = claimed_task(conn, ids, paper, status="succeeded")
    conn.execute(
        "INSERT INTO chunks(chunk_id, project_id, paper_id, page_number, excerpt, synthetic)"
        " VALUES(%s,%s,%s,1,'成功片段',false)", (uid(), ids["project"], paper)
    )
    conn.execute(
        "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, paper_id, payload,"
        " result_fingerprint) VALUES(%s,%s,'process',1,%s,%s::jsonb,%s)",
        (task, ids["project"], paper, json_scope_payload(paper), "2" * 64),
    )
    conn.execute(
        "INSERT INTO result_sources(task_id, scope, citation_id, project_id, ordinal, paper_id,"
        " chunk_id, page_number, excerpt, synthetic)"
        " VALUES(%s,'root','1',%s,1,%s,(SELECT chunk_id FROM chunks WHERE paper_id=%s LIMIT 1),1,'成功片段',false)",
        (task, ids["project"], paper, paper),
    )
    conn.execute(
        "UPDATE project_papers SET status='searchable', index_state='mock_only',"
        " active_process_task_id=%s WHERE paper_id=%s",
        (task, paper),
    )


# ---- K09 task scope and result kind ---------------------------------------------------


@probe("K09", "结果类型与任务类型不一致被拒绝", False)
def k09_kind_mismatch(conn, ids):
    paper = imported_paper(conn, ids)
    task = claimed_task(conn, ids, paper, kind="answer", status="succeeded")
    conn.execute(
        "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, evidence_id, payload,"
        " result_fingerprint) VALUES(%s,%s,'process',1,%s,%s::jsonb,%s)",
        (task, ids["project"], paper, json_scope_payload(paper), "3" * 64),
    )


# ---- K10 evidence shape -----------------------------------------------------------------


def evidence_task(conn, ids, paper_id):
    task = claimed_task(conn, ids, paper_id, kind="evidence", status="succeeded")
    evidence = uid()
    conn.execute(
        "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, paper_id, evidence_id,"
        " payload, result_fingerprint) VALUES(%s,%s,'evidence',1,%s,%s,%s::jsonb,%s)",
        (task, ids["project"], paper_id, evidence, json_scope_payload(paper_id), "4" * 64),
    )
    return task, evidence


@probe("K10", "证据恰好五个字段时提交成功", True)
def k10_valid_evidence(conn, ids):
    paper = imported_paper(conn, ids)
    task, _ = evidence_task(conn, ids, paper)
    chunk_id = chunk(conn, ids, paper)
    conn.cursor().executemany(
        "INSERT INTO evidence_fields(task_id, field_name, project_id, text, status)"
        " VALUES(%s,%s,%s,'证据文本','pending_verification')",
        [(task, name, ids["project"]) for name in
         ("research_question", "method", "results", "conclusion", "limitations")],
    )
    conn.cursor().executemany(
        "INSERT INTO result_sources(task_id, scope, citation_id, project_id, field_name, ordinal,"
        " paper_id, chunk_id, page_number, excerpt, synthetic)"
        " VALUES(%s,%s,%s,%s,%s,1,%s,%s,1,'探针片段',false)",
        [(task, name, uid()[:8], ids["project"], name, paper, chunk_id)
         for name in ("research_question", "method", "results", "conclusion", "limitations")],
    )


@probe("K10", "证据字段缺失时提交被拒绝", False)
def k10_missing_field(conn, ids):
    paper = imported_paper(conn, ids)
    task, _ = evidence_task(conn, ids, paper)
    conn.cursor().executemany(
        "INSERT INTO evidence_fields(task_id, field_name, project_id, text, status)"
        " VALUES(%s,%s,%s,NULL,'not_mentioned')",
        [(task, name, ids["project"]) for name in ("research_question", "method", "results", "conclusion")],
    )
    # Only four fields; the deferred K10 trigger must fail the COMMIT.


@probe("K10", "重复 evidence_id 被全局唯一索引拒绝", False)
def k10_duplicate_evidence(conn, ids):
    paper = imported_paper(conn, ids)
    _, evidence = evidence_task(conn, ids, paper)
    other = imported_paper(conn, ids)
    other_task = claimed_task(conn, ids, other, kind="evidence", status="succeeded")
    conn.execute(
        "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, paper_id, evidence_id,"
        " payload, result_fingerprint) VALUES(%s,%s,'evidence',1,%s,%s,%s::jsonb,%s)",
        (other_task, ids["project"], other, evidence, json_scope_payload(other), "5" * 64),
    )


@probe("K10", "非证据结果不得携带证据字段", False)
def k10_fields_on_answer(conn, ids):
    paper = imported_paper(conn, ids)
    task = claimed_task(conn, ids, paper, kind="answer", status="succeeded")
    conn.execute(
        "INSERT INTO task_results(task_id, project_id, kind, committed_attempt, payload,"
        " result_fingerprint) VALUES(%s,%s,'answer',1,'{}'::jsonb,%s)",
        (task, ids["project"], "6" * 64),
    )
    conn.execute(
        "INSERT INTO evidence_fields(task_id, field_name, project_id, text, status)"
        " VALUES(%s,'method',%s,'文本','not_mentioned')",
        (task, ids["project"]),
    )


# ---- K11 report chain ----------------------------------------------------------------------


def report_with_version(conn, ids):
    report, version = uid(), uid()
    conn.execute(
        "INSERT INTO reports(report_id, project_id, created_by, current_version_id) VALUES(%s,%s,%s,%s)",
        (report, ids["project"], ids["user"], version),
    )
    conn.execute(
        "INSERT INTO report_versions(version_id, project_id, report_id, number, title,"
        " research_question, body, created_by) VALUES(%s,%s,%s,1,'标题','问题','正文一',%s)",
        (version, ids["project"], report, ids["user"]),
    )
    return report, version


@probe("K11", "连续版本与最新指针提交成功", True)
def k11_valid_chain(conn, ids):
    report, first = report_with_version(conn, ids)
    second = uid()
    conn.execute(
        "INSERT INTO report_versions(version_id, project_id, report_id, number, base_version_id,"
        " title, research_question, body, created_by) VALUES(%s,%s,%s,2,%s,'标题','问题','正文二',%s)",
        (second, ids["project"], report, first, ids["user"]),
    )
    conn.execute("UPDATE reports SET current_version_id=%s WHERE report_id=%s", (second, report))


@probe("K11", "跳号或分叉的版本被拒绝", False)
def k11_gap_or_fork(conn, ids):
    report, first = report_with_version(conn, ids)
    conn.execute(
        "INSERT INTO report_versions(version_id, project_id, report_id, number, base_version_id,"
        " title, research_question, body, created_by) VALUES(%s,%s,%s,3,%s,'标题','问题','跳号',%s)",
        (uid(), ids["project"], report, first, ids["user"]),
    )


@probe("K11", "历史版本正文不可覆盖", False)
def k11_version_mutation(conn, ids):
    report, first = report_with_version(conn, ids)
    conn.execute("UPDATE report_versions SET body='篡改' WHERE version_id=%s", (first,))


@probe("K11", "最新版本指针必须指向最大版本", False)
def k11_stale_pointer(conn, ids):
    report, first = report_with_version(conn, ids)
    second = uid()
    conn.execute(
        "INSERT INTO report_versions(version_id, project_id, report_id, number, base_version_id,"
        " title, research_question, body, created_by) VALUES(%s,%s,%s,2,%s,'标题','问题','正文二',%s)",
        (second, ids["project"], report, first, ids["user"]),
    )
    conn.execute("UPDATE reports SET current_version_id=%s WHERE report_id=%s", (first, report))


# ---- K12 idempotency --------------------------------------------------------------------------


@probe("K12", "幂等键重复插入被主键拒绝", False)
def k12_duplicate_key(conn, ids):
    paper = imported_paper(conn, ids)
    key = "probe-key-1"
    conn.execute(
        "INSERT INTO idempotency_records(user_id, project_id, operation, key, request_fingerprint,"
        " response_snapshot, paper_id, task_id) VALUES(%s,%s,'upload',%s,%s,'{}'::jsonb,%s,%s)",
        (ids["user"], ids["project"], key, "a" * 64, paper, paper),
    )
    conn.execute(
        "INSERT INTO idempotency_records(user_id, project_id, operation, key, request_fingerprint,"
        " response_snapshot, paper_id, task_id) VALUES(%s,%s,'upload',%s,%s,'{}'::jsonb,%s,%s)",
        (ids["user"], ids["project"], key, "b" * 64, paper, paper),
    )


@probe("K12", "幂等记录目标列与操作不符被拒绝", False)
def k12_wrong_target(conn, ids):
    conn.execute(
        "INSERT INTO idempotency_records(user_id, project_id, operation, key, request_fingerprint,"
        " response_snapshot, version_id) VALUES(%s,%s,'answer',%s,%s,'{}'::jsonb,%s)",
        (ids["user"], ids["project"], uid()[:32], "c" * 64, uid()),
    )


# ---- K13 audit uniqueness and append-only -------------------------------------------------------


def audit_row(conn, ids, event_id=None):
    event_id = event_id or uid()
    conn.execute(
        "INSERT INTO audit_events(event_id, timestamp, request_id, actor_id, project_id, action,"
        " resource_type, decision, execution_status, duration_ms, mock, event_fingerprint)"
        " VALUES(%s, now(), %s, %s, %s, 'probe.action', 'probe', 'allowed', 'completed', 1, false, %s)",
        (event_id, uid(), ids["user"], ids["project"], "d" * 64),
    )
    return event_id


@probe("K13", "审计事件追加成功且事件编号唯一", True)
def k13_valid_append(conn, ids):
    audit_row(conn, ids)
    conn.execute(
        "INSERT INTO audit_events(event_id, timestamp, request_id, actor_id, project_id, action,"
        " resource_type, decision, execution_status, duration_ms, mock, event_fingerprint)"
        " VALUES(%s, now(), %s, %s, %s, 'probe.other', 'probe', 'allowed', 'completed', 1, false, %s)",
        (uid(), uid(), ids["user"], ids["project"], "e" * 64),
    )


@probe("K13", "审计历史不可更新或删除", False)
def k13_audit_mutation(conn, ids):
    event_id = audit_row(conn, ids)
    conn.execute("UPDATE audit_events SET decision='rejected' WHERE event_id=%s", (event_id,))


@probe("K13", "负值或非有限耗时被拒绝", False)
def k13_bad_duration(conn, ids):
    conn.execute(
        "INSERT INTO audit_events(event_id, timestamp, request_id, actor_id, project_id, action,"
        " resource_type, decision, execution_status, duration_ms, mock, event_fingerprint)"
        " VALUES(%s, now(), %s, %s, %s, 'probe.bad', 'probe', 'allowed', 'completed', 'NaN', false, %s)",
        (uid(), uid(), ids["user"], ids["project"], "f" * 64),
    )


# ---- K14 retention without cascades ---------------------------------------------------------------


@probe("K14", "被业务引用的用户不可物理删除", False)
def k14_user_delete_blocked(conn, ids):
    conn.execute("DELETE FROM users WHERE user_id=%s", (ids["user"],))


@probe("K14", "成员撤权后历史操作者引用仍保留", True)
def k14_member_removal_keeps_history(conn, ids):
    paper = imported_paper(conn, ids)
    claimed_task(conn, ids, paper)
    conn.execute(
        "INSERT INTO project_members(project_id, user_id, role) VALUES(%s,%s,'member')",
        (ids["project"], ids["other"]),
    )
    conn.execute(
        "DELETE FROM project_members WHERE project_id=%s AND user_id=%s", (ids["project"], ids["other"])
    )


# ---- K15 embedding/model consistency ----------------------------------------------------------------


@probe("K15", "模型登记与维度一致的向量提交成功", True)
def k15_valid_embedding(conn, ids):
    model = uid()
    conn.execute(
        "INSERT INTO embedding_models(model_id, project_id, provider, model_name, revision, dimensions,"
        " distance_metric) VALUES(%s,%s,'probe','model','v1',3,'cosine')",
        (model, ids["project"]),
    )
    paper = imported_paper(conn, ids)
    chunk_id = chunk(conn, ids, paper)
    conn.execute(
        "INSERT INTO chunk_embeddings(chunk_id, model_id, project_id, dimensions, embedding)"
        " VALUES(%s,%s,%s,3,'[1,2,3]')",
        (chunk_id, model, ids["project"]),
    )


@probe("K15", "向量维度与模型登记不一致被拒绝", False)
def k15_dimension_mismatch(conn, ids):
    model = uid()
    conn.execute(
        "INSERT INTO embedding_models(model_id, project_id, provider, model_name, revision, dimensions,"
        " distance_metric) VALUES(%s,%s,'probe','model2','v1',3,'cosine')",
        (model, ids["project"]),
    )
    paper = imported_paper(conn, ids)
    chunk_id = chunk(conn, ids, paper)
    conn.execute(
        "INSERT INTO chunk_embeddings(chunk_id, model_id, project_id, dimensions, embedding)"
        " VALUES(%s,%s,%s,3,'[1,2,3,4]')",
        (chunk_id, model, ids["project"]),
    )


@probe("K15", "模型维度与版本登记不可原位更改", False)
def k15_model_mutation(conn, ids):
    model = uid()
    conn.execute(
        "INSERT INTO embedding_models(model_id, project_id, provider, model_name, revision, dimensions,"
        " distance_metric) VALUES(%s,%s,'probe','model3','v1',3,'cosine')",
        (model, ids["project"]),
    )
    conn.execute(
        "UPDATE embedding_models SET dimensions=4 WHERE model_id=%s", (model,)
    )


# ---- C13 outbox delivery columns vs identity --------------------------------------------------


def outbox_row(conn, ids):
    event_id = uid()
    conn.execute(
        "INSERT INTO outbox_events(event_id, project_id, actor_id, request_id, payload)"
        " VALUES(%s,%s,%s,%s,%s::jsonb)",
        (
            event_id,
            ids["project"],
            ids["user"],
            uid(),
            '{"action":"probe.action","resource_type":"project","resource_id":"x",'
            '"decision":"allowed","execution_status":"succeeded","duration_ms":0,"mock":false}',
        ),
    )
    return event_id


@probe("C13", "outbox 投递状态列可更新（触发器必须放行）", True)
def c13_delivery_columns_updatable(conn, ids):
    event_id = outbox_row(conn, ids)
    conn.execute(
        "UPDATE outbox_events SET delivery_attempts=1, next_attempt_at=now(), published_at=now(),"
        " last_error_code=NULL WHERE event_id=%s",
        (event_id,),
    )


@probe("C13", "outbox 身份与载荷不可更新", False)
def c13_identity_immutable(conn, ids):
    event_id = outbox_row(conn, ids)
    conn.execute("UPDATE outbox_events SET payload='{}'::jsonb WHERE event_id=%s", (event_id,))


def run_probe(conn, function, positive):
    """Execute one probe in its own transaction; negatives must raise, positives must commit."""
    conn.execute("BEGIN")
    try:
        ids = base_entities(conn)
        function(conn, ids)
        conn.commit()
        return True, None
    except psycopg.Error as error:
        conn.rollback()
        if positive:
            return False, f"{type(error).__name__}: {str(error).splitlines()[0]}"
        return True, type(error).__name__
    except Exception as error:  # noqa: BLE001 - probe crash is a failure record
        conn.rollback()
        return False, f"{type(error).__name__}: {error}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=str(REPORT))
    options = parser.parse_args()

    admin = admin_config()
    dbname = DB_PREFIX + uuid.uuid4().hex[:12]
    with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    config = ConnectionConfig(
        host=admin.host, port=admin.port, user=admin.user, password=admin.password, dbname=dbname
    )
    database = PostgresDatabase(config)
    try:
        database.migrate()
        with psycopg.connect(row_factory=None, **config.conninfo()) as conn:
            record = {"database": "postgres16+pgvector", "probes": [], "passed": 0, "failed": 0}
            for rule, name, positive, function in PROBES:
                ok, detail = run_probe(conn, function, positive)
                record["probes"].append(
                    {"rule": rule, "name": name, "expect_commit": positive, "passed": ok, "detail": detail}
                )
                record["passed" if ok else "failed"] += 1
                mark = "PASS" if ok else "FAIL"
                print(f"[{mark}] {rule} {name}" + ("" if ok else f" :: {detail}"))
        Path(options.report).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"report -> {options.report} ({record['passed']} passed, {record['failed']} failed)")
        if record["failed"]:
            raise SystemExit(1)
    finally:
        database.close()
        with psycopg.connect(**admin.conninfo(), autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname))
            )


if __name__ == "__main__":
    main()
