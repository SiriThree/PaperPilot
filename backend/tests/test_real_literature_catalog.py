"""Prevent a changed/mistargeted corpus from silently becoming demo evidence."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "seed_real_literature", ROOT / "scripts/seed_real_literature.py"
)
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


def test_pinned_catalog_and_project_questions():
    catalog, papers = seed.load_catalog(ROOT / "demo/real-literature.json")
    assert len(papers) == 6 and len(catalog["projects"]) == 2
    for paper in papers.values():
        assert paper["pdf_url"].endswith(f"/{paper['arxiv_id']}v{paper['version']}")
        assert len(paper["sha256"]) == 64 and int(paper["sha256"], 16) > 0
    for project in catalog["projects"]:
        assert len(project["questions"]) >= 5
        assert any(q["expect_insufficient"] for q in project["questions"])
        for question in project["questions"]:
            assert set(question["expected_pages"]) <= set(question["paper_keys"])
            for key, pages in question["expected_pages"].items():
                assert pages and all(1 <= p <= papers[key]["pages"] for p in pages)


@pytest.mark.parametrize("mutation", ["scope", "duplicate", "traversal", "download_host"])
def test_catalog_rejects_wrong_evidence_scope_or_download_target(tmp_path, mutation):
    catalog = json.loads((ROOT / "demo/real-literature.json").read_text())
    if mutation == "scope":
        catalog["projects"][0]["questions"][0]["paper_keys"] = ["bert"]
    elif mutation == "duplicate":
        catalog["papers"].append(copy.deepcopy(catalog["papers"][0]))
    elif mutation == "traversal":
        catalog["papers"][0]["filename"] = "../paper.pdf"
    else:
        catalog["papers"][0]["pdf_url"] = "https://arxiv.org.example.com/paper.pdf"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog))
    with pytest.raises(ValueError):
        seed.load_catalog(path)


def test_changed_pdf_is_rejected_without_overwrite_or_download(tmp_path):
    _, papers = seed.load_catalog(ROOT / "demo/real-literature.json")
    paper = papers["rag"]
    target = tmp_path / paper["filename"]
    target.write_bytes(b"a different document")
    with pytest.raises(ValueError, match="hash mismatch"):
        seed.prepare_pdfs({"rag": paper}, tmp_path, download=True)
    assert target.read_bytes() == b"a different document"


def test_missing_pdf_requires_explicit_download(tmp_path):
    _, papers = seed.load_catalog(ROOT / "demo/real-literature.json")
    with pytest.raises(ValueError, match="Missing PDF"):
        seed.prepare_pdfs({"rag": papers["rag"]}, tmp_path, download=False)
    assert not list(tmp_path.iterdir())
