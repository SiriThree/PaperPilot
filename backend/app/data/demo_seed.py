"""Repeatable demo seed for the persistent development database.

Builds the default demo baseline through the real DataPort (``PostgresData``),
mirroring the Mock demo composition in ``app.demo``: one project with six
synthetic papers (five processed with curated sources, one failed task), two
evidence results and a two-version report with citation snapshots.

All identities are deterministic UUIDv5 values and every business write uses a
fixed idempotency key, so re-running the seed replays the recorded responses
instead of inserting duplicates; already-closed tasks are detected and skipped.
Process/evidence/report/audit writes only use public DataPort methods; the
project row itself is bootstrapped directly because ``create_project`` has no
replayable key yet (see the scoped replay candidate notes).

Run this before starting the application: a running worker would claim the
pending demo tasks and replace the curated content with Mock AI output.

    python scripts/manage_postgres.py seed-demo-project
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from app.adapters.data_rules import canonical
from app.adapters.ports import Context, FileReference
from app.data.adapter import PostgresData
from app.data.settings import DataSettings
from app.schemas import (
    AuditEvent,
    EvidenceField,
    EvidenceResult,
    ProcessResult,
    QuestionRequest,
    ReportSave,
    Source,
)

DEMO_SEED_VERSION = "v1"
DEMO_TITLE = "科研证据追溯演示"
DEMO_DESCRIPTION = (
    "预置合成演示：6 篇文献（5 成功、1 失败）、来源与证据、两版综述。"
    "由 seed-demo-project 可重复初始化，非真实论文或研究结论。"
)
DEMO_REPORT_TITLE = "科研证据追溯综述（合成演示）"
DEMO_QUESTION = "检索增强生成如何建立可追溯的科研证据链？"

# Deterministic identities; alice/bob must match database.seed_development_users.
ALICE_ID = uuid5(NAMESPACE_URL, "paperpilot/development/alice")
PROJECT_ID = uuid5(NAMESPACE_URL, f"paperpilot/demo-seed/{DEMO_SEED_VERSION}/project")

# Fixed audit timestamps: replays rebuild byte-identical events, so append_audit
# deduplicates them through its event fingerprint instead of raising a conflict.
AUDIT_BASE = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)

# Keep the fixture content in sync with app.demo.PAPERS so Mock and persistent
# demos show the same baseline. All titles and excerpts are invented fixtures.
PAPERS = (
    (
        "rag",
        "检索增强生成中的来源核验框架（合成演示）",
        2024,
        (3, "系统将回答中的引用标记关联到论文片段、页码和项目范围，供研究者逐条回看。"),
        (7, "来源定位不等于结论已经证实；摘录是否支持论断仍需人工核对。"),
    ),
    (
        "retrieval",
        "面向科研文献的组合检索流程（合成演示）",
        2023,
        (2, "关键词与语义检索分别提供候选片段，再按研究问题组织阅读范围。"),
        (5, "本示例未提供可复现实验样本，不能由流程描述推断检索准确率。"),
    ),
    (
        "evidence",
        "结构化证据提取与人工复核（合成演示）",
        2025,
        (4, "研究问题、方法、结果、结论和局限分别保留支撑片段；没有支撑时标记为证据不足。"),
        (8, "部分字段在材料中未提及，应保留空缺，而不是补写推测。"),
    ),
    (
        "versioning",
        "研究综述的版本与引用快照（合成演示）",
        2022,
        (3, "保存正文时同时保存引用快照，使旧版本仍能读取当时采用的来源。"),
        (6, "版本历史用于追踪编辑变化，不能替代对原始证据的核验。"),
    ),
    (
        "access",
        "多项目科研协作中的访问范围（合成演示）",
        2021,
        (2, "服务端根据登录会话和项目成员关系检查资源访问，资源编号本身不是授权凭证。"),
        (5, "统计查询需要独立网关约束；界面上的允许标记不能证明真实 SQL 已安全执行。"),
    ),
    (
        "failed",
        "扫描材料处理失败样例（合成演示）",
        2025,
        (1, "此占位样例没有可展示正文，用于演示失败状态。"),
        (1, "不应将失败样例纳入可检索来源。"),
    ),
)

EVIDENCE_INDEXES = (0, 2)


def _seed_id(name):
    return uuid5(NAMESPACE_URL, f"paperpilot/demo-seed/{DEMO_SEED_VERSION}/{name}")


def _context(action):
    return Context(ALICE_ID, PROJECT_ID, _seed_id(f"request/{action}"))


def _sources_for(slug, paper_id, first, second):
    return [
        Source(
            citation_id=str(index),
            paper_id=paper_id,
            chunk_id=_seed_id(f"chunk/{slug}/{index}"),
            page_number=page,
            excerpt=f"【合成演示片段，非真实论文】{excerpt}",
            synthetic=True,
        )
        for index, (page, excerpt) in enumerate((first, second), 1)
    ]


def _append_audit(data, ctx, action, execution_status, slot):
    """Deterministic per-call-site audit event.

    ``slot`` is a fixed number per call site (never a run-local counter):
    skipped stages on a re-run must not shift later events, or the same
    event_id would rebuild with a different fingerprint and raise a conflict.
    """
    data.append_audit(
        AuditEvent(
            event_id=_seed_id(f"audit/{action}/{ctx.request_id}"),
            timestamp=AUDIT_BASE + timedelta(seconds=slot),
            request_id=ctx.request_id,
            actor_id=ALICE_ID,
            project_id=PROJECT_ID,
            action=action,
            resource_type=action.split(".")[0],
            decision="allowed",
            execution_status=execution_status,
            duration_ms=0,
            mock=False,
        )
    )


def _bootstrap_project(database):
    """Insert the deterministic project row; additive on every re-run."""
    with database.transaction(write=True) as connection:
        inserted = connection.execute(
            "INSERT INTO projects(project_id, name, description, owner_id, created_at)"
            " VALUES(%s,%s,%s,%s,now()) ON CONFLICT (project_id) DO NOTHING",
            (PROJECT_ID, DEMO_TITLE, DEMO_DESCRIPTION, ALICE_ID),
        ).rowcount
        connection.execute(
            "INSERT INTO project_members(project_id, user_id, role, joined_at)"
            " VALUES(%s,%s,'owner',now()) ON CONFLICT DO NOTHING",
            (PROJECT_ID, ALICE_ID),
        )
        if inserted:
            connection.execute(
                "INSERT INTO outbox_events(event_id, project_id, actor_id, request_id, payload)"
                " VALUES(%s,%s,%s,%s,%s::jsonb)",
                (
                    _seed_id("outbox/create-project"),
                    PROJECT_ID,
                    ALICE_ID,
                    _seed_id("request/create-project"),
                    canonical(
                        {
                            "action": "create_project",
                            "resource_type": "project",
                            "resource_id": str(PROJECT_ID),
                            "decision": "allowed",
                            "execution_status": "succeeded",
                            "duration_ms": 0,
                            "mock": False,
                        }
                    ),
                ),
            )
    return bool(inserted)


def _live_task(data, ctx, task):
    """Idempotent replays return the creation-time snapshot; read the live row."""
    if task is None or task.status != "pending":
        return task
    return data.task(ctx, task.task_id)


def seed_demo_project(database, settings=None, *, log=None):
    """Seed the demo baseline; returns a manifest describing the result."""
    settings = settings or DataSettings.from_env()
    data = PostgresData(database, settings)
    database.seed_development_users()
    manifest = {
        "seed_version": DEMO_SEED_VERSION,
        "project_id": str(PROJECT_ID),
        "created_project": _bootstrap_project(database),
        "papers": {},
        "evidence": [],
        "report_versions": [],
        "failed_task_id": None,
    }

    data.add_member(_context("member/bob"), "bob")

    report_sources = []
    failed_task_id = None
    for paper_index, (slug, title, year, first, second) in enumerate(PAPERS):
        paper_id = _seed_id(f"paper/{slug}")
        ctx = _context(f"process/{slug}")
        digest = sha256(f"paperpilot-demo-seed-{DEMO_SEED_VERSION}/{slug}".encode()).hexdigest()
        _, task = data.import_paper(
            ctx,
            title,
            year,
            digest,
            FileReference(f"demo-seed/{DEMO_SEED_VERSION}/{slug}.pdf", paper_id, PROJECT_ID),
            f"demo-seed-import-{slug}",
        )
        if slug == "failed":
            failed_task_id = str(task.task_id)
        live = _live_task(data, ctx, task)
        if live.status == "pending":
            job = data.claim_job()
            if job is None or job.task.task_id != task.task_id:
                raise RuntimeError(
                    "claim_job did not return the expected demo task; stop the application"
                    " (its worker claims pending tasks) and re-run the seed"
                )
            if slug == "failed":
                data.fail_job(job, "AI_UNAVAILABLE", "合成演示：模拟处理服务不可用，未获得正文；可明确重试。")
                _append_audit(data, ctx, "process.failed", "failed", paper_index + 1)
            else:
                items = _sources_for(slug, paper_id, first, second)
                data.complete_job(job, ProcessResult(paper_id=paper_id, sources=items, mock=True))
                _append_audit(data, ctx, "process.completed", "completed", paper_index + 1)
            manifest["papers"][slug] = "seeded"
        else:
            manifest["papers"][slug] = f"already-{live.status}"
        if slug != "failed":
            first_source = _sources_for(slug, paper_id, first, second)[0]
            report_sources.append(
                first_source.model_copy(update={"citation_id": str(len(report_sources) + 1)})
            )

    for index in EVIDENCE_INDEXES:
        source = report_sources[index]
        ctx = _context(f"evidence/{index}")
        task = data.create_task(
            ctx,
            "evidence",
            {
                "question": QuestionRequest(question="证据提取", paper_ids=[source.paper_id]),
                "paper_ids": [source.paper_id],
            },
            f"demo-seed-evidence-{index}",
        )
        live = _live_task(data, ctx, task)
        if live.status == "pending":
            job = data.claim_job()
            if job is None or job.task.task_id != task.task_id:
                raise RuntimeError(
                    "claim_job did not return the expected demo evidence task; stop the"
                    " application and re-run the seed"
                )

            def field(text, status, sources):
                return EvidenceField(text=text, status=status, sources=sources)

            data.complete_job(
                job,
                EvidenceResult(
                    evidence_id=_seed_id(f"evidence/{index}"),
                    paper_id=source.paper_id,
                    mock=True,
                    research_question=field(
                        "【合成演示】如何把研究结论与可回看的来源关联？",
                        "pending_verification",
                        [source],
                    ),
                    method=field(source.excerpt, "pending_verification", [source]),
                    results=field(None, "insufficient_evidence", []),
                    conclusion=field(
                        "【合成演示】保留来源定位，并由研究者逐条核对。", "pending_verification", [source]
                    ),
                    limitations=field(None, "not_mentioned", []),
                ),
            )
            _append_audit(data, ctx, "evidence.completed", "completed", 20 + index)
            manifest["evidence"].append("seeded")
        else:
            manifest["evidence"].append(f"already-{live.status}")

    first_body = (
        f"# {DEMO_REPORT_TITLE}\n\n"
        "> 全文为合成演示资料，不对应真实论文，不可作为科研结论或实验结果。\n\n"
        "## 研究问题\n如何让科研问答中的论断回到可核对的文献片段？\n\n"
        "## 阅读线索\n来源核验需要同时保留论文、页码与片段标识 [1]。"
        "组合检索提供候选材料，但不能据此宣称准确率提升 [2]。\n\n"
        "## 局限\n当前材料没有真实实验数据，所有论断仍待核验。"
    )
    previous = None
    for number, body, references in (
        (1, first_body, report_sources[:2]),
        (
            2,
            first_body + "\n\n## 第二版补充：证据缺口\n结构化证据需要区分有来源、证据不足和文献未提及 [3]。"
            "本版增加第三条引用快照；第一版仍保留原来的两条来源。",
            report_sources[:3],
        ),
    ):
        ctx = _context(f"report/{number}")
        _append_audit(data, ctx, "report.save_requested", "pending", 30 + number)
        previous = data.save_report(
            ctx,
            ReportSave(
                title=DEMO_REPORT_TITLE,
                research_question=DEMO_QUESTION,
                body=body,
                sources=references,
                report_id=previous.report_id if previous else None,
                base_version_id=previous.version_id if previous else None,
            ),
            f"demo-seed-report-{number}",
        )
        _append_audit(data, ctx, "report.saved", "completed", 40 + number)
        manifest["report_versions"].append(
            {"number": previous.number, "version_id": str(previous.version_id)}
        )

    manifest["failed_task_id"] = failed_task_id

    if log is not None:
        log(f"demo seed: {manifest}")
    return manifest
