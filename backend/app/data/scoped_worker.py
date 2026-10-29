"""Explicit test-only worker bridge using a single actor/project DB identity."""

from app.adapters.mock_ai import MockAI
from app.adapters.ports import Adapters
from app.data.scoped_business import ScopedResultWorker
from app.errors import AppError
from app.services import ResearchService


class ScopedWorkerData:
    """Expose only worker operations; the separately supplied audit sink is explicit."""

    def __init__(self, worker, audit_sink):
        if not isinstance(worker, ScopedResultWorker) or not callable(audit_sink):
            raise ValueError("Invalid isolated worker configuration")
        self.worker = worker
        self.audit_sink = audit_sink

    def role(self, user_id, project_id):
        cfg = self.worker.config
        if user_id != cfg.user_id or project_id != cfg.project_id:
            return None
        return self.worker.role(user_id, project_id)

    def papers(self, ctx, year_from=None, year_to=None):
        return self.worker.papers(ctx, year_from, year_to)

    def paper(self, ctx, paper_id):
        return self.worker.paper(ctx, paper_id)

    def sources(self, ctx, paper_id):
        return self.worker.sources(ctx, paper_id)

    def claim_job(self):
        return self.worker.claim_job()

    def complete_job(self, job, result):
        return self.worker.complete_job(job, result)

    def fail_job(self, job, code, message):
        return self.worker.fail_job(job, code, message)

    def append_audit(self, event):
        cfg = self.worker.config
        if event.actor_id != cfg.user_id or event.project_id != cfg.project_id:
            raise AppError(404, "RESOURCE_NOT_FOUND", "资源不存在或不可访问")
        self.audit_sink(event)


def isolated_worker_service(worker, audit_sink, *, job_timeout_seconds):
    """Use an explicit Mock AI so the pilot cannot imply real AI processing."""
    data = ScopedWorkerData(worker, audit_sink)
    return ResearchService(
        Adapters(data=data, ai=MockAI(data), security=None), job_timeout_seconds=job_timeout_seconds
    )
