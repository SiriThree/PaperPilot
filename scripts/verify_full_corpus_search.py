"""Read-only real-corpus retrieval verification, with separate HTTP timing samples.

Needs an already imported corpus and running local application. No ingestion, model
downloads, chat calls or relevance labels. Full source text stays in memory only.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import heapq
import json
import math
from pathlib import Path
import statistics
import sys
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

from app.adapters.ports import Context  # noqa: E402
from app.ai.retrieval import bm25, bm25_topk, rank  # noqa: E402
from app.data.adapter import PostgresData  # noqa: E402
from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402
from app.data.settings import DataSettings  # noqa: E402
from app.indexing import EmbeddingModel  # noqa: E402
from app.schemas import SearchRequest  # noqa: E402
from seed_real_literature import authenticate, checked, save_state  # noqa: E402


def source_rows(connection, project_id):
    with psycopg.connect(**connection, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        return conn.execute(
            "SELECT pp.paper_id,p.title,rs.chunk_id,rs.page_number,rs.excerpt,rs.synthetic"
            " FROM project_papers pp JOIN papers p USING(project_id,metadata_id)"
            " JOIN result_sources rs ON rs.project_id=pp.project_id AND rs.paper_id=pp.paper_id"
            " AND rs.task_id=pp.active_process_task_id AND rs.scope='root'"
            " WHERE pp.project_id=%s AND pp.removed_at IS NULL AND pp.status='searchable'"
            " ORDER BY pp.paper_id,rs.page_number,rs.chunk_id",
            (project_id,),
        ).fetchall()


def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()


def run(args):
    cfg = json.loads(args.database_config.read_text())
    connection = {k: cfg[k] for k in ("host", "port", "user", "dbname")}
    connection["password"] = Path(cfg["password_file"]).read_text().strip()
    state = json.loads(args.import_state.read_text())
    project_id = state["projects"][args.project_key]["project_id"]
    catalog = json.loads(args.catalog.read_text())
    workload = json.loads(args.workload.read_text())["queries"]
    if not workload or len({q["id"] for q in workload}) != len(workload):
        raise ValueError("Expected a nonempty workload with unique query IDs")
    rows = source_rows(connection, project_id)
    if len(rows) != sum(p["chunk_count"] for p in catalog["papers"]) or any(r["synthetic"] for r in rows):
        raise ValueError("Active real sources differ from the frozen corpus")
    if len({r["paper_id"] for r in rows}) != catalog["target_count"]:
        raise ValueError("Active paper count differs from the frozen corpus")
    canonical = {str(r["chunk_id"]): r for r in rows}
    keys = [(r["paper_id"], r["page_number"], r["chunk_id"]) for r in rows]
    documents = [r["title"] + "\n" + r["excerpt"] for r in rows]
    source_digest = digest(rows)
    database = PostgresDatabase(ConnectionConfig(**connection))
    data = PostgresData(database, DataSettings())
    ctx = Context(data.user_by_name(args.username).user_id, UUID(project_id), uuid4())
    try:
        keyword_query = next(q for q in workload if q["mode"] == "keyword")
        expected_scores = bm25(keyword_query["query"], documents)
        expected = [(keys[i], expected_scores[i]) for i in rank(expected_scores)[:50]]
        with data.retrieval_snapshot(
            ctx, SearchRequest(query=keyword_query["query"]), timeout_seconds=90
        ) as snap:
            actual = bm25_topk(keyword_query["query"], snap.documents, 50)
        if actual != expected:
            raise ValueError("Streaming BM25 differs from the original full-corpus reference")
        # Use a stored real vector as a reproducible exact-search reference probe.
        # HTTP checks below independently embed all semantic queries with the actual BGE model.
        with database.transaction(snapshot=True) as conn:
            model_row = conn.execute(
                "SELECT m.* FROM embedding_models m JOIN process_indexes pi USING(project_id,model_id)"
                " JOIN project_papers pp ON pp.active_process_task_id=pi.task_id"
                " AND pp.project_id=pi.project_id WHERE pp.project_id=%s LIMIT 1",
                (project_id,),
            ).fetchone()
            model = EmbeddingModel(
                **{
                    k: model_row[k]
                    for k in (
                        "provider",
                        "model_name",
                        "revision",
                        "dimensions",
                        "distance_metric",
                    )
                }
            )
            vector = json.loads(
                conn.execute(
                    "SELECT embedding::text AS v FROM chunk_embeddings WHERE project_id=%s AND chunk_id=%s"
                    " AND model_id=%s",
                    (project_id, keys[0][2], model_row["model_id"]),
                ).fetchone()["v"]
            )
            norm = math.hypot(*vector)
            vector = [v / norm for v in vector]
            with conn.cursor(name="cosine_reference") as cursor:
                cursor.itersize = 256
                cursor.execute(
                    "SELECT chunk_id,embedding::text AS v FROM chunk_embeddings"
                    " WHERE project_id=%s AND model_id=%s",
                    (project_id, model_row["model_id"]),
                )

                def reference():
                    for row in cursor:
                        source = canonical.get(str(row["chunk_id"]))
                        if source is None:  # Historical chunks must not become candidates.
                            continue
                        v = json.loads(row["v"])
                        score = sum(a * b for a, b in zip(vector, v, strict=True)) / math.hypot(*v)
                        if score > 0.2:
                            yield -score, (source["paper_id"], source["page_number"], source["chunk_id"])

                reference_top = [(key, -score) for score, key in heapq.nsmallest(50, reference())]
        with data.retrieval_snapshot(
            ctx, SearchRequest(query="cosine reference"), timeout_seconds=90
        ) as snap:
            sql_top = snap.nearest(model, vector, 0.2, 50)
        if [k for k, _ in sql_top] != [k for k, _ in reference_top] or any(
            abs(a[1] - b[1]) > 1e-6 for a, b in zip(sql_top, reference_top, strict=True)
        ):
            raise ValueError("SQL exact cosine ranking differs from the streamed Python reference")
    finally:
        database.close()

    def request(client, query):
        start = time.perf_counter()
        response = client.post(
            f"/api/v1/projects/{project_id}/search", json={k: v for k, v in query.items() if k != "id"}
        )
        elapsed = time.perf_counter() - start
        payload = response.json()
        valid = response.status_code == 200
        hits = payload.get("items", [])
        if valid:
            valid = not payload.get("mock", True) and payload.get("mode") == query["mode"]
            valid = valid and len(hits) <= query["limit"]
            for number, hit in enumerate(hits, 1):
                source = hit["source"]
                row = canonical.get(source["chunk_id"])
                valid = (
                    valid
                    and row is not None
                    and (
                        hit["paper"]["project_id"] == project_id
                        and hit["paper"]["paper_id"] == source["paper_id"] == str(row["paper_id"])
                        and source["page_number"] == row["page_number"]
                        and source["excerpt"] == row["excerpt"]
                        and source["synthetic"] is False
                        and source["citation_id"] == str(number)
                    )
                )
        return {
            "id": query["id"],
            "mode": query["mode"],
            "http_status": response.status_code,
            "code": payload.get("code"),
            "seconds": round(elapsed, 6),
            "hits": len(hits),
            "valid": bool(valid),
        }

    baseline = []
    password = args.password_file.read_text().strip()
    if args.baseline_api:
        with httpx.Client(base_url=args.baseline_api, timeout=95, trust_env=False) as client:
            authenticate(client, args.username, password, args.baseline_origin)
            baseline = [
                request(client, next(q for q in workload if q["mode"] == mode))
                for mode in ("keyword", "semantic", "hybrid")
            ]
    with httpx.Client(base_url=args.api_base, timeout=95, trust_env=False) as client:
        authenticate(client, args.username, password, args.origin)
        papers = checked(client.get(f"/api/v1/projects/{project_id}/papers"))["items"]
        if len(papers) != catalog["target_count"]:
            raise ValueError("HTTP project differs from the verified corpus")
        samples = []
        for query in workload:
            sample = request(client, query)
            samples.append(sample)
            print(json.dumps(sample), flush=True)
        probes = [
            next(q for q in workload if q["mode"] == mode) for mode in ("keyword", "semantic", "hybrid")
        ] * 2
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            concurrent = list(pool.map(lambda q: request(client, q), probes))
    after_digest = digest(source_rows(connection, project_id))
    if after_digest != source_digest:
        raise ValueError("Active corpus changed during verification; do not mix the results")
    files = [
        "backend/app/ai/adapter.py",
        "backend/app/ai/retrieval.py",
        "backend/app/data/retrieval.py",
        "backend/app/data/adapter.py",
        "backend/app/data/database.py",
    ]
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        "workload_sha256": hashlib.sha256(args.workload.read_bytes()).hexdigest(),
        "paper_count": len(papers),
        "active_sources": len(rows),
        "synthetic_sources": 0,
        "source_snapshot_sha256": source_digest,
        "source_snapshot_after_sha256": after_digest,
        "corpus_unchanged": True,
        "implementation_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files
        },
        "embedding_model": model.model_dump(),
        "bm25_top50_matches_original_full_corpus": True,
        "cosine_top50_matches_python_reference": True,
        "cosine_score_tolerance": 1e-6,
        "baseline": baseline,
        "sequential": samples,
        "concurrent_probe": {"concurrency": args.concurrency, "samples": concurrent},
        "timing_summary": {
            mode: {
                "n": len(values),
                "median_seconds": round(statistics.median(values), 6),
                "min_seconds": min(values),
                "max_seconds": max(values),
            }
            for mode in ("keyword", "semantic", "hybrid")
            if (values := [r["seconds"] for r in samples if r["mode"] == mode and r["valid"]])
        },
        "notes": [
            "Single local run; sequential samples include HTTP, auth checks and model startup.",
            "Six concurrent probes are a smoke test, not a sustained load or capacity benchmark.",
            "No relevance labels, recall, nDCG or answer-quality claims.",
            "Baseline errors are failures, never successful latency/speedup samples.",
        ],
    }
    save_state(args.output, result)
    if not all(s["valid"] for s in samples + concurrent):
        raise ValueError("Some searches failed; inspect the saved report")
    print(
        json.dumps(
            {
                "successful_queries": len(samples),
                "successful_concurrent_probes": len(concurrent),
                "timings": result["timing_summary"],
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--import-state", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "demo/chunking-v2/catalog.json")
    parser.add_argument("--workload", type=Path, default=ROOT / "demo/rag-300/workload.json")
    parser.add_argument("--project-key", default="rag300")
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--baseline-api")
    parser.add_argument("--baseline-origin")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "demo/retrieval-v2/verification.json")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 8 or (args.baseline_api and not args.baseline_origin):
        parser.error("Use concurrency 1..8 and specify the baseline origin with its API")
    run(args)


if __name__ == "__main__":
    main()
