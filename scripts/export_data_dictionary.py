"""Export the live PostgreSQL schema as the frontend data-dictionary snapshot.

Reads table/column/constraint/index facts from pg_catalog and information_schema
of the database named by PAPERPILOT_DATA_* (the same explicit connection rules as
manage_postgres.py), joins them with curated domain groups and Chinese table
descriptions, and writes ``frontend/src/data/data-dictionary.json`` which the
dictionary page renders. Row counts are an export-time snapshot, never live.

    python scripts/export_data_dictionary.py [--check]
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402

OUTPUT = ROOT / "frontend" / "src" / "data" / "data-dictionary.json"

# Domain grouping and curated descriptions; column/constraint facts always come
# from the live catalog, never from this table.
GROUPS = {
    "users": ("身份与协作", "唯一业务用户来源；正式密码摘要与会话结构待 A/B/D 冻结。"),
    "projects": ("身份与协作", "研究项目；负责人不可变，与 owner 成员关系同事务创建。"),
    "project_members": (
        "身份与协作",
        "项目成员关系；部分唯一索引保证每项目至多一位 owner，移除普通成员即物理删除此行。",
    ),
    "papers": ("文献与内容", "每次导入独立的书目元数据；metadata_id 从不作为公开 paper_id 返回。"),
    "project_papers": (
        "文献与内容",
        "项目×论文关联（paper_id 即一次导入身份）；移出为软删除（removed_at），项目内摘要唯一。",
    ),
    "authors": ("文献与内容", "作者书目信息。"),
    "paper_authors": ("文献与内容", "论文-作者关联，保持书目顺序。"),
    "topics": ("文献与内容", "研究主题词；by_topic 统计的数据源之一。"),
    "paper_topics": ("文献与内容", "论文-主题关联。"),
    "chunks": (
        "文献与内容",
        "处理产出的来源片段（页码 + 摘录）；不可变触发器保护，检索与引用定位的基本单位。",
    ),
    "embedding_models": ("向量与索引", "版本化 embedding 模型登记；真实模型维度待 C 确认。"),
    "chunk_embeddings": ("向量与索引", "片段向量；不定维 vector + 模型版本，换模型须重建索引。"),
    "process_indexes": (
        "向量与索引",
        "process 结果与模型/维度/索引指纹的不可变提交关系（迁移 002 新增，真实 C 的原子索引提交面）。",
    ),
    "tasks": (
        "任务与结果",
        "process/answer/evidence/draft 四类任务；按 task_id+attempts 条件提交，拒绝迟到写入。",
    ),
    "task_papers": ("任务与结果", "任务-论文多对多关联。"),
    "task_attempts": (
        "任务与结果",
        "worker 领取与租约记录；部分唯一索引保证每任务至多一个在途尝试，过期租约可被恢复领取。",
    ),
    "task_results": (
        "任务与结果",
        "四类任务共享的稳定结果容器；论文 searchable 状态由此派生，不存可悬空 result_ref。",
    ),
    "evidence_fields": ("任务与结果", "证据五字段（研究问题/方法/结果/结论/局限），各自带状态与支撑来源。"),
    "result_sources": ("任务与结果", "结果引用的来源快照，按 scope+ordinal 保序。"),
    "reports": ("报告与版本", "报告容器；current_version_id 指向最新版本，旧版本永不覆盖。"),
    "report_versions": (
        "报告与版本",
        "不可变报告版本；(report_id, number) 唯一，base_version_id 乐观并发控制。",
    ),
    "report_version_sources": (
        "报告与版本",
        "保存时的引用快照副本；论文移出后旧版本报告仍可完整回读当时来源。",
    ),
    "idempotency_records": (
        "一致性与审计",
        "幂等键账本（用户+项目+操作+键）；相同键不同载荷 409，演示期无限保留。",
    ),
    "audit_events": ("一致性与审计", "审计事件；追加写 + 事件指纹去重，业务入口不可修改或删除。"),
    "outbox_events": (
        "一致性与审计",
        "领域事件 outbox；由独立投递器按至少一次语义投递，event_id 供审计去重。",
    ),
    "outbox_requeue_history": (
        "一致性与审计",
        "outbox 死信重放的不可变操作历史（迁移 004）；重投保留原事件 ID，只记录操作员对账声明。",
    ),
    "auth_sessions": (
        "身份与协作",
        "凭据认证的会话存储（令牌摘要、空闲/绝对过期、停用撤销）；开发身份模式不使用。",
    ),
    "auth_login_limits": (
        "身份与协作",
        "跨 worker 共享的登录限速窗口（迁移 005）；成功与失败均计数，超限返回 429。",
    ),
    "schema_migrations": (
        "一致性与审计",
        "迁移账本（版本 + SHA-256 校验和 + 应用时间）；迁移器据此幂等并拒绝未知库。",
    ),
}

TYPE_ALIASES = {
    "character varying": "varchar",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "date": "date",
    "character": "char",
}

TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

COLUMN_SQL = """
SELECT column_name, data_type, udt_name, character_maximum_length,
       numeric_precision, numeric_scale, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema='public' AND table_name=%s
