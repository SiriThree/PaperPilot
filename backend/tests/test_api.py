from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.mock_data import seed_id
from app.schemas import SearchHit, SearchResult, Source
from conftest import finished


def base(project):
    return f"/api/v1/projects/{project}"


def test_health(client):
    data = client.get("/api/v1/health").json()
    assert data["status"] == "ok" and data["mock"] is True
    assert data["modes"] == {"a": "mock", "c": "mock", "d": "mock"}


def test_unauthenticated_and_request_id(client):
    rid = str(uuid4())
    response = client.get("/api/v1/projects", headers={"X-Request-ID": rid})
    assert response.status_code == 401
    assert response.json() == {"code": "UNAUTHENTICATED", "message": "请先登录", "request_id": rid}
    assert response.headers["X-Request-ID"] == rid


def test_invalid_request_id_is_replaced(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "not-an-id"})
    assert str(uuid4()) != response.headers["X-Request-ID"]
    from uuid import UUID

    UUID(response.headers["X-Request-ID"])


def test_validation_error_does_not_echo_input(client):
    response = client.post("/api/v1/auth/login", json={"username": "alice", "private_field": "DO_NOT_ECHO"})
    assert response.status_code == 422
    assert "DO_NOT_ECHO" not in response.text and "private_field" not in response.text


def test_csrf_checks(app):
    with TestClient(app) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 403
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "alice"},
                headers={"X-PaperPilot-Client": "web", "Origin": "https://untrusted.invalid"},
            ).status_code
            == 403
        )


def test_dev_cookie_and_logout(client):
    response = client.post("/api/v1/auth/login", json={"username": "alice"})
    assert (
        "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
    )
    assert response.json()["authentication"] == "development_identity_only"
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_projects_scoped_and_create(signed):
    projects = signed.get("/api/v1/projects").json()["items"]
    assert len(projects) == 1
    assert projects[0]["project_id"] == str(seed_id("project-a"))
    created = signed.post("/api/v1/projects", json={"name": "新项目"})
    assert created.status_code == 201 and created.json()["role"] == "owner"


@pytest.mark.parametrize(
    "suffix,method,payload",
    [
        ("papers", "GET", None),
        ("search", "POST", {"query": "test"}),
        ("answers", "POST", {"question": "test"}),
        ("reports", "GET", None),
        ("audit-events", "GET", None),
        ("secure-queries", "POST", {"question": "按年份统计论文数量"}),
        ("members", "GET", None),
    ],
)
def test_project_id_substitution_denied(signed, suffix, method, payload):
    response = signed.request(method, base(seed_id("project-b")) + "/" + suffix, json=payload)
    assert response.status_code == 404
    assert "隔离项目" not in response.text


def test_resource_id_substitution_denied(signed, project):
    assert signed.get(base(project) + f"/papers/{seed_id('paper-b')}").status_code == 404
    assert (
        signed.post(
            base(project) + "/answers", json={"question": "test", "paper_ids": [str(seed_id("paper-b"))]}
        ).status_code
        == 404
    )


def test_member_revocation_immediate(app, signed, project):
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as bob:
        bob.post("/api/v1/auth/login", json={"username": "bob"})
        assert bob.get(base(project) + "/papers").status_code == 200
        assert bob.get(base(project) + "/audit-events").status_code == 403
        assert bob.post(base(project) + "/members", json={"username": "eve"}).status_code == 403
        assert signed.delete(base(project) + f"/members/{seed_id('bob')}").status_code == 200
        assert bob.get(base(project) + "/papers").status_code == 404


def test_upload_task_and_duplicate(signed, project):
    path = base(project) + "/papers"
    kwargs = {
        "files": {"file": ("sample.pdf", b"%PDF-1.4\nSynthetic fixture only")},
        "data": {"title": "Mock 上传论文"},
        "headers": {"Idempotency-Key": "upload-one"},
    }
    response = signed.post(path, **kwargs)
    assert response.status_code == 202
    task = response.json()["task"]
    assert task["status"] == "pending"
    done = finished(signed, project, task["task_id"])
    assert done["status"] == "succeeded" and done["result"]["kind"] == "process"
    assert signed.post(path, **kwargs).json()["task"]["task_id"] == task["task_id"]
    kwargs["headers"] = {"Idempotency-Key": "upload-two"}
    assert signed.post(path, **kwargs).status_code == 409
    detail = signed.get(base(project) + "/papers/" + response.json()["paper"]["paper_id"]).json()
    assert detail["paper"]["status"] == "searchable"
    assert all(s["synthetic"] for s in detail["sources"])


