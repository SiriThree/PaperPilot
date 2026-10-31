"""Compare real local query vectors, process reuse/recovery and batch independence."""

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.local_embeddings import LocalEmbeddings  # noqa: E402
from app.ai.settings import AISettings  # noqa: E402
from seed_real_literature import save_state  # noqa: E402


async def verify(args):
    model = json.loads(args.model_manifest.read_text())
    config = AISettings(
        base_url="https://unused.invalid/v1",
        api_key="local-verification-no-chat",
        chat_model="unused",
        embedding_model=model["model"],
        embedding_dimensions=model["dimensions"],
        embedding_backend="fastembed",
        local_model_dir=Path(model["model_dir"]),
        embedding_revision=Path(model["model_dir"]).name,
        timeout_seconds=90,
    )
    embedder = LocalEmbeddings(config)
    queries = [q for q in json.loads(args.workload.read_text())["queries"] if q["mode"] != "keyword"]
    results = []
    await embedder.astart()
    try:
        first_process = embedder.query_worker._process
        first_reference = None
        for q in queries:
            started = time.perf_counter()
            reference = (await embedder.embed([q["query"]]))[0]
            oneshot_seconds = time.perf_counter() - started
            started = time.perf_counter()
            resident = await embedder.embed_query(q["query"])
            resident_seconds = time.perf_counter() - started
            error = max(abs(a - b) for a, b in zip(reference, resident, strict=True))
            if error > 1e-7:
                raise ValueError("Resident query vector differs from the one-shot reference")
            if first_reference is None:
                first_reference = reference
            assert embedder.query_worker._process is first_process
            results.append(
                {
                    "id": q["id"],
                    "max_absolute_error": error,
                    "identical_floats": reference == resident,
                    "oneshot_seconds": round(oneshot_seconds, 6),
                    "resident_seconds": round(resident_seconds, 6),
                }
            )
            print(json.dumps(results[-1]), flush=True)
        # The separate, original batch path does not replace or queue into the query worker.
        batch = await embedder.embed([queries[0]["query"], "Independent document embedding probe."])
        assert len(batch) == 2 and embedder.query_worker._process is first_process
        first_process.kill()
        await first_process.wait()
        recovered = await embedder.embed_query(queries[0]["query"])
        second_process = embedder.query_worker._process
        assert second_process is not first_process
        assert max(abs(a - b) for a, b in zip(first_reference, recovered, strict=True)) <= 1e-7
        await embedder.aclose()
        assert second_process.returncode is not None
        result = {
            "checked_at": datetime.now(UTC).isoformat(),
            "model_manifest_sha256": hashlib.sha256(args.model_manifest.read_bytes()).hexdigest(),
            "workload_sha256": hashlib.sha256(args.workload.read_bytes()).hexdigest(),
            "query_count": len(results),
            "tolerance": 1e-7,
            "samples": results,
            "one_worker_reused_for_all_queries": True,
            "batch_path_independent": True,
            "forced_crash_recovered": True,
            "shutdown_reaped": True,
            "notes": [
                "Actual prepared BGE model; no model double or remote chat calls.",
                "Per-vector timings exclude HTTP, authorization and database retrieval.",
                "No paper processing or stored embeddings were changed.",
            ],
        }
        save_state(args.output, result)
    finally:
        await embedder.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--workload", type=Path, default=ROOT / "demo/rag-300/workload.json")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "demo/retrieval-acceleration/embedding-check.json"
    )
    asyncio.run(verify(parser.parse_args()))


if __name__ == "__main__":
    main()
