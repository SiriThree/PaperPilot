"""Exercise the seeded real corpus over trusted HTTPS; never print credentials or paper text."""

from datetime import UTC, datetime
import json

import httpx

from production_deployment import STATE, compose, metadata, wait_ready
from seed_real_literature import authenticate, checked, save_state, wait_task


def main():
    meta = metadata()
    corpus = json.loads((STATE / "literature/corpus-state.json").read_text(encoding="utf-8"))
    accounts = json.loads((STATE / "acceptance-secrets.json").read_text(encoding="utf-8"))
    owner = accounts["owner"]
    run_path = STATE / ("research-check-" + meta["source_fingerprint"][:12] + ".json")
    run_state = json.loads(run_path.read_text()) if run_path.exists() else {}
    checks = []

    def passed(name, condition):
        if not condition:
            raise RuntimeError("Research acceptance failed: " + name)
        checks.append(name)
        print("PASS", name, flush=True)

    with httpx.Client(
        base_url=meta["url"], verify=str(STATE / "local-ca.crt"), trust_env=False, timeout=105
    ) as client:
        authenticate(client, owner["username"], owner["password"], meta["url"])
        try:
            project = corpus["projects"]["rag-evidence"]
            base = "/api/v1/projects/" + project["project_id"]
            rag = project["papers"]["rag"]["paper_id"]
            originals = {}
            for entry in corpus["projects"].values():
                for paper in entry["papers"].values():
                    detail = checked(
                        client.get("/api/v1/projects/" + entry["project_id"] + "/papers/" + paper["paper_id"])
                    )
                    passed(
                        "real_paper_" + paper["arxiv_id"],
                        detail["paper"]["status"] == "searchable"
                        and bool(detail["sources"])
                        and all(not s["synthetic"] for s in detail["sources"]),
                    )
                    originals.update({s["chunk_id"]: s for s in detail["sources"]})

            def valid_sources(sources, paper_ids=None):
                return bool(sources) and all(
                    not s["synthetic"]
                    and s["chunk_id"] in originals
                    and (paper_ids is None or s["paper_id"] in paper_ids)
                    and all(
                        s[k] == originals[s["chunk_id"]][k] for k in ("paper_id", "page_number", "excerpt")
                    )
                    for s in sources
                )

            allowed = {p["paper_id"] for p in project["papers"].values()}
            for mode in ("keyword", "semantic", "hybrid"):
                result = checked(
                    client.post(
                        base + "/search",
                        json={"query": "retrieval augmented generation", "mode": mode, "limit": 10},
                    )
                )
                passed(
                    "real_" + mode + "_search_and_original_sources",
                    not result["mock"] and valid_sources([h["source"] for h in result["items"]], allowed),
                )

            question = "RAG-Sequence 和 RAG-Token 在生成时如何使用检索文档？"

            def task(kind, route, body):
                if kind not in run_state:
                    value = checked(
                        client.post(
                            base + route,
                            json=body,
                            headers={
                                "Idempotency-Key": "production-research-"
                                + meta["source_fingerprint"][:20]
                                + "-"
                                + kind
                            },
                        )
                    )
                    run_state[kind] = value["task_id"]
                    save_state(run_path, run_state)
                value = wait_task(client, base, run_state[kind], 150)
                if value["status"] != "succeeded":
                    raise RuntimeError(
                        kind + " task failed: " + value.get("error", {}).get("code", "UNKNOWN")
                    )
                return value["result"]

            answer = task("answer", "/answers", {"question": question, "paper_ids": [rag]})
            passed(
                "real_answer_with_selected_paper_citations",
                not answer["mock"]
                and not answer["insufficient_evidence"]
                and valid_sources(answer["sources"], {rag}),
            )
            evidence = task("evidence", "/papers/" + rag + "/evidence", None)
            fields = {
                name: evidence[name]
                for name in ("research_question", "method", "results", "conclusion", "limitations")
            }
            passed(
                "five_field_evidence_protocol",
                not evidence["mock"]
                and set(fields) == {"research_question", "method", "results", "conclusion", "limitations"},
            )
            passed(
                "evidence_original_source_identity",
                all(not f["sources"] or valid_sources(f["sources"], {rag}) for f in fields.values())
                and any(f["sources"] for f in fields.values()),
            )
            history = checked(client.get(base + "/papers/" + rag + "/evidence"))
            passed("evidence_history_persisted", bool(history["items"]))
            draft = task("draft", "/report-drafts", {"question": question, "paper_ids": [rag]})
            passed(
                "real_draft_with_original_citations",
                not draft["mock"] and bool(draft["body"]) and valid_sources(draft["sources"], {rag}),
            )
            if "report" not in run_state:
                report = checked(
                    client.post(
                        base + "/reports",
                        headers={
                            "Idempotency-Key": "production-research-report-" + meta["source_fingerprint"][:20]
                        },
                        json={
                            "title": "RAG 文献模型验收（待人工核验）",
                            "research_question": question,
                            "body": draft["body"],
                            "sources": draft["sources"],
                        },
                    )
                )
                run_state["report"] = {k: report[k] for k in ("report_id", "version_id")}
                save_state(run_path, run_state)
            report = run_state["report"]
            export_url = (
                base + "/reports/" + report["report_id"] + "/versions/" + report["version_id"] + "/text"
            )
            exported = client.get(export_url)
            passed(
                "report_saved_and_exported", exported.status_code == 200 and draft["body"] in exported.text
            )
            foreign = next(p for p in corpus["projects"].values() if p["project_id"] != project["project_id"])
            foreign_paper = next(iter(foreign["papers"].values()))["paper_id"]
            passed(
                "foreign_paper_not_enqueued",
                client.post(base + "/papers/" + foreign_paper + "/evidence").status_code == 404,
            )
            for entry in corpus["projects"].values():
                result = checked(
                    client.post(
                        "/api/v1/projects/" + entry["project_id"] + "/secure-queries",
                        json={"question": "按年份统计论文数量"},
                    )
                )
                passed(
                    "D_native_statistics_" + entry["project_id"],
                    result["execution_status"] == "executed"
                    and not result["mock"]
                    and sum(r["count"] for r in result["rows"]) == len(entry["papers"]),
                )
            compose("restart", "backend", capture=True)
            wait_ready(client)
            passed(
                "session_and_report_survive_restart",
                client.get("/api/v1/auth/me").status_code == 200
                and client.get(export_url).text == exported.text,
            )
            passed(
                "evidence_survives_restart",
                bool(checked(client.get(base + "/papers/" + rag + "/evidence"))["items"]),
            )
            health = checked(client.get("/api/v1/health"))
            report = {
                "deployment_id": meta["deployment_id"],
                "source_fingerprint": meta["source_fingerprint"],
                "research_model_fingerprint": health["deployment"]["research_model_fingerprint"],
                "completed_at": datetime.now(UTC).isoformat(),
                "passed": True,
                "checks": checks,
                "state": str(run_path.relative_to(STATE)),
                "scope": "6_real_papers_local_BGE_real_chat_A_C_D",
            }
            save_state(STATE / "evidence/research-acceptance.json", report)
            print("Production research acceptance passed:", len(checks), "checks")
        finally:
            client.post("/api/v1/auth/logout")


if __name__ == "__main__":
    main()