def test_unsupported_upload(signed, project):
    response = signed.post(
        base(project) + "/papers", files={"file": ("fake.pdf", b"not pdf")}, data={"title": "fake"}
    )
    assert response.status_code == 415


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_search_modes_and_filters(signed, project, mode):
    response = signed.post(base(project) + "/search", json={"query": "test", "mode": mode})
    assert response.status_code == 200 and response.json()["mode"] == mode
    assert response.json()["items"][0]["source"]["paper_id"] == str(seed_id("paper-a"))
    assert (
        signed.post(base(project) + "/search", json={"query": "test", "year_from": 2025}).json()["items"]
        == []
    )
    assert (
        signed.post(
            base(project) + "/search", json={"query": "test", "year_from": 2025, "year_to": 2020}
        ).status_code
        == 422
    )


@pytest.mark.parametrize("question,insufficient", [("证据核验", False), ("证据不足", True)])
def test_answer_contract_and_request_correlation(signed, project, question, insufficient):
    rid = str(uuid4())
    response = signed.post(
        base(project) + "/answers", json={"question": question}, headers={"X-Request-ID": rid}
    )
    done = finished(signed, project, response.json()["task_id"])
    assert done["request_id"] == rid and done["status"] == "succeeded"
    answer = done["result"]
    assert answer["insufficient_evidence"] is insufficient
    assert answer["citation_ids"] == [s["citation_id"] for s in answer["sources"]]


def test_failed_task_retry_same_identity(signed, project):
    response = signed.post(base(project) + "/answers", json={"question": "模拟失败"})
    done = finished(signed, project, response.json()["task_id"])
    assert done["status"] == "failed" and done["error"]["code"] == "AI_UNAVAILABLE"
    retried = signed.post(base(project) + f"/tasks/{done['task_id']}/retry")
    assert retried.status_code == 202 and retried.json()["task_id"] == done["task_id"]
    assert finished(signed, project, done["task_id"])["attempts"] == 2


def test_evidence_and_draft(signed, project):
    evidence = signed.post(base(project) + f"/papers/{seed_id('paper-a')}/evidence")
    result = finished(signed, project, evidence.json()["task_id"])["result"]
    assert result["method"]["status"] == "pending_verification" and result["method"]["sources"]
    assert result["limitations"]["status"] == "not_mentioned"
    draft = signed.post(base(project) + "/report-drafts", json={"question": "检索"})
    assert finished(signed, project, draft.json()["task_id"])["result"]["kind"] == "draft"


def test_forged_search_source_rejected(app, signed, project, monkeypatch):
    data = app.state.adapters.data
    from app.adapters.ports import Context

    ctx = Context(seed_id("alice"), seed_id("project-a"), uuid4())
    paper = data.paper(ctx, seed_id("paper-a"))
    forged = Source(
        citation_id="1", paper_id=paper.paper_id, chunk_id=uuid4(), page_number=1, excerpt="fabricated"
    )

    async def search(ctx, query):
        return SearchResult(items=[SearchHit(paper=paper, source=forged)], mode=query.mode)

    monkeypatch.setattr(app.state.adapters.ai, "search", search)
    assert signed.post(base(project) + "/search", json={"query": "test"}).status_code == 502


