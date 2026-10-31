"""Read-only check: committed active sources must equal the audited original-text slices."""

import argparse
from collections import defaultdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.token_budget import TokenBudget  # noqa: E402
from seed_real_literature import save_state  # noqa: E402


def verify(args):
    catalog = json.loads(args.catalog.read_text())
    comparison = json.loads(args.comparison.read_text())
    state = json.loads(args.import_state.read_text())
    if state.get("status") != "complete" or catalog["status"] != "complete":
        raise ValueError("Finish the full import before source verification")
    project = state["projects"][catalog["projects"][0]["key"]]
    entries = project["papers"]
    budget = TokenBudget(args.tokenizer_dir)
    if budget.fingerprint != comparison["policy"]["tokenizer_sha256"]:
        raise ValueError("Tokenizer differs from the audited snapshot")
    cache_key = hashlib.sha256((comparison["code_sha256"] + comparison["policy_id"]).encode()).hexdigest()[
        :16
    ]
    cache = args.cache_dir / cache_key
    config = json.loads(args.database_config.read_text())
    connection = {key: config[key] for key in ["host", "port", "user", "dbname"]}
    connection["password"] = Path(config["password_file"]).read_text().strip()
    with psycopg.connect(**connection, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        rows = conn.execute(
            "SELECT pp.paper_id::text,p.title,rs.chunk_id::text,rs.page_number,rs.excerpt,rs.synthetic "
            "FROM project_papers pp JOIN papers p ON p.metadata_id=pp.metadata_id AND p.project_id=pp.project_id "
            "JOIN result_sources rs ON rs.task_id=pp.active_process_task_id AND rs.project_id=pp.project_id "
            "WHERE pp.project_id=%s AND pp.removed_at IS NULL AND pp.status='searchable' "
            "AND pp.index_state='ready' AND rs.scope='root' ORDER BY pp.paper_id,rs.ordinal",
            (project["project_id"],),
        ).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["paper_id"]].append(row)
    if set(grouped) != {entry["paper_id"] for entry in entries.values()}:
        raise ValueError("Active database papers differ from the import manifest")
    all_ids, max_tokens = set(), 0
    for paper in catalog["papers"]:
        entry = entries[paper["key"]]
        expected = json.loads((cache / (paper["key"] + ".json")).read_text())
        if (
            entry["status"] != "succeeded"
            or expected["pdf_sha256"] != paper["sha256"]
            or expected["policy_id"] != comparison["policy_id"]
        ):
            raise ValueError("Cached/source import identity differs")
        stored = grouped[entry["paper_id"]]
        if [(row["page_number"], row["excerpt"]) for row in stored] != [
            (c["page_number"], c["excerpt"]) for c in expected["chunks"]
        ]:
            raise ValueError("Database sources differ from audited original-text spans")
        for row in stored:
            if row["title"] != paper["title"] or row["synthetic"] or row["chunk_id"] in all_ids:
                raise ValueError("Unexpected title, synthetic source, or duplicate source identity")
            all_ids.add(row["chunk_id"])
            count = budget.count(row["title"] + "\n" + row["excerpt"])
            if count > budget.max_tokens:
                raise ValueError("Committed embedding input exceeds the prepared model limit")
            max_tokens = max(max_tokens, count)
    result = {
        "verified_at": datetime.now(UTC).isoformat(),
        "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        "paper_count": len(grouped),
        "active_sources": len(rows),
        "unique_chunk_ids": len(all_ids),
        "exact_matches_to_audited_page_excerpts": True,
        "tokenizer_sha256": budget.fingerprint,
        "max_actual_embedding_input_tokens": max_tokens,
        "model_input_limit": budget.max_tokens,
        "over_limit": 0,
        "synthetic_sources": 0,
        "database_read_only": True,
    }
    save_state(args.output, result)
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "demo/chunking-v2/catalog.json")
    parser.add_argument("--comparison", type=Path, default=ROOT / "demo/chunking-v2/comparison.json")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".runtime/chunking-v2/parsed")
    parser.add_argument("--import-state", type=Path, required=True)
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "demo/chunking-v2/ingestion-check.json")
    verify(parser.parse_args())


if __name__ == "__main__":
    main()
