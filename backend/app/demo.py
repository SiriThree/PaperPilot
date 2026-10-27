"""Explicit, in-memory demo entry point. Never seeds an A-owned data provider."""

from contextlib import asynccontextmanager
from hashlib import sha256
import logging

from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context, FileReference
from app.config import Settings
from app.main import create_app
from app.schemas import (
    EvidenceField,
    EvidenceResult,
    ProcessResult,
    Project,
    QuestionRequest,
    ReportSave,
    Source,
)

DEMO_PROJECT_ID = seed_id("demo-v1/project")
DEMO_TITLE = "科研证据追溯演示"
DEMO_QUESTION = "检索增强生成如何建立可追溯的科研证据链？"
DEMO_REPORT_TITLE = "科研证据追溯综述（合成演示）"

# All titles, excerpts and findings below are invented fixtures, not published literature.
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


def context(action):
    return Context(seed_id("alice"), DEMO_PROJECT_ID, seed_id(f"demo-v1/request/{action}"))


async def seed_demo(data, service):
    if type(data) is not MockData or DEMO_PROJECT_ID in data.project_records:
        raise RuntimeError(
            "Demo requires a fresh, explicit Mock store; existing data will not be overwritten"
        )
    # Fixed fixture identities are confined to this demo composition, never the business services.
    data.project_records[DEMO_PROJECT_ID] = Project(
        project_id=DEMO_PROJECT_ID,
        name=DEMO_TITLE,
        description="预置合成演示：6 篇文献、来源与证据、两版综述。非真实论文或研究结论。",
        owner_id=seed_id("alice"),
        role="owner",
        mock=True,
    )
    data.memberships[(DEMO_PROJECT_ID, seed_id("alice"))] = "owner"
    data.memberships[(DEMO_PROJECT_ID, seed_id("bob"))] = "member"
    sources = []
    failed_task_id = None
    for slug, title, year, first, second in PAPERS:
        ctx = context(f"process/{slug}")
        paper_id = seed_id(f"demo-v1/paper/{slug}")
        _, task = data.import_paper(
            ctx,
            title,
            year,
            sha256(f"synthetic-fixture-v1/{slug}".encode()).hexdigest(),
            # An opaque Mock reference only. No PDF file or parsed original is claimed to exist.
            FileReference(f"demo-fixtures/{slug}.pdf", paper_id, DEMO_PROJECT_ID),
            f"demo-v1-import-{slug}",
        )
        job = data.claim_job()
        if job is None or job.task.task_id != task.task_id:
            raise RuntimeError("Demo initialization requires an idle Mock worker")
        if slug == "failed":
            failed_task_id = task.task_id
            data.fail_job(job, "AI_UNAVAILABLE", "合成演示：模拟处理服务不可用，未获得正文；可明确重试。")
            service.audit(ctx, "process.failed", "process", execution_status="failed")
            continue
        items = [
            Source(
                citation_id=str(index),
                paper_id=paper_id,
                chunk_id=seed_id(f"demo-v1/chunk/{slug}/{index}"),
                page_number=page,
                excerpt=f"【合成演示片段，非真实论文】{excerpt}",
                synthetic=True,
            )
            for index, (page, excerpt) in enumerate((first, second), 1)
        ]
        data.complete_job(job, ProcessResult(paper_id=paper_id, sources=items, mock=True))
        service.audit(ctx, "process.completed", "process")
        sources.append(items[0].model_copy(update={"citation_id": str(len(sources) + 1)}))

    # Persist evidence with the same public create/claim/complete boundary as normal tasks.
    for index in (0, 2):
        source = sources[index]
        ctx = context(f"evidence/{index}")
        data.create_task(
            ctx,
            "evidence",
            {
                "question": QuestionRequest(question="证据提取", paper_ids=[source.paper_id]),
                "paper_ids": [source.paper_id],
            },
            f"demo-v1-evidence-{index}",
        )
        job = data.claim_job()
        data.complete_job(
            job,
            EvidenceResult(
                evidence_id=seed_id(f"demo-v1/evidence/{index}"),
                paper_id=source.paper_id,
                mock=True,
                research_question=EvidenceField(
                    text="【合成演示】如何把研究结论与可回看的来源关联？",
                    status="pending_verification",
                    sources=[source],
                ),
                method=EvidenceField(text=source.excerpt, status="pending_verification", sources=[source]),
                results=EvidenceField(text=None, status="insufficient_evidence", sources=[]),
                conclusion=EvidenceField(
                    text="【合成演示】保留来源定位，并由研究者逐条核对。",
                    status="pending_verification",
                    sources=[source],
                ),
                limitations=EvidenceField(text=None, status="not_mentioned", sources=[]),
            ),
        )
        service.audit(ctx, "evidence.completed", "evidence")

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
        (1, first_body, sources[:2]),
        (
            2,
            first_body + "\n\n## 第二版补充：证据缺口\n结构化证据需要区分有来源、证据不足和文献未提及 [3]。"
            "本版增加第三条引用快照；第一版仍保留原来的两条来源。",
            sources[:3],
        ),
    ):
        ctx = context(f"report/{number}")
        service.audit(ctx, "report.save_requested", "report", execution_status="pending")
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
            f"demo-v1-report-{number}",
        )
        service.audit(ctx, "report.saved", "report")

    # Produce audit entries by actually invoking B -> Mock SecurityPort, never fabricating real SQL events.
    for index, question in enumerate(("按年份统计论文数量", "不支持的问题", "模拟执行失败")):
        await service.secure_query(context(f"query/{index}"), question)
    return {"project_id": DEMO_PROJECT_ID, "failed_task_id": failed_task_id}


def create_demo_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    if settings.environment not in {"development", "test"} or any(
        mode != "mock" for mode in (settings.a_mode, settings.c_mode, settings.d_mode)
    ):
        raise RuntimeError(
            "Demo entry requires explicit development/test and all Mock modes; startup refused"
        )
    app = create_app(settings)
    base_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def demo_lifespan(app):
        if not getattr(app.state, "demo_seeded", False):
            app.state.demo_manifest = await seed_demo(app.state.adapters.data, app.state.service)
            app.state.demo_seeded = True
            logging.getLogger("paperpilot").warning(
                "DEMO synthetic_dataset=v1 project_id=%s failed_task_id=%s; no real PDF/AI/SQL",
                DEMO_PROJECT_ID,
                app.state.demo_manifest["failed_task_id"],
            )
        async with base_lifespan(app):
            yield

    app.router.lifespan_context = demo_lifespan
    return app
