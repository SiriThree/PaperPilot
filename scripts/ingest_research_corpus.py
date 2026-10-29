"""Resume a bounded real-PDF import, optionally following a growing catalog.

Uses the normal credential-authenticated upload/task APIs. No direct database
inserts, generated papers, or chat completions are used. An ambiguous upload is
replayed with the same idempotency key on the next invocation.
"""

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx

from seed_real_literature import ROOT, authenticate, checked, load_catalog, save_state


def validate_entry(entry, paper, upload_key):
    if entry["sha256"] != paper["sha256"] or entry["upload_key"] != upload_key:
        raise ValueError("Stored import identity differs from the catalog")


def verify_detail(detail, paper, entry):
    sources = detail["sources"]
    if detail["paper"]["status"] != "searchable" or len(sources) != paper["chunk_count"]:
        raise ValueError("Stored searchable source count differs from the PDF probe")
    if any(s["synthetic"] or s["paper_id"] != entry["paper_id"] for s in sources):
        raise ValueError("A real PDF returned synthetic or mismatched sources")
    if len({s["chunk_id"] for s in sources}) != len(sources):
        raise ValueError("Duplicate source identity")
    return {
        "source_count": len(sources),
        "pages_with_text": len({s["page_number"] for s in sources}),
    }


def import_corpus(args):
    catalog, papers = load_catalog(args.catalog)
    if len(catalog["projects"]) != 1 or catalog["target_count"] != args.expected_count:
        raise ValueError("Expected one project with the explicitly requested target count")
    project = catalog["projects"][0]
    if set(project["paper_keys"]) != set(papers):
        raise ValueError("The single project must contain the whole catalog")
    state = (
        json.loads(args.state.read_text())
        if args.state.exists()
        else {
            "api_base": args.api_base.rstrip("/"),
            "catalog_version": catalog["version"],
            "projects": {},
        }
    )
    if state["api_base"] != args.api_base.rstrip("/") or state["catalog_version"] != catalog["version"]:
        raise ValueError("Use a separate state file for a different service or catalog")
    password = args.password_file.read_text().strip()
    if not password:
        raise ValueError("Empty credential file")
    with httpx.Client(base_url=args.api_base, timeout=60, trust_env=False) as client:
        identity = authenticate(client, args.username, password, args.origin)
        marker = f"[paperpilot-real-corpus:{catalog['version']}:{project['key']}]"
        matches = [
            p
            for p in checked(client.get("/api/v1/projects"))["items"]
            if p["owner_id"] == identity["user_id"] and marker in p["description"]
        ]
        if len(matches) > 1:
            raise ValueError("Duplicate project markers require reconciliation")
        record = (
            matches[0]
            if matches
            else checked(
                client.post(
                    "/api/v1/projects",
                    json={
                        "name": project["name"],
                        "description": project["description"] + " " + marker,
                    },
                )
            )
        )
        result = state["projects"].setdefault(project["key"], {"papers": {}})
        if result.get("project_id", record["project_id"]) != record["project_id"]:
            raise ValueError("Stored project identity differs from its marker")
        result.update(project_id=record["project_id"], name=project["name"])
        entries = result["papers"]
        save_state(args.state, state)
        base = "/api/v1/projects/" + record["project_id"]
        deadlines = {}
        while True:
            latest, papers = load_catalog(args.catalog)
            if latest["version"] != catalog["version"] or latest["query"] != catalog["query"]:
                raise ValueError("Catalog identity changed during import")
            if len(papers) > args.expected_count or not set(entries) <= set(papers):
                raise ValueError("Catalog removed imported papers or exceeded its target")
            for key, entry in entries.items():
                upload_key = f"real-literature-{catalog['version']}-{project['key']}-{key}"
                validate_entry(entry, papers[key], upload_key)
                if (entry.get("status") == "succeeded" and "source_count" in entry) or "task_id" not in entry:
                    continue
                task = checked(client.get(base + "/tasks/" + entry["task_id"]))
                entry.update(status=task["status"], error=task.get("error"))
                if task["status"] == "failed":
                    save_state(args.state, state)
                    raise RuntimeError(
                        f"Task failed for {key}; original task retained: {task['error']['code']}"
                    )
                if task["status"] == "succeeded":
                    detail = checked(client.get(base + "/papers/" + entry["paper_id"]))
                    entry.update(verify_detail(detail, papers[key], entry))
                    save_state(args.state, state)
                    print(
                        json.dumps(
                            {
                                "stage": "indexed",
                                "count": sum(e.get("status") == "succeeded" for e in entries.values()),
                                "arxiv_id": papers[key]["arxiv_id"],
                                "chunks": entry["source_count"],
                            }
                        ),
                        flush=True,
                    )
                elif time.monotonic() > deadlines.setdefault(key, time.monotonic() + args.wait_seconds):
                    save_state(args.state, state)
                    raise RuntimeError(f"Task still pending for {key}; original task retained")
            pending = sum(e.get("status") != "succeeded" for e in entries.values())
            threshold = getattr(args, "serialize_above_chunks", 0)
            large_pending = threshold and any(
                e.get("status") != "succeeded" and papers[k]["chunk_count"] >= threshold
                for k, e in entries.items()
            )
            for key, paper in papers.items():
                if key in entries and "task_id" in entries[key]:
                    continue
                # A saved ambiguous upload already occupies one queue slot.
                if pending >= args.max_pending and key not in entries:
                    break
                if key not in entries and (
                    large_pending or (threshold and paper["chunk_count"] >= threshold and pending)
                ):
                    break
                upload_key = f"real-literature-{catalog['version']}-{project['key']}-{key}"
                path = args.pdf_dir / paper["filename"]
                if hashlib.sha256(path.read_bytes()).hexdigest() != paper["sha256"]:
                    raise ValueError(f"PDF hash mismatch: {paper['filename']}")
                if key not in entries:
                    entries[key] = {
                        "sha256": paper["sha256"],
                        "upload_key": upload_key,
                        "arxiv_id": paper["arxiv_id"],
                        "status": "uploading",
                    }
                    pending += 1
                # Persist before POST: replay uses this same identity if the acknowledgement is lost.
                save_state(args.state, state)
                if threshold and paper["chunk_count"] >= threshold:
                    large_pending = True
                with path.open("rb") as stream:
                    upload = checked(
                        client.post(
                            base + "/papers",
                            headers={"Idempotency-Key": upload_key},
                            data={"title": paper["title"], "year": str(paper["year"])},
                            files={"file": (paper["filename"], stream, "application/pdf")},
                        )
                    )
                entries[key].update(
                    paper_id=upload["paper"]["paper_id"],
                    task_id=upload["task"]["task_id"],
                    status="pending_verification",
                )
                deadlines[key] = time.monotonic() + args.wait_seconds
                save_state(args.state, state)
            succeeded = sum(e.get("status") == "succeeded" for e in entries.values())
            if latest["status"] == "complete" and len(papers) == succeeded == args.expected_count:
                state["status"] = "complete"
                save_state(args.state, state)
                return state
            if not args.watch and len(papers) == succeeded:
                return state
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "demo/rag-300/catalog.json")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / ".runtime/corpus300/papers")
    parser.add_argument("--api-base", default="http://127.0.0.1:18802")
    parser.add_argument("--origin", default="http://127.0.0.1:15175")
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=ROOT / ".runtime/corpus300/import-state.json")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--expected-count", type=int, default=300)
    parser.add_argument("--max-pending", type=int, default=3)
    parser.add_argument(
        "--serialize-above-chunks",
        type=int,
        default=0,
        help="Process a paper at/above this chunk count alone; zero disables",
    )
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args()
    if (
        not 1 <= args.max_pending <= 8
        or args.expected_count < 1
        or args.wait_seconds < 1
        or args.serialize_above_chunks < 0
    ):
        parser.error("Invalid queue size, count or timeout")
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            state = import_corpus(args)
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except httpx.HTTPError:
            print(
                "Transport interrupted; original upload/task identities are retained for resume.",
                file=sys.stderr,
            )
            return 1
    print(json.dumps({"stage": "complete", "projects": len(state["projects"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
