"""Paired HTTPS ablations against the same real corpus, without changing its indexes.

Starts one owned API process per variant, sequentially. The control is the current
team-integrated code with streaming BM25 and one-shot query embeddings. No query
result cache, paid chat calls, PDF ingestion or database migrations are performed.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import ssl
import socket
import statistics
import subprocess
import sys
import time

import httpx

from seed_real_literature import authenticate, checked, save_state
from verify_full_corpus_search import source_rows, digest

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = {
    "control": ("streaming", "oneshot"),
    "resident_only": ("streaming", "resident"),
    "inverted_only": ("inverted", "oneshot"),
    "combined": ("inverted", "resident"),
}


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percentile(values, q):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, __import__("math").ceil(q * len(ordered)) - 1))]


def summarize(samples):
    return {
        mode: {
            "requests": len(rows),
            "successes": len(values),
            "median_seconds": round(statistics.median(values), 6) if values else None,
            "p95_seconds": round(percentile(values, 0.95), 6) if values else None,
            "min_seconds": min(values) if values else None,
            "max_seconds": max(values) if values else None,
        }
        for mode in ("keyword", "semantic", "hybrid")
        if (rows := [r for r in samples if r["mode"] == mode])
        for values in [[r["seconds"] for r in rows if r["valid"]]]
    }


def environment(args, cfg, model, lexical, worker):
    return dict(
        os.environ,
        PYTHONPATH=str(ROOT / "backend"),
        PAPERPILOT_ENV="development",
        PAPERPILOT_AUTH_MODE="credentials",
        PAPERPILOT_AUTH_DATABASE="shared",
        PAPERPILOT_A_MODE="persistent",
        PAPERPILOT_C_MODE="real",
        PAPERPILOT_D_MODE="real",
        PAPERPILOT_SECURITY_EXECUTION="disabled",
        PAPERPILOT_AUDIT_PREFLIGHT="disabled",
        PAPERPILOT_DATA_HOST=cfg["host"],
        PAPERPILOT_DATA_PORT=str(cfg["port"]),
        PAPERPILOT_DATA_USER=cfg["user"],
        PAPERPILOT_DATA_PASSWORD="",
        PAPERPILOT_DATA_PASSWORD_FILE=cfg["password_file"],
        PAPERPILOT_DATA_DBNAME=cfg["dbname"],
        PAPERPILOT_ALLOWED_ORIGINS=f"{args.origin},https://127.0.0.1:{args.port}",
        PAPERPILOT_STORAGE_DIR=str(args.upload_dir.resolve()),
        PAPERPILOT_AI_BASE_URL="https://unused.invalid/v1",
        PAPERPILOT_AI_API_KEY="local-benchmark-no-chat",
        PAPERPILOT_AI_API_KEY_FILE="",
        PAPERPILOT_AI_CHAT_MODEL="unused-no-chat",
        PAPERPILOT_AI_RESPONSE_FORMAT="json_object",
        PAPERPILOT_AI_EMBEDDING_BACKEND="fastembed",
        PAPERPILOT_AI_EMBEDDING_MODEL=model["model"],
        PAPERPILOT_AI_EMBEDDING_DIMENSIONS=str(model["dimensions"]),
        PAPERPILOT_AI_LOCAL_MODEL_DIR=model["model_dir"],
        PAPERPILOT_AI_EMBEDDING_REVISION=Path(model["model_dir"]).name,
        PAPERPILOT_AI_EMBEDDING_BASE_URL="",
        PAPERPILOT_AI_EMBEDDING_API_KEY="",
        PAPERPILOT_AI_TIMEOUT_SECONDS="90",
        PAPERPILOT_AI_MIN_SIMILARITY="0.2",
        PAPERPILOT_AI_CHUNK_STRATEGY="sentence-token-v2",
        PAPERPILOT_AI_LEXICAL_BACKEND=lexical,
        PAPERPILOT_AI_LEXICAL_CACHE_MB="256",
        PAPERPILOT_AI_QUERY_EMBEDDING_WORKER=worker,
        PAPERPILOT_AI_EMBEDDING_QUEUE_SIZE="16",
    )


def memory(pid):
    result = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in {"VmRSS", "VmHWM"}:
                result[key + "_kib"] = int(value.split()[0])
        children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        result["children"] = [memory(int(child)) for child in children]
    except (OSError, ValueError):
        pass
    return result


def run(args):
    args.state_dir.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(args.database_config.read_text())
    model = json.loads(args.model_manifest.read_text())
    state = json.loads(args.import_state.read_text())
    project_id = state["projects"][args.project_key]["project_id"]
    conn = {k: cfg[k] for k in ("host", "port", "user", "dbname")}
    conn["password"] = Path(cfg["password_file"]).read_text().strip()
    sources = source_rows(conn, project_id)
    original_digest = digest(sources)
    frozen = json.loads((ROOT / "demo/retrieval-v2/verification.json").read_text())
    if original_digest != frozen["source_snapshot_sha256"]:
        raise ValueError("Corpus differs from B0; freeze and audit it separately first")
    canonical = {str(row["chunk_id"]): row for row in sources}
    workload = json.loads(args.workload.read_text())["queries"]
    password = args.password_file.read_text().strip()
    tls = ssl.create_default_context(cafile=str(args.tls_cert))
    api = f"https://127.0.0.1:{args.port}"
    identities = {}
    report = {
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "historical_B0": {
            "commit": "15437271b39860975e613c3c4fee4e4cad7ed39a",
            "timings": frozen["timing_summary"],
            "transport": "HTTP",
        },
        "paper_count": len({r["paper_id"] for r in sources}),
        "active_sources": len(sources),
        "source_snapshot_sha256": original_digest,
        "workload_sha256": checksum(args.workload),
        "embedding_model": frozen["embedding_model"],
        "api_workers": 1,
        "transport": "HTTPS",
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "implementation_sha256": {
            str(p.relative_to(ROOT)): checksum(p)
            for p in [
                ROOT / "backend/app/ai/adapter.py",
                ROOT / "backend/app/ai/lexical_index.py",
                ROOT / "backend/app/ai/local_embeddings.py",
                ROOT / "backend/app/ai/resident_embeddings.py",
                ROOT / "backend/app/ai/embedding_worker.py",
                ROOT / "backend/app/data/retrieval.py",
                ROOT / "backend/app/main.py",
                ROOT / "backend/app/ai/settings.py",
            ]
        },
        "variants": {},
        "notes": [
            "Control and all variants use the same team-integrated code, data, TLS, auth and single API worker.",
            "Historical B0 timings are retained separately; ratios use the contemporaneous HTTPS control.",
            "Forty distinct workload queries run once per variant. The first query includes cold lexical construction.",
            "The resident model is loaded during application startup; startup time is recorded separately.",
            "No query-result or query-vector cache is implemented.",
            "PostgreSQL and filesystem caches are not flushed; variant order is fixed.",
            "Pressure samples are a bounded fixed-workload probe, not a long-running capacity/SLA claim.",
            "p95 is nearest-rank over successful requests only; errors are separate. Small samples do not support p99.",
            "No relevance labels, nDCG or answer-quality improvements are claimed.",
        ],
    }

    def query(client, q):
        started = time.perf_counter()
        response = client.post(
            f"/api/v1/projects/{project_id}/search", json={k: v for k, v in q.items() if k != "id"}
        )
        elapsed = time.perf_counter() - started
        payload = response.json()
        hits = payload.get("items", [])
        valid = (
            response.status_code == 200 and not payload.get("mock", True) and payload.get("mode") == q["mode"]
        )
        valid = valid and len(hits) == q["limit"]
        for number, hit in enumerate(hits, 1):
            source = hit["source"]
            row = canonical.get(source["chunk_id"])
            valid = (
                valid
                and row is not None
                and (
                    hit["paper"]["project_id"] == project_id
                    and hit["paper"]["title"] == row["title"]
                    and hit["paper"]["paper_id"] == source["paper_id"] == str(row["paper_id"])
                    and source["page_number"] == row["page_number"]
                    and source["excerpt"] == row["excerpt"]
                    and source["synthetic"] is False
                    and source["citation_id"] == str(number)
                )
            )
        signature = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        matches = q["id"] not in identities or identities[q["id"]] == signature
        return {
            "id": q["id"],
            "mode": q["mode"],
            "seconds": round(elapsed, 6),
            "http_status": response.status_code,
            "code": payload.get("code"),
            "hits": len(hits),
            "valid": bool(valid),
            "result_sha256": signature,
            "matches_control": matches,
        }

    for name, (lexical, worker) in VARIANTS.items():
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", args.port))
        log = (args.state_dir / (name + ".log")).open("a")
        started = time.perf_counter()
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.dev_composition:create_app",
                "--factory",
                "--app-dir",
                "backend",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.port),
                "--no-access-log",
                "--ssl-certfile",
                str(args.tls_cert.resolve()),
                "--ssl-keyfile",
                str(args.tls_key.resolve()),
            ],
            cwd=ROOT,
            env=environment(args, cfg, model, lexical, worker),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=api, verify=tls, trust_env=False, timeout=95) as client:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError(f"{name} failed to start; see private log")
                    try:
                        if client.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("API did not become ready")
                startup = time.perf_counter() - started
                authenticate(client, args.username, password, args.origin)
                assert (
                    len(checked(client.get(f"/api/v1/projects/{project_id}/papers"))["items"])
                    == report["paper_count"]
                )
                samples = []
                for q in workload:
                    sample = query(client, q)
                    samples.append(sample)
                    if name == "control":
                        identities[q["id"]] = sample["result_sha256"]
                    print(json.dumps({"variant": name, **sample}), flush=True)
                warm_repeat = query(client, workload[0])
                result = {
                    "lexical_backend": lexical,
                    "query_worker": worker,
                    "startup_seconds": round(startup, 6),
                    "sequential": samples,
                    "first_query_seconds": samples[0]["seconds"],
                    "warm_repeat": warm_repeat,
                    "summary": summarize(samples),
                    "pressure": {},
                    "memory": memory(process.pid),
                }
                report["variants"][name] = result
                if name == "combined":
                    for concurrency in args.concurrency:
                        started = time.perf_counter()
                        with ThreadPoolExecutor(max_workers=concurrency) as pool:
                            probes = list(pool.map(lambda q: query(client, q), workload))
                        duration = time.perf_counter() - started
                        result["pressure"][str(concurrency)] = {
                            "requests": len(probes),
                            "duration_seconds": round(duration, 6),
                            "successful_qps": round(sum(p["valid"] for p in probes) / duration, 6),
                            "samples": probes,
                            "summary": summarize(probes),
                            "memory": memory(process.pid),
                        }
                        print(
                            json.dumps(
                                {
                                    "variant": name,
                                    "concurrency": concurrency,
                                    "successful": sum(p["valid"] for p in probes),
                                    "seconds": duration,
                                }
                            ),
                            flush=True,
                        )
                save_state(args.output, report)
                all_samples = (
                    samples + [warm_repeat] + [s for p in result["pressure"].values() for s in p["samples"]]
                )
                if not all(s["valid"] and s["matches_control"] for s in all_samples):
                    raise ValueError(f"{name} failed validation; preserve the partial report")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            log.close()
    after = digest(source_rows(conn, project_id))
    if after != original_digest:
        raise ValueError("Corpus changed during the experiment")
    report.update(
        status="complete",
        checked_at=datetime.now(UTC).isoformat(),
        source_snapshot_after_sha256=after,
        corpus_unchanged=True,
    )
    save_state(args.output, report)
    print(json.dumps({name: v["summary"] for name, v in report["variants"].items()}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-config", type=Path, required=True)
    parser.add_argument("--import-state", type=Path, required=True)
    parser.add_argument("--upload-dir", type=Path, required=True)
    parser.add_argument("--model-manifest", type=Path, required=True)
    parser.add_argument("--tls-cert", type=Path, required=True)
    parser.add_argument("--tls-key", type=Path, required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--username", default="alice")
    parser.add_argument("--project-key", default="rag300")
    parser.add_argument("--origin", default="https://127.0.0.1:15178")
    parser.add_argument("--port", type=int, default=18806)
    parser.add_argument("--concurrency", type=int, nargs="+", default=[2, 4, 8])
    parser.add_argument("--workload", type=Path, default=ROOT / "demo/rag-300/workload.json")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".runtime/acceleration/benchmark")
    parser.add_argument("--output", type=Path, default=ROOT / "demo/retrieval-acceleration/benchmark.json")
    args = parser.parse_args()
    if any(not 1 <= n <= 8 for n in args.concurrency):
        parser.error("Use concurrency 1..8")
    run(args)


if __name__ == "__main__":
    main()
