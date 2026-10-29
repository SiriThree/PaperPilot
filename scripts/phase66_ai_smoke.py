"""Reproduce the Phase 6.6 synthetic PDF smoke check with one live chat call.

Explicit opt-in required. Loads AI settings from the process environment, not .env.
This uses an ephemeral in-memory corpus and does not test PostgreSQL or model quality.
"""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests"), str(ROOT / "scripts")]

from ai_contracts.support import pdf_bytes  # noqa: E402
from app.adapters.ports import Context, FileReference  # noqa: E402
from app.ai.adapter import RealAI  # noqa: E402
from app.ai.files import LocalPDFReader  # noqa: E402
from app.ai.settings import AISettings  # noqa: E402
from app.schemas import Paper, QuestionRequest, SearchRequest  # noqa: E402
from evaluate_ai import EvaluationCorpus  # noqa: E402


async def run():
    settings = AISettings.from_env()
    ctx = Context(uuid4(), uuid4(), uuid4())
    paper_id = uuid4()
    paper = Paper(
        paper_id=paper_id,
        project_id=ctx.project_id,
        title="Fictional Evidence Search Experiment",
        year=2024,
        status="searchable",
        mock=False,
    )
    corpus = EvaluationCorpus(ctx, paper)
    with tempfile.TemporaryDirectory(prefix="paperpilot-phase66-c-") as directory:
        file = FileReference(f"{paper_id}.pdf", paper_id, ctx.project_id)
        (Path(directory) / file.storage_key).write_bytes(
            pdf_bytes(
                [
                    "FICTIONAL EXAMPLE ONLY. The simulated study compares keyword retrieval and vector retrieval. "
                    "The simulated dataset has twelve documents. It is not real scientific evidence."
                ]
            )
        )
        ai = RealAI(corpus, settings, reader=LocalPDFReader(Path(directory)))
        parsed = await ai.process(ctx, uuid4(), file)
        corpus.chunks = parsed.sources
        print("PDF chunks:", len(parsed.sources), "pages:", [s.page_number for s in parsed.sources])
        for mode in ("keyword", "semantic", "hybrid"):
            found = await ai.search(
                ctx, SearchRequest(query="simulated dataset twelve documents", mode=mode, limit=3)
            )
            print(
                "Search:",
                mode,
                "hits:",
                len(found.items),
                "pages:",
                [h.source.page_number for h in found.items],
            )
        answer = await ai.answer(
            ctx,
            QuestionRequest(question="How many documents are in the fictional simulated dataset?"),
            corpus.chunks,
        )
        print("Chat calls: 1; insufficient:", answer.insufficient_evidence, "citations:", answer.citation_ids)
        print("Pages:", [s.page_number for s in answer.sources], "answer:", answer.answer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-model", action="store_true", help="Allow one external chat request")
    args = parser.parse_args()
    if not args.live_model:
        parser.error("--live-model is required; no requests were made")
    try:
        asyncio.run(run())
    except Exception as exc:
        # Do not print provider errors, credentials, or untrusted exception text.
        print("Smoke check failed:", getattr(exc, "code", type(exc).__name__), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
