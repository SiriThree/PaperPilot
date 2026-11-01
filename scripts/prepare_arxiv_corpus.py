"""Collect versioned, text-extractable RAG PDFs for a reproducible local corpus.

Public metadata and hashes are versioned; PDFs and API responses remain local.
Requests are serial and at least three seconds apart. Existing PDFs are never
overwritten. The accepted catalog is published atomically after every paper.
"""

import argparse
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import httpx

ROOT = Path(__file__).resolve().parents[1]
QUERY = (
    'cat:cs.CL AND (ti:"retrieval augmented generation" OR '
    'ti:"retrieval-augmented generation") AND submittedDate:[202001010000 TO 202512312359]'
)
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "ar": "http://arxiv.org/schemas/atom",
    "o": "http://a9.com/-/spec/opensearch/1.1/",
}
MAX_BYTES = 20 * 1024 * 1024
PROBE = r"""
import io,json,re,resource,sys
resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
resource.setrlimit(resource.RLIMIT_CPU,(30,30))
from pypdf import PdfReader
from app.ai.pdf_worker import parse
data=sys.stdin.buffer.read(20*1024*1024+1)
try:
    result=parse(data,1200,120)
    if 'error' in result:
        print(json.dumps(result));sys.exit(0)
    chunks=result['chunks']
    pages=len(PdfReader(io.BytesIO(data)).pages)
    first=' '.join(c['excerpt'] for c in chunks if c['page_number']==1)
    tokens=set(re.findall(r'[a-z0-9]+',sys.argv[1].lower()))-{'a','an','the','of','for','and','to','with','from','on','in','as','at'}
    present=set(re.findall(r'[a-z0-9]+',first.lower()))
    print(json.dumps({'pages':pages,'pages_with_text':len({c['page_number'] for c in chunks}),
                     'chunk_count':len(chunks),'chunk_characters_including_overlap':sum(len(c['excerpt']) for c in chunks),
                     'title_token_overlap':len(tokens&present)/max(1,len(tokens))}))
except Exception:
    print(json.dumps({'error':'PDF_INVALID'}))
"""


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_feed(body):
    root = ET.fromstring(body)
    papers = []
    for entry in root.findall("a:entry", NS):
        identifier = entry.findtext("a:id", default="", namespaces=NS).rsplit("/abs/", 1)[-1]
        match = re.fullmatch(r"(\d{4}\.\d{4,5})v([1-9]\d*)", identifier)
        if not match:
            raise ValueError("API did not return versioned arXiv article IDs")
        title = " ".join(entry.findtext("a:title", default="", namespaces=NS).split())
        normalized = re.sub(r"[-‐‑–—\s]+", " ", title.lower())
        categories = [c.attrib["term"] for c in entry.findall("a:category", NS)]
        comment = entry.findtext("ar:comment", default="", namespaces=NS)
        if "retrieval augmented generation" not in normalized or "cs.CL" not in categories:
            continue
        if "withdrawn" in comment.lower():
            continue
        aid, version = match.group(1), int(match.group(2))
        published = entry.findtext("a:published", default="", namespaces=NS)
        papers.append(
            {
                "key": "arxiv-" + aid.replace(".", "-"),
                "arxiv_id": aid,
                "version": version,
                "title": title,
                "authors": [
                    a.findtext("a:name", default="", namespaces=NS) for a in entry.findall("a:author", NS)
                ],
                "published": published,
                "updated": entry.findtext("a:updated", namespaces=NS),
                "year": int(published[:4]),
                "categories": categories,
                "doi": entry.findtext("ar:doi", namespaces=NS),
                "abstract_url": f"https://arxiv.org/abs/{aid}v{version}",
                "pdf_url": f"https://arxiv.org/pdf/{aid}v{version}",
                "filename": f"{aid}v{version}.pdf",
            }
        )
    return papers, int(root.findtext("o:totalResults", default="0", namespaces=NS))


