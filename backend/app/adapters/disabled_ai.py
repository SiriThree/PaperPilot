"""Explicitly unavailable AI; never fabricates model results."""

from app.errors import AppError


class DisabledAI:
    async def process(self, ctx, task_id, file):
        raise AppError(503, "AI_NOT_CONFIGURED", "当前部署未配置 AI")

    async def search(self, ctx, query):
        raise AppError(503, "AI_NOT_CONFIGURED", "当前部署未配置 AI")

    async def answer(self, ctx, question, sources):
        raise AppError(503, "AI_NOT_CONFIGURED", "当前部署未配置 AI")

    async def extract(self, ctx, paper_id, sources):
        raise AppError(503, "AI_NOT_CONFIGURED", "当前部署未配置 AI")

    async def draft(self, ctx, question, sources):
        raise AppError(503, "AI_NOT_CONFIGURED", "当前部署未配置 AI")
