"""Verify local PDFs, uploaded copies and committed indexes, then freeze corpus metadata.

Read-only database checks use the existing business role. The exported summary
contains counts and model provenance, never credentials, PDFs or full text.
"""

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import shutil
import subprocess

import psycopg
from psycopg.rows import dict_row

from seed_real_literature import ROOT, load_catalog, save_state


def snapshot(args):
    catalog, papers = load_catalog(args.catalog)
    if catalog["status"] != "complete" or len(papers) != catalog["target_count"]:
        raise ValueError("Finish the catalog before freezing a snapshot")
    state = json.loads(args.import_state.read_text())
    project = state["projects"][catalog["projects"][0]["key"]]
    entries = project["papers"]
    if state.get("status") != "complete" or set(entries) != set(papers):
        raise ValueError("Import has not completed for the whole catalog")
    if len({p["arxiv_id"] for p in papers.values()}) != len(papers) or len(
        {p["sha256"] for p in papers.values()}
    ) != len(papers):
        raise ValueError("Duplicate paper identity or PDF content")
    config = json.loads(args.database_config.read_text())
    kwargs = {k: config[k] for k in ["host", "port", "user", "dbname"]}
    kwargs["password"] = Path(config["password_file"]).read_text().strip()
    with psycopg.connect(**kwargs, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        pid = project["project_id"]
        rows = conn.execute(
            "SELECT paper_id::text, digest, storage_key, status, index_state FROM project_papers WHERE project_id=%s AND removed_at IS NULL",
            (pid,),
        ).fetchall()
        stored = {r["paper_id"]: r for r in rows}
        if set(stored) != {e["paper_id"] for e in entries.values()}:
            raise ValueError("Database project differs from the imported manifest")
        for key, paper in papers.items():
            entry = entries[key]
            row = stored[entry["paper_id"]]
            if (
                entry["status"] != "succeeded"
                or row["status"] != "searchable"
                or row["index_state"] != "ready"
                or row["digest"] != paper["sha256"]
            ):
                raise ValueError(f"Paper/index not ready: {key}")
            for path in [args.pdf_dir / paper["filename"], args.upload_dir / row["storage_key"]]:
                data = path.read_bytes()
                if len(data) != paper["bytes"] or hashlib.sha256(data).hexdigest() != paper["sha256"]:
                    raise ValueError(f"PDF content changed: {key}")
            counts = conn.execute(
                "SELECT count(*) AS chunks, count(*) FILTER (WHERE synthetic) AS synthetic, count(DISTINCT page_number) AS pages FROM chunks WHERE project_id=%s AND paper_id=%s",
                (pid, entry["paper_id"]),
            ).fetchone()
            if (
                counts["chunks"] != paper["chunk_count"]
                or counts["synthetic"]
                or counts["pages"] != paper["pages_with_text"]
            ):
                raise ValueError(f"Stored chunks differ from parsed PDF: {key}")
        totals = conn.execute(
            "SELECT count(*) AS chunks, count(*) FILTER (WHERE synthetic) AS synthetic FROM chunks WHERE project_id=%s",
            (pid,),
        ).fetchone()
        vectors = conn.execute(
            "SELECT count(*) AS embeddings, min(vector_dims(embedding)) AS min_dimensions, max(vector_dims(embedding)) AS max_dimensions FROM chunk_embeddings WHERE project_id=%s",
            (pid,),
        ).fetchone()
        if totals["chunks"] != vectors["embeddings"] or totals["synthetic"]:
            raise ValueError("Embedding/source count mismatch")
        indexes = conn.execute(
            "SELECT count(*) AS count FROM process_indexes WHERE project_id=%s", (pid,)
        ).fetchone()["count"]
        tasks = conn.execute(
            "SELECT kind, status, count(*) AS count FROM tasks WHERE project_id=%s GROUP BY kind,status ORDER BY kind,status",
            (pid,),
        ).fetchall()
        attempts = conn.execute(
            "SELECT a.status,a.error_code,count(*) AS count FROM task_attempts a JOIN tasks t ON t.task_id=a.task_id WHERE t.project_id=%s GROUP BY a.status,a.error_code ORDER BY a.status,a.error_code",
            (pid,),
        ).fetchall()
        if indexes != len(papers) or tasks != [
            {"kind": "process", "status": "succeeded", "count": len(papers)}
        ]:
            raise ValueError("Unexpected processing tasks or missing index commits")
        models = conn.execute(
            "SELECT provider,model_name,revision,dimensions,distance_metric FROM embedding_models WHERE project_id=%s",
            (pid,),
        ).fetchall()
        if (
            len(models) != 1
            or models[0]["dimensions"] != vectors["min_dimensions"]
            or vectors["min_dimensions"] != vectors["max_dimensions"]
        ):
            raise ValueError("Model identity/dimensions differ across the corpus")
        sizes = conn.execute(
            "SELECT relname, pg_total_relation_size(relid) AS bytes FROM pg_catalog.pg_statio_user_tables WHERE schemaname='public' ORDER BY relname"
        ).fetchall()
        database_size = conn.execute("SELECT pg_database_size(current_database()) AS bytes").fetchone()[
            "bytes"
        ]
        schema = conn.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()["version"]
        server = conn.execute("SHOW server_version").fetchone()["server_version"]
        vector_version = conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname='vector'"
        ).fetchone()["extversion"]
    disk = shutil.disk_usage(args.pdf_dir)
    catalog_hash = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    summary = {
        "version": catalog["version"],
        "verified_at": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "catalog_sha256": catalog_hash,
        "domain": catalog["domain"],
        "paper_count": len(papers),
        "distinct_arxiv_ids": len(papers),
        "distinct_pdf_sha256": len(papers),
        "years": dict(sorted(Counter(p["year"] for p in papers.values()).items())),
        "pdf_bytes": sum(p["bytes"] for p in papers.values()),
        "uploaded_pdf_bytes": sum(p["bytes"] for p in papers.values()),
        "pages": sum(p["pages"] for p in papers.values()),
        "pages_with_text": sum(p["pages_with_text"] for p in papers.values()),
        "chunks": totals["chunks"],
        "synthetic_chunks": totals["synthetic"],
        **vectors,
        "process_indexes": indexes,
        "tasks": tasks,
        "task_attempts": attempts,
        "packages": {name: version(name) for name in ["pypdf", "fastembed", "onnxruntime", "psycopg"]},
        "embedding_models": models,
        "chunking": catalog["chunking"],
        "postgresql": server,
        "pgvector": vector_version,
        "schema_version": schema,
        "database_bytes": database_size,
        "relations": sizes,
        "filesystem_free_bytes": disk.free,
        "filesystem_total_bytes": disk.total,
        "limitations": [
            "Not a relevance benchmark: questions have no human relevance judgments.",
            "Current retrieval rejects more than 1000 candidate chunks (AI_CORPUS_LIMIT).",
            "No full-corpus latency or throughput result is claimed.",
        ],
    }
    save_state(args.output_dir / "summary.json", summary)
    subsets = {
        "version": catalog["version"],
        "catalog_sha256": catalog_hash,
        "ordering": "catalog acquisition order (cached API requested submittedDate ascending; initial 39 retained when extending the date window)",
        "subsets": [
            {
                "paper_count": size,
                "chunk_count": sum(p["chunk_count"] for p in list(papers.values())[:size]),
                "paper_keys": list(papers)[:size],
            }
            for size in [10, 30, 50, 100, 300]
            if size <= len(papers)
        ],
    }
    save_state(args.output_dir / "subsets.json", subsets)
    print(
        json.dumps(
            {
                k: summary[k]
                for k in [
                    "paper_count",
                    "pdf_bytes",
                    "pages",
                    "chunks",
                    "embeddings",
                    "database_bytes",
                    "filesystem_free_bytes",
                ]
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "demo/rag-300/catalog.json")
    parser.add_argument("--import-state", type=Path, default=ROOT / ".runtime/corpus300/import-state.json")
    parser.add_argument("--database-config", type=Path, default=ROOT / ".runtime/corpus300/database.json")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / ".runtime/corpus300/papers")
    parser.add_argument("--upload-dir", type=Path, default=ROOT / ".runtime/corpus300/uploads")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "demo/rag-300")
    snapshot(parser.parse_args())


if __name__ == "__main__":
    main()
