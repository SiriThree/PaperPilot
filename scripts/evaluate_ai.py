"""Real PDF + real retrieval evaluation. Network calls require explicit --live-models.

No download, business database, model doubles or hidden credential discovery.
"""

import argparse
import asyncio
import hashlib
import json
import platform
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from time import monotonic

from app.adapters.ports import Context, FileReference
from app.ai.adapter import RealAI
from app.ai.client import ModelClient
from app.ai.files import LocalPDFReader
from app.ai.settings import AISettings
from app.errors import AppError
from app.schemas import Paper, QuestionRequest, SearchRequest

ROOT = Path(__file__).resolve().parents[1]


class PacedClient(ModelClient):
    """Evaluator-only pacing for institutional request quotas, never automatic retries."""

    def __init__(self, settings, interval):
        super().__init__(settings)
        self.interval, self.last = interval, 0

    async def _post(self, endpoint, payload):
        await asyncio.sleep(max(0, self.last + self.interval - monotonic()))
        self.last = monotonic()
        return await super()._post(endpoint, payload)


class EvaluationCorpus:
    """Ephemeral read store, populated only by the evaluator from the supplied PDF.

    Intentionally has no task/report/SQL methods; exercises C without a business DB.
    """

    def __init__(self, ctx, paper):
        self.ctx, self.record, self.chunks = ctx, paper, []

    def role(self, user_id, project_id):
        return "owner" if (user_id, project_id) == (self.ctx.user_id, self.ctx.project_id) else None

    def paper(self, ctx, paper_id):
        if ctx != self.ctx or paper_id != self.record.paper_id:
            raise ValueError("Evaluation scope mismatch")
        return self.record.model_copy(deep=True)

    def papers(self, ctx, year_from=None, year_to=None):
        paper = self.paper(ctx, self.record.paper_id)
        return (
            [paper]
            if ((year_from is None or paper.year >= year_from) and (year_to is None or paper.year <= year_to))
            else []
        )

    def sources(self, ctx, paper_id):
        self.paper(ctx, paper_id)
        return [s.model_copy(deep=True) for s in self.chunks]