ORDER BY ordinal_position
"""

CONSTRAINT_SQL = """
SELECT conname, contype, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE conrelid=( 'public.' || %s )::regclass
ORDER BY contype, conname
"""

INDEX_SQL = """
SELECT indexname, indexdef FROM pg_indexes
WHERE schemaname='public' AND tablename=%s
ORDER BY indexname
"""

CONSTRAINT_KINDS = {"p": "primary_key", "f": "foreign_key", "u": "unique", "c": "check"}


def format_type(row):
    if row["data_type"] == "USER-DEFINED":
        return row["udt_name"]
    base = TYPE_ALIASES.get(row["data_type"], row["data_type"])
    if row["character_maximum_length"]:
        return f"{base}({row['character_maximum_length']})"
    return base


def export(database):
    with database.transaction() as connection:
        version_row = connection.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
        tables = [
            row["table_name"]
            for row in connection.execute(
                "SELECT c.relname AS table_name FROM pg_class c"
                " JOIN pg_namespace n ON n.oid=c.relnamespace"
                " WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname"
            ).fetchall()
        ]
        export_data = []
        for table in tables:
            if not TABLE_RE.match(table):
                raise RuntimeError(f"Unexpected table name in catalog: {table!r}")
            group, description = GROUPS.get(table, ("其他", ""))
            columns = [
                {
                    "name": row["column_name"],
                    "type": format_type(row),
                    "nullable": row["is_nullable"] == "YES",
                    "default": row["column_default"],
                }
                for row in connection.execute(COLUMN_SQL, (table,)).fetchall()
            ]
            constraints = [
                {
                    "kind": CONSTRAINT_KINDS.get(row["contype"], row["contype"]),
                    "name": row["conname"],
                    "definition": row["definition"],
                }
                for row in connection.execute(CONSTRAINT_SQL, (table,)).fetchall()
            ]
            indexes = [
                {"name": row["indexname"], "definition": row["indexdef"]}
                for row in connection.execute(INDEX_SQL, (table,)).fetchall()
            ]
            row_count = connection.execute(
                f'SELECT count(*) AS n FROM "public"."{table}"'  # noqa: S608 - name from catalog, regex-checked
            ).fetchone()["n"]
            export_data.append(
                {
                    "name": table,
                    "group": group,
                    "description": description,
                    "row_count": row_count,
                    "columns": columns,
                    "constraints": constraints,
                    "indexes": indexes,
                }
            )
    return {
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "schema_version": version_row["version"] if version_row else None,
        "table_count": len(tables),
        "source": "PostgreSQL pg_catalog / information_schema 导出快照；行数为导出时刻值，非实时",
        "tables": export_data,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed snapshot matches the live schema facts (row counts excluded)",
    )
    options = parser.parse_args()
    database = PostgresDatabase(ConnectionConfig.from_env(os.environ))
    try:
        data = export(database)
    finally:
        database.close()
    if options.check:
        committed = json.loads(OUTPUT.read_text(encoding="utf-8"))

        def strip(payload):
            return json.dumps(
                {
                    **payload,
                    "exported_at": None,
                    "tables": [{**table, "row_count": None} for table in payload["tables"]],
                },
                ensure_ascii=False,
                sort_keys=True,
            )

        if strip(committed) != strip(data):
            print("data dictionary snapshot is stale; re-run without --check", file=sys.stderr)
            raise SystemExit(1)
        print(f"data dictionary check: ok ({data['table_count']} tables, schema v{data['schema_version']})")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"data dictionary exported: {OUTPUT.relative_to(ROOT)} "
        f"({data['table_count']} tables, schema v{data['schema_version']})"
    )


if __name__ == "__main__":
    main()
