"""Download versioned real papers and seed them through the existing authenticated API.

PDFs, credentials and runtime state stay outside version control. Rerunning uses
project markers and stable upload idempotency keys; it never resets existing data.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.secret_config import secret_value  # noqa: E402


def load_catalog(path):
    catalog = json.loads(path.read_text(encoding="utf-8"))
    papers = {paper["key"]: paper for paper in catalog["papers"]}
    if len(papers) != len(catalog["papers"]) or len({p["key"] for p in catalog["projects"]}) != len(
        catalog["projects"]
    ):
        raise ValueError("Duplicate catalog keys")
    for project in catalog["projects"]:
        if not project["paper_keys"] or any(key not in papers for key in project["paper_keys"]):
            raise ValueError("Unknown paper in project")
        for question in project["questions"]:
            if not question["paper_keys"] or not set(question["paper_keys"]) <= set(project["paper_keys"]):
                raise ValueError("Question scope exceeds its project")
    for paper in papers.values():
        if Path(paper["filename"]).name != paper["filename"] or not paper["filename"].endswith(".pdf"):
            raise ValueError("Invalid local PDF filename")
        if urlsplit(paper["pdf_url"]).hostname != "arxiv.org" or not paper["pdf_url"].startswith("https://"):
            raise ValueError("Catalog download must use the official arXiv HTTPS endpoint")
    return catalog, papers


def prepare_pdfs(papers, directory, download):
    directory.mkdir(parents=True, exist_ok=True)
    for paper in papers.values():
        target = directory / paper["filename"]
        if not target.exists():
            if not download:
                raise ValueError(f"Missing PDF {target.name}; supply --download or --pdf-dir")
            with httpx.stream("GET", paper["pdf_url"], follow_redirects=True, timeout=90) as response:
                response.raise_for_status()
                data = bytearray()
                for block in response.iter_bytes():
                    data.extend(block)
                    if len(data) > 20 * 1024 * 1024:
                        raise ValueError("PDF exceeds upload budget")
            if not data.startswith(b"%PDF-") or len(data) < 10000:
                raise ValueError("Downloaded resource is not a research PDF")
            if hashlib.sha256(data).hexdigest() != paper["sha256"]:
                raise ValueError("Downloaded PDF differs from the pinned version")
            with target.open("xb") as stream:
                stream.write(data)
            time.sleep(1)
        if hashlib.sha256(target.read_bytes()).hexdigest() != paper["sha256"]:
            raise ValueError(f"PDF hash mismatch: {target.name}")


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def checked(response):
    if response.status_code >= 400:
        try:
            code = response.json().get("code", "HTTP_ERROR")
        except ValueError:
            code = "HTTP_ERROR"
        raise RuntimeError(f"API rejected operation: HTTP {response.status_code} {code}")
    return response.json()


def authenticate(client, username, password, origin):
    health = checked(client.get("/api/v1/health"))
    if health["modes"]["a"] not in {"persistent", "real"} or health["modes"]["c"] != "real":
        raise ValueError("Real-literature seeding requires persistent A and real C")
    if "authentication_credentials" not in health["limitations"]:
        raise ValueError("Use the explicitly configured credential-authenticated development service")
    csrf = checked(client.get("/api/v1/auth/csrf"))["csrf_token"]
    client.headers.update({"Origin": origin, "X-CSRF-Token": csrf, "X-PaperPilot-Client": "web"})
    identity = checked(client.post("/api/v1/auth/login", json={"username": username, "password": password}))
    return identity["user"]


def wait_task(client, base, task_id, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = checked(client.get(base + "/tasks/" + task_id))
        if task["status"] in {"succeeded", "failed"}:
            return task
        time.sleep(0.3)
    raise RuntimeError(f"Task state still unknown; query the original task {task_id}")


def seed(args, catalog, papers):
    password = secret_value({"LOGIN_PASSWORD_FILE": str(args.password_file.resolve())}, "LOGIN_PASSWORD")
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
        raise ValueError("Select a separate state file for a different service or catalog version")
    with httpx.Client(base_url=args.api_base.rstrip("/"), timeout=30, trust_env=False) as client:
        identity = authenticate(client, args.username, password, args.origin)
        for project in catalog["projects"]:
            marker = f"[paperpilot-real-corpus:{catalog['version']}:{project['key']}]"
            known = checked(client.get("/api/v1/projects"))["items"]
            matches = [
                p for p in known if p["owner_id"] == identity["user_id"] and marker in p["description"]
            ]
            if len(matches) > 1:
                raise ValueError("Multiple projects have the same corpus marker; reconcile before seeding")
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
            save_state(args.state, state)
            base = "/api/v1/projects/" + record["project_id"]
            for key in project["paper_keys"]:
                paper = papers[key]
                upload_key = f"real-literature-{catalog['version']}-{project['key']}-{key}"
                with (args.pdf_dir / paper["filename"]).open("rb") as pdf:
                    upload = checked(
                        client.post(
                            base + "/papers",
                            headers={"Idempotency-Key": upload_key},
                            data={"title": paper["title"], "year": str(paper["year"])},
                            files={"file": (paper["filename"], pdf, "application/pdf")},
                        )
                    )
                entry = result["papers"].setdefault(key, {})
                entry.update(
                    paper_id=upload["paper"]["paper_id"],
                    task_id=upload["task"]["task_id"],
                    arxiv_id=paper["arxiv_id"],
                    sha256=paper["sha256"],
                    title=paper["title"],
                )
                save_state(args.state, state)
                task = wait_task(client, base, entry["task_id"], args.wait_seconds)
                if task["status"] == "failed" and args.retry_failed:
                    checked(client.post(base + "/tasks/" + entry["task_id"] + "/retry"))
                    task = wait_task(client, base, entry["task_id"], args.wait_seconds)
                entry["status"] = task["status"]
                entry["error"] = task.get("error")
                save_state(args.state, state)
                if task["status"] != "succeeded":
                    raise RuntimeError(
                        f"Processing failed for {key}: {task['error']['code']}; original task retained"
                    )
                detail = checked(client.get(base + "/papers/" + entry["paper_id"]))
                if detail["paper"]["status"] != "searchable" or not detail["sources"]:
                    raise ValueError("Processing returned no searchable sources")
                if any(s["synthetic"] or s["paper_id"] != entry["paper_id"] for s in detail["sources"]):
                    raise ValueError("A real paper returned synthetic or mismatched sources")
                entry.update(
                    source_count=len(detail["sources"]),
                    pages_with_text=len({s["page_number"] for s in detail["sources"]}),
                )
                save_state(args.state, state)
                print(
                    json.dumps(
                        {
                            "project": project["name"],
                            "paper": paper["title"],
                            "sources": entry["source_count"],
                            "pages": entry["pages_with_text"],
                            "status": "searchable",
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "demo/real-literature.json")
    parser.add_argument("--pdf-dir", type=Path, default=ROOT / ".runtime/real-demo/papers")
    parser.add_argument("--download", action="store_true", help="Explicitly download missing versioned PDFs")
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--origin", required=True, help="An origin already trusted by the local service")
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--state", type=Path, default=ROOT / ".runtime/real-demo/corpus-state.json")
    parser.add_argument("--wait-seconds", type=int, default=180)
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly retry the original failed processing task once",
    )
    args = parser.parse_args()
    try:
        catalog, papers = load_catalog(args.catalog)
        prepare_pdfs(papers, args.pdf_dir, args.download)
        result = seed(args, catalog, papers)
    except Exception as exc:
        if isinstance(exc, (ValueError, RuntimeError)):
            print(str(exc), file=sys.stderr)
        else:
            print(
                "Seeding failed; inspect local configuration and query any original task before retrying.",
                file=sys.stderr,
            )
        return 1
    print(f"Ready: {len(result['projects'])} real research projects; state saved to {args.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