async def evaluate(args):
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    digest = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    if digest != dataset["sha256"]:
        raise ValueError("PDF SHA256 differs from the annotated dataset; supply the exact documented version")
    settings = (
        AISettings.from_env()
        if args.live_models
        else AISettings(
            base_url="https://offline.invalid/v1",
            api_key="unused-offline-keyword-evaluation",
            chat_model="unused",
            embedding_model="unused",
            embedding_dimensions=1,
        )
    )
    project_id = uuid5(NAMESPACE_URL, "paperpilot/evaluation/project")
    ctx = Context(uuid5(project_id, "user"), project_id, uuid5(project_id, "request"))
    paper_id = uuid5(project_id, dataset["paper_id"])
    paper = Paper(
        paper_id=paper_id,
        project_id=project_id,
        title=dataset["title"],
        year=dataset["year"],
        status="searchable",
        mock=False,
    )
    corpus = EvaluationCorpus(ctx, paper)
    with tempfile.TemporaryDirectory(prefix="paperpilot-c-eval-") as directory:
        root = Path(directory)
        file = FileReference(f"{paper_id}.pdf", paper_id, project_id)
        shutil.copyfile(args.pdf, root / file.storage_key)
        ai = RealAI(
            corpus, settings, reader=LocalPDFReader(root), client=PacedClient(settings, args.request_interval)
        )
        parsed = await ai.process(ctx, uuid5(project_id, "parse-task"), file)
        corpus.chunks = parsed.sources
        result = {
            "dataset": dataset["paper_id"],
            "source_url": dataset["source_url"],
            "pdf_sha256": digest,
            "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
            "python": platform.python_version(),
            "pypdf": version("pypdf"),
            "live_models": args.live_models,
            "generation_cases_requested": args.generation_cases,
            "models": {
                "chat": settings.chat_model,
                "embedding": settings.embedding_model,
                "dimensions": settings.embedding_dimensions,
                "embedding_backend": settings.embedding_backend,
            }
            if args.live_models
            else None,
            "parsing": {
                "chunk_count": len(parsed.sources),
                "pages_with_text": len({s.page_number for s in parsed.sources}),
                "max_excerpt_chars": max(len(s.excerpt) for s in parsed.sources),
                "unique_chunk_ids": len({s.chunk_id for s in parsed.sources}) == len(parsed.sources),
            },
            "k": args.k,
            "modes": {},
            "limitations": [
                "One public paper and eight manually labeled development queries; no generalization claim.",
                "Page labels may omit other relevant pages; ranking is evaluated at chunk granularity.",
                "Citation validity does not establish semantic entailment or factual correctness.",
                "Semantic, hybrid and generation need explicit live configuration; not replaced by doubles.",
            ],
        }
        if result["parsing"]["pages_with_text"] != dataset["pages"]:
            raise ValueError("Unexpected PDF page coverage")
        review = []
        for mode in args.modes:
            cases = []
            for query_number, query in enumerate(dataset["queries"]):
                found = await ai.search(ctx, SearchRequest(query=query["query"], mode=mode, limit=args.k))
                pages = [hit.source.page_number for hit in found.items]
                gold = set(query["relevant_pages"])
                positions = [i for i, page in enumerate(pages, 1) if page in gold]
                case = {
                    "id": query["id"],
                    "retrieved_pages": pages,
                    "relevant_pages": sorted(gold),
                    "reciprocal_rank": 1 / positions[0] if positions else 0,
                    "page_recall": len(set(pages) & gold) / len(gold) if gold else None,
                    "hit": bool(positions),
                    "correct_abstention": not pages if not gold else None,
                }
                if args.live_models and mode == args.modes[0] and query_number < args.generation_cases:
                    sources = [hit.source for hit in found.items]
                    question = QuestionRequest(question=query["query"])
                    try:
                        answer = await ai.answer(ctx, question, sources)
                        draft = await ai.draft(ctx, question, sources)
                        evidence = await ai.extract(ctx, paper_id, sources)
                        case["generation"] = {
                            "status": "succeeded",
                            "insufficient_evidence": answer.insufficient_evidence,
                            "answer_citations": len(answer.citation_ids),
                            "human_review_required": True,
                        }
                        review.append(
                            {
                                "query": query,
                                "answer": answer.model_dump(mode="json"),
                                "draft": draft.model_dump(mode="json"),
                                "evidence": evidence.model_dump(mode="json"),
                            }
                        )
                    except AppError as exc:
                        case["generation"] = {"status": "failed", "code": exc.code}
                cases.append(case)
            labeled = [c for c in cases if c["relevant_pages"]]
            result["modes"][mode] = {
                "hit_rate_at_k": sum(c["hit"] for c in labeled) / len(labeled),
                "mean_reciprocal_rank_at_k": sum(c["reciprocal_rank"] for c in labeled) / len(labeled),
                "mean_page_recall_at_k": sum(c["page_recall"] for c in labeled) / len(labeled),
                "cases": cases,
            }
        if args.review_output:
            args.review_output.parent.mkdir(parents=True, exist_ok=True)
            args.review_output.write_text(
                json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf", type=Path, required=True, help="Local arXiv 1706.03762v7 PDF (not downloaded)"
    )
    parser.add_argument("--dataset", type=Path, default=ROOT / "docs/ai-evaluation/dataset.json")
    parser.add_argument("--modes", choices=["keyword", "semantic", "hybrid"], nargs="+", default=["keyword"])
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument(
        "--live-models", action="store_true", help="Explicitly permit configured external model calls"
    )
    parser.add_argument("--review-output", type=Path, help="Optional private generated text for human review")
    parser.add_argument(
        "--generation-cases",
        type=int,
        default=0,
        help="Evaluate generation on first N queries (three calls each)",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=7,
        help="Minimum seconds between provider calls (SJTU quota)",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.k <= 50:
        parser.error("--k must be between 1 and 50")
    if args.generation_cases < 0 or args.request_interval < 0:
        parser.error("Generation count and request interval must be nonnegative")
    if args.generation_cases and not args.live_models:
        parser.error("Generation evaluation requires --live-models")
    if any(mode != "keyword" for mode in args.modes) and not args.live_models:
        parser.error(
            "Semantic/hybrid evaluation requires --live-models and explicit PAPERPILOT_AI configuration"
        )
    try:
        result = asyncio.run(evaluate(args))
    except Exception:
        # Do not print vendor exception strings, keys or paper text to CI logs.
        parser.exit(
            1, "Evaluation failed: check the local PDF hash, AI configuration and service availability.\n"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "parsing": result["parsing"],
                "metrics": {
                    mode: {key: value for key, value in report.items() if key != "cases"}
                    for mode, report in result["modes"].items()
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