class PacedPublicClient:
    def __init__(self, interval):
        self.interval, self.last = interval, 0.0
        self.client = httpx.Client(
            timeout=90,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": "PaperPilot-Academic-Corpus/1.0 (local research)"},
        )

    def fetch(self, url, max_bytes):
        for attempt in range(2):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            with self.client.stream("GET", url) as response:
                if response.status_code == 403:
                    raise RuntimeError("Source denied access; stop instead of retrying or changing identity")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        time.sleep(10)
                        continue
                    raise RuntimeError(f"Source remains unavailable: HTTP {response.status_code}")
                response.raise_for_status()
                body = bytearray()
                for block in response.iter_bytes():
                    body.extend(block)
                    if len(body) > max_bytes:
                        raise ValueError("DOWNLOAD_SIZE_LIMIT")
                return bytes(body)
        raise RuntimeError("Source request did not complete")


def candidates(args, client):
    state = args.state_dir
    settings = {"query": args.query, "sort_by": "submittedDate", "sort_order": "ascending"}
    query_path = state / "query.json"
    if query_path.exists() and json.loads(query_path.read_text()) != settings:
        raise ValueError("Use a different state directory for a different query")
    atomic_json(query_path, settings)
    found, seen = [], set()
    for offset in range(0, args.candidate_count, 100):
        target = state / f"search-{offset:04d}.atom"
        params = {
            "search_query": args.query,
            "start": offset,
            "max_results": 100,
            "sortBy": "submittedDate",
            "sortOrder": "ascending",
        }
        url = "https://export.arxiv.org/api/query?" + urlencode(params)
        if not target.exists():
            body = client.fetch(url, 8 * 1024 * 1024)
            parse_feed(body)
            target.write_bytes(body)
        batch, total = parse_feed(target.read_bytes())
        for paper in batch:
            if paper["arxiv_id"] not in seen:
                found.append(paper)
                seen.add(paper["arxiv_id"])
        print(
            json.dumps(
                {"stage": "metadata", "offset": offset, "eligible": len(found), "total_results": total}
            ),
            flush=True,
        )
        if offset + 100 >= total:
            break
    atomic_json(state / "candidates.json", found)
    return found


def probe_pdf(data, title):
    if len(data) < 10000 or not data.startswith(b"%PDF-"):
        raise ValueError("NOT_A_PDF")
    environment = dict(os.environ, PYTHONPATH=str(ROOT / "backend"))
    try:
        process = subprocess.run(
            [sys.executable, "-c", PROBE, title], input=data, capture_output=True, timeout=45, env=environment
        )
        result = json.loads(process.stdout)
    except (subprocess.TimeoutExpired, ValueError):
        raise ValueError("PDF_PROBE_FAILED") from None
    if "error" in result:
        raise ValueError(result["error"])
    if result["pages"] < 3 or result["chunk_characters_including_overlap"] < 2000:
        raise ValueError("INSUFFICIENT_TEXT_FOR_RETRIEVAL")
    if result["title_token_overlap"] < 0.5:
        raise ValueError("PDF_TITLE_NEEDS_REVIEW")
    return result