def test_report_versions_and_idempotency(signed, project):
    source = signed.get(base(project) + f"/papers/{seed_id('paper-a')}").json()["sources"][0]
    payload = {"title": "报告", "research_question": "问题", "body": "版本一", "sources": [source]}
    first = signed.post(
        base(project) + "/reports", json=payload, headers={"Idempotency-Key": "save-one"}
    ).json()
    replay = signed.post(base(project) + "/reports", json=payload, headers={"Idempotency-Key": "save-one"})
    assert replay.json()["version_id"] == first["version_id"]
    payload.update(report_id=first["report_id"], base_version_id=first["version_id"], body="版本二")
    second = signed.post(base(project) + "/reports", json=payload)
    assert second.status_code == 201 and second.json()["number"] == 2
    assert signed.post(base(project) + "/reports", json=payload).status_code == 409
    versions = signed.get(base(project) + f"/reports/{first['report_id']}/versions").json()["items"]
    assert [v["body"] for v in versions] == ["版本一", "版本二"]
    assert (
        signed.get(base(project) + f"/reports/{first['report_id']}/versions/{first['version_id']}/text").text
        == "版本一"
    )
    forged = {**payload, "sources": [{**source, "excerpt": "fabricated"}]}
    assert signed.post(base(project) + "/reports", json=forged).status_code == 502


def test_report_cross_project_id_denied(signed, project):
    payload = {"title": "报告", "research_question": "问题", "body": "内容", "sources": []}
    report = signed.post(base(project) + "/reports", json=payload).json()
    other = signed.post("/api/v1/projects", json={"name": "第二项目"}).json()["project_id"]
    assert signed.get(base(other) + f"/reports/{report['report_id']}/versions").status_code == 404


def test_remove_hides_paper_search_and_task_preserves_report(signed, project):
    task = signed.post(base(project) + "/answers", json={"question": "test"}).json()
    result = finished(signed, project, task["task_id"])["result"]
    version = signed.post(
        base(project) + "/reports",
        json={"title": "报告", "research_question": "问题", "body": "内容", "sources": result["sources"]},
    ).json()
    path = base(project) + f"/papers/{seed_id('paper-a')}"
    assert signed.delete(path).status_code == 409
    assert signed.delete(path + "?confirm_source_impact=true").status_code == 200
    assert signed.get(path).status_code == 404
    assert signed.post(base(project) + "/search", json={"query": "test"}).json()["items"] == []
    assert signed.get(base(project) + f"/tasks/{task['task_id']}").status_code == 404
    history = signed.get(base(project) + f"/reports/{version['report_id']}/versions").json()["items"]
    assert history[0]["sources"] == result["sources"]


@pytest.mark.parametrize(
    "question,decision,status",
    [
        ("按年份统计论文数量", "allowed", "executed"),
        ("不支持的问题", "rejected", "not_executed"),
        ("模拟执行失败", "undetermined", "failed"),
    ],
)
def test_gateway_states_and_audit(signed, project, question, decision, status):
    response = signed.post(base(project) + "/secure-queries", json={"question": question})
    assert response.status_code == 200
    result = response.json()
    assert (result["decision"], result["execution_status"]) == (decision, status)
    audits = signed.get(
        base(project) + "/audit-events", params={"decision": decision, "action": "secure_query.finished"}
    ).json()["items"]
    assert audits[0]["request_id"] == result["request_id"]


def test_gateway_down_has_no_fallback(app, signed, project, monkeypatch):
    calls = []
    monkeypatch.setattr(app.state.adapters.data, "controlled_statistics", lambda *a: calls.append(a))
    response = signed.post(base(project) + "/secure-queries", json={"question": "模拟网关故障"})
    assert response.status_code == 503 and response.json()["code"] == "GATEWAY_UNAVAILABLE"
    assert calls == []


def test_critical_audit_failure_prevents_gateway(app, signed, project, monkeypatch):
    calls = []

    async def gateway(*args):
        calls.append(args)

    def fail(*args):
        raise RuntimeError("private-storage-error")

    monkeypatch.setattr(app.state.adapters.security, "execute", gateway)
    monkeypatch.setattr(app.state.adapters.data, "append_audit", fail)
    response = signed.post(base(project) + "/secure-queries", json={"question": "按年份统计论文数量"})
    assert response.status_code == 503 and response.json()["code"] == "AUDIT_UNAVAILABLE"
    assert "private-storage-error" not in response.text and not calls