def catalog(args, accepted):
    return {
        "version": "rag300-v1",
        "status": "complete" if len(accepted) == args.count else "collecting",
        "target_count": args.count,
        "domain": "Retrieval-Augmented Generation in NLP",
        "query": args.query,
        "selection": "First 300 accepted matching cs.CL titles in cached submittedDate-ascending API acquisition order (2020-2025); initial 39 PDFs retained when widening the date window. Distinct arXiv IDs and PDF SHA256; text/size/title checks; versions fixed at acquisition.",
        "chunking": {"characters": 1200, "overlap": 120},
        "papers": accepted,
        "projects": [
            {
                "key": "rag300",
                "name": "RAG 真实文献库（300篇）",
                "description": "300篇自然语言处理检索增强生成真实文献，用于后续检索优化和压力测试。",
                "paper_keys": [p["key"] for p in accepted],
                "questions": [],
            }
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--candidate-count", type=int, default=500)
    parser.add_argument("--query", default=QUERY)
    parser.add_argument("--interval", type=float, default=3)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".runtime/corpus300")
    parser.add_argument("--catalog", type=Path, default=ROOT / "demo/rag-300/catalog.json")
    args = parser.parse_args()
    if not 1 <= args.count <= args.candidate_count <= 2000 or args.interval < 3:
        parser.error("Use 1 <= count <= candidate-count <= 2000 and interval >= 3")
    args.state_dir.mkdir(parents=True, exist_ok=True)
    (args.state_dir / "papers").mkdir(exist_ok=True)
    lock = (args.state_dir / "download.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = shutil.disk_usage(args.state_dir)
    if before.free < 10 * 1024**3:
        raise RuntimeError("Reserve at least 10 GiB before corpus preparation")
    if not (args.state_dir / "capacity-before.json").exists():
        atomic_json(
            args.state_dir / "capacity-before.json",
            {
                "free_bytes": before.free,
                "total_bytes": before.total,
                "checked_at": datetime.now(UTC).isoformat(),
            },
        )
    client = PacedPublicClient(args.interval)
    try:
        pool = candidates(args, client)
        old = json.loads(args.catalog.read_text()) if args.catalog.exists() else catalog(args, [])
        if old["query"] != args.query or old["target_count"] != args.count:
            raise ValueError("Catalog selection differs from this run")
        accepted = old["papers"]
        ids, hashes = {p["arxiv_id"] for p in accepted}, {p["sha256"] for p in accepted}
        for paper in accepted:
            target = args.state_dir / "papers" / paper["filename"]
            if hashlib.sha256(target.read_bytes()).hexdigest() != paper["sha256"]:
                raise ValueError("An accepted PDF changed; refusing to overwrite")
        rejected_path = args.state_dir / "rejected.json"
        rejected = json.loads(rejected_path.read_text()) if rejected_path.exists() else []
        rejected_ids = {p["arxiv_id"] for p in rejected}
        atomic_json(args.catalog, catalog(args, accepted))
        for paper in pool:
            if len(accepted) == args.count:
                break
            if paper["arxiv_id"] in ids or paper["arxiv_id"] in rejected_ids:
                continue
            target = args.state_dir / "papers" / paper["filename"]
            try:
                download_url = paper["pdf_url"].replace("https://arxiv.org/", "https://export.arxiv.org/")
                data = target.read_bytes() if target.exists() else client.fetch(download_url, MAX_BYTES)
                probe = probe_pdf(data, paper["title"])
                digest = hashlib.sha256(data).hexdigest()
                if digest in hashes:
                    raise ValueError("DUPLICATE_PDF_CONTENT")
                if not target.exists():
                    with target.open("xb") as stream:
                        stream.write(data)
                paper = dict(paper, **probe, bytes=len(data), sha256=digest, download_url=download_url)
                accepted.append(paper)
                ids.add(paper["arxiv_id"])
                hashes.add(digest)
                atomic_json(args.catalog, catalog(args, accepted))
                print(
                    json.dumps(
                        {
                            "stage": "downloaded",
                            "accepted": len(accepted),
                            "arxiv_id": paper["arxiv_id"],
                            "bytes": len(data),
                            "pages": probe["pages"],
                            "chunks": probe["chunk_count"],
                        }
                    ),
                    flush=True,
                )
                time.sleep(1)
            except (ValueError, httpx.HTTPStatusError) as error:
                reason = str(error) if isinstance(error, ValueError) else f"HTTP_{error.response.status_code}"
                rejected.append({"arxiv_id": paper["arxiv_id"], "reason": reason})
                rejected_ids.add(paper["arxiv_id"])
                atomic_json(rejected_path, rejected)
                print(
                    json.dumps({"stage": "excluded", "arxiv_id": paper["arxiv_id"], "reason": reason}),
                    flush=True,
                )
        if len(accepted) != args.count:
            raise RuntimeError(f"Only {len(accepted)} papers accepted; extend candidate-count explicitly")
        print(
            json.dumps(
                {
                    "stage": "complete",
                    "count": len(accepted),
                    "pdf_bytes": sum(p["bytes"] for p in accepted),
                    "pages": sum(p["pages"] for p in accepted),
                    "chunks": sum(p["chunk_count"] for p in accepted),
                }
            ),
            flush=True,
        )
    finally:
        client.client.close()
        lock.close()


if __name__ == "__main__":
    main()