def test_audit_filter_time_validation(signed, project):
    assert (
        signed.get(base(project) + "/audit-events", params={"since": "2026-10-26T00:00:00"}).status_code
        == 422
    )
    assert signed.get(base(project) + "/audit-events", params={"limit": 101}).status_code == 422


def test_unexpected_storage_error_is_sanitized(app, signed, project, monkeypatch):
    def fail(*args):
        raise RuntimeError("PRIVATE_PROVIDER_DETAIL_DO_NOT_ECHO")

    monkeypatch.setattr(app.state.adapters.data, "papers", fail)
    response = signed.get(base(project) + "/papers")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "PRIVATE_PROVIDER_DETAIL_DO_NOT_ECHO" not in response.text
    assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_answer_outside_retrieved_citations_fails_task(app, signed, project, monkeypatch):
    from app.schemas import AnswerResult

    async def bad_answer(ctx, question, sources):
        changed = sources[0].model_copy(update={"citation_id": "99"})
        return AnswerResult(
            answer="unsupported", citation_ids=["99"], sources=[changed], insufficient_evidence=False
        )

    monkeypatch.setattr(app.state.adapters.ai, "answer", bad_answer)
    task = signed.post(base(project) + "/answers", json={"question": "test"}).json()
    done = finished(signed, project, task["task_id"])
    assert done["status"] == "failed" and done["error"]["code"] == "INVALID_CITATION"


def test_same_idempotency_key_different_payload_conflicts(signed, project):
    path = base(project) + "/answers"
    headers = {"Idempotency-Key": "same-key"}
    first = signed.post(path, json={"question": "first"}, headers=headers)
    assert first.status_code == 202
    replay = signed.post(path, json={"question": "first"}, headers=headers)
    assert replay.json()["task_id"] == first.json()["task_id"]
    assert signed.post(path, json={"question": "different"}, headers=headers).status_code == 409


def test_report_failure_does_not_emit_saved_audit(app, signed, project, monkeypatch):
    from app.errors import AppError

    def unavailable(*args):
        raise AppError(503, "DATA_UNAVAILABLE", "数据存储暂不可用")

    monkeypatch.setattr(app.state.adapters.data, "save_report", unavailable)
    response = signed.post(
        base(project) + "/reports",
        json={"title": "报告", "research_question": "问题", "body": "正文", "sources": []},
    )
    assert response.status_code == 503
    events = signed.get(base(project) + "/audit-events", params={"action": "report.saved"}).json()["items"]
    assert events == []


def test_post_commit_audit_failure_does_not_overwrite_completed_task(app, signed, project, monkeypatch):
    original = app.state.adapters.data.append_audit

    def append(event):
        if event.action == "answer.completed":
            raise RuntimeError("audit unavailable")
        return original(event)

    monkeypatch.setattr(app.state.adapters.data, "append_audit", append)
    response = signed.post(base(project) + "/answers", json={"question": "证据核验"})
    done = finished(signed, project, response.json()["task_id"])
    assert done["status"] == "succeeded" and done["error"] is None


def test_upload_unknown_commit_preserves_adopted_file_and_replays(app, signed, project, monkeypatch):
    from app.errors import AppError

    original = app.state.adapters.data.import_paper

    def uncertain(*args):
        original(*args)
        raise AppError(503, "DATA_COMMIT_UNKNOWN", "数据提交状态待确认")

    monkeypatch.setattr(app.state.adapters.data, "import_paper", uncertain)
    kwargs = {
        "files": {"file": ("uncertain.pdf", b"%PDF-1.4\nMock uncertain commit")},
        "data": {"title": "未知提交演示"},
        "headers": {"Idempotency-Key": "uncertain-import"},
    }
    failed = signed.post(base(project) + "/papers", **kwargs)
    assert failed.status_code == 503 and failed.json()["code"] == "DATA_COMMIT_UNKNOWN"
    paths = list(app.state.settings.storage_dir.glob("*.pdf"))
    assert len(paths) == 1
    monkeypatch.setattr(app.state.adapters.data, "import_paper", original)
    replay = signed.post(base(project) + "/papers", **kwargs)
    assert replay.status_code == 202
    assert paths[0].stem == replay.json()["paper"]["paper_id"]
    assert list(app.state.settings.storage_dir.glob("*.pdf")) == paths


@pytest.mark.parametrize("kind", ["opaque_exception", "unclassified_app_error"])
def test_upload_unclassified_commit_error_preserves_file(app, signed, project, monkeypatch, kind):
    from app.errors import AppError

    original = app.state.adapters.data.import_paper

    def uncertain(*args):
        original(*args)
        if kind == "unclassified_app_error":
            raise AppError(503, "PROVIDER_TIMEOUT", "PRIVATE_COMMIT_DETAIL")
        raise RuntimeError("PRIVATE_COMMIT_DETAIL")

    monkeypatch.setattr(app.state.adapters.data, "import_paper", uncertain)
    response = signed.post(
        base(project) + "/papers", files={"file": ("a.pdf", b"%PDF-1.4\nOpaque")}, data={"title": "不明提交"}
    )
    assert response.status_code == 503 and response.json()["code"] == "DATA_COMMIT_UNKNOWN"
    assert "PRIVATE_COMMIT_DETAIL" not in response.text
    assert len(list(app.state.settings.storage_dir.glob("*.pdf"))) == 1


def test_upload_confirmed_rollback_cleans_only_own_file(app, signed, project, monkeypatch):
    from app.errors import AppError

    root = app.state.settings.storage_dir
    root.mkdir(parents=True)
    previous = root / "previous.pdf"
    previous.write_bytes(b"previous upload")

    def rolled_back(*args):
        raise AppError(503, "DATA_UNAVAILABLE", "confirmed rollback")

    monkeypatch.setattr(app.state.adapters.data, "import_paper", rolled_back)
    response = signed.post(
        base(project) + "/papers", files={"file": ("a.pdf", b"%PDF-1.4\nRollback")}, data={"title": "回滚"}
    )
    assert response.status_code == 503 and response.json()["code"] == "DATA_UNAVAILABLE"
    assert list(root.glob("*.pdf")) == [previous]
    assert previous.read_bytes() == b"previous upload"


def test_upload_file_creation_conflict_preserves_preexisting_file(app, signed, project, monkeypatch):
    from app.routes import literature

    candidate = uuid4()
    root = app.state.settings.storage_dir
    root.mkdir(parents=True)
    previous = root / f"{candidate}.pdf"
    previous.write_bytes(b"previous adopted file")
    imports = []
    monkeypatch.setattr(literature, "uuid4", lambda: candidate)
    monkeypatch.setattr(app.state.adapters.data, "import_paper", lambda *args: imports.append(args))
    response = signed.post(
        base(project) + "/papers",
        files={"file": ("a.pdf", b"%PDF-1.4\nConflict")},
        data={"title": "文件冲突"},
    )
    assert response.status_code == 503 and response.json()["code"] == "FILE_STORAGE_UNAVAILABLE"
    assert not imports and previous.read_bytes() == b"previous adopted file"


def test_upload_partial_file_failure_never_calls_import(app, signed, project, monkeypatch):
    from pathlib import Path

    real_open = Path.open

    class BrokenWrite:
        def __init__(self, path):
            self.stream = real_open(path, "xb")

        def __enter__(self):
            return self

        def write(self, value):
            self.stream.write(value[:4])
            raise OSError("PRIVATE_DISK_DETAIL")

        def __exit__(self, *args):
            self.stream.close()

    def open_file(path, *args, **kwargs):
        if args and args[0] == "xb" and path.suffix == ".pdf":
            return BrokenWrite(path)
        return real_open(path, *args, **kwargs)

    imports = []
    monkeypatch.setattr(Path, "open", open_file)
    monkeypatch.setattr(app.state.adapters.data, "import_paper", lambda *args: imports.append(args))
    response = signed.post(
        base(project) + "/papers", files={"file": ("a.pdf", b"%PDF-1.4\nPartial")}, data={"title": "部分文件"}
    )
    assert response.status_code == 503 and response.json()["code"] == "FILE_STORAGE_UNAVAILABLE"
    assert "PRIVATE_DISK_DETAIL" not in response.text
    assert not imports and list(app.state.settings.storage_dir.glob("*.pdf")) == []
