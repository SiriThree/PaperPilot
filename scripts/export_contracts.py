"""Export the running app's contract and actual Mock examples; no credential output."""

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402
from app.adapters.mock_data import seed_id  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas import SecurityExperimentState  # noqa: E402


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export(check=False):
    with tempfile.TemporaryDirectory() as temp:
        app = create_app(Settings(environment="test", storage_dir=Path(temp)))
        spec = app.openapi()
        target = ROOT / "contracts/openapi.json"
        if check:
            if json.loads(target.read_text(encoding="utf-8")) != spec:
                raise SystemExit("OpenAPI is stale; regenerate contracts")
            print("OpenAPI matches application schemas")
            return
        write_json(target, spec)
        with (
            TestClient(
                app,
                base_url="http://127.0.0.1",
                client=("127.0.0.1", 50000),
                headers={"X-PaperPilot-Client": "web"},
            ) as client,
            TestClient(app, headers={"X-PaperPilot-Client": "web"}) as anonymous,
        ):

            def capture(method, template, actual=None, **kwargs):
                actual = actual or template
                response = client.request(method.upper(), actual, **kwargs)
                assert response.status_code < 300, (method, template, response.status_code, response.text)
                failure_kwargs = dict(kwargs)
                failure_kwargs.pop("headers", None)
                if template in {"/api/v1/health", "/api/v1/auth/csrf"}:
                    failure_kwargs["headers"] = {"Content-Length": "invalid"}
                elif template == "/api/v1/auth/login":
                    failure_kwargs["json"] = {"username": "unknown"}
                failure = anonymous.request(method.upper(), actual, **failure_kwargs)
                assert failure.status_code >= 400, (method, template)
                operation = spec["paths"][template][method]["operationId"]

                def envelope(r, request_kwargs):
                    content_type = r.headers["content-type"].split(";")[0]
                    request = {"path": actual, "headers": {"X-PaperPilot-Client": "web"}}
                    if "json" in request_kwargs:
                        request["body"] = request_kwargs["json"]
                    if "files" in request_kwargs:
                        request["multipart"] = {
                            **request_kwargs.get("data", {}),
                            "file": "<PDF binary; not embedded>",
                        }
                    body = r.json() if content_type == "application/json" else r.text
                    if template == "/api/v1/auth/csrf" and r.status_code == 200:
                        body = {"csrf_token": "synthetic-example-token"}
                    return {
                        "request": request,
                        "response": {
                            "status": r.status_code,
                            "content_type": content_type,
                            "body": body,
                        },
                    }

                write_json(
                    ROOT / f"contracts/examples/{operation}.json",
                    {
                        "method": method,
                        "path_template": template,
                        "mock": True,
                        "success": envelope(response, kwargs),
                        "error": envelope(failure, failure_kwargs),
                    },
                )
                return response

            def wait(project, task):
                for _ in range(100):
                    state = client.get(f"/api/v1/projects/{project}/tasks/{task}").json()
                    if state["status"] in {"succeeded", "failed"}:
                        return state
                    time.sleep(0.02)
                raise RuntimeError("Mock task timed out")

            capture("get", "/api/v1/health")
            capture("get", "/api/v1/auth/csrf")
            capture("post", "/api/v1/auth/login", json={"username": "alice"})
            capture("get", "/api/v1/auth/me")
            project = capture("get", "/api/v1/projects").json()["items"][0]["project_id"]
            capture("post", "/api/v1/projects", json={"name": "契约演示项目", "description": "合成数据"})
            prefix, actual = "/api/v1/projects/{project_id}", f"/api/v1/projects/{project}"
            capture("get", prefix + "/security-experiments", actual + "/security-experiments")
            # Contract-only synthetic accepted task; this exporter never launches Docker.
            app.state.security_experiments = SimpleNamespace(
                start=lambda ctx: SecurityExperimentState(
                    enabled=True,
                    busy=True,
                    run_id=uuid4(),
                    status="running",
                )
            )
            try:
                capture(
                    "post",
                    prefix + "/security-experiments",
                    actual + "/security-experiments",
                    json={},
                    headers={"Origin": "http://127.0.0.1:5173"},
                )
            finally:
                app.state.security_experiments = None
            capture("get", prefix + "/members", actual + "/members")
            capture("post", prefix + "/members", actual + "/members", json={"username": "bob"})
            capture("delete", prefix + "/members/{user_id}", actual + f"/members/{seed_id('bob')}")
            capture("get", prefix + "/papers", actual + "/papers")
            paper = str(seed_id("paper-a"))
            source = capture("get", prefix + "/papers/{paper_id}", actual + f"/papers/{paper}").json()[
                "sources"
            ][0]
            upload = capture(
                "post",
                prefix + "/papers",
                actual + "/papers",
                files={"file": ("synthetic.pdf", b"%PDF-1.4\nMock fixture")},
                data={"title": "Mock 上传示例"},
            )
            tid = upload.json()["task"]["task_id"]
            wait(project, tid)
            capture("get", prefix + "/tasks/{task_id}", actual + f"/tasks/{tid}")
            capture(
                "post",
                prefix + "/search",
                actual + "/search",
                json={"query": "证据核验", "mode": "hybrid", "limit": 10},
            )
            answer = capture(
                "post", prefix + "/answers", actual + "/answers", json={"question": "证据核验"}
            ).json()
            wait(project, answer["task_id"])
            extracted = capture(
                "post", prefix + "/papers/{paper_id}/evidence", actual + f"/papers/{paper}/evidence"
            ).json()
            wait(project, extracted["task_id"])
            capture("get", prefix + "/papers/{paper_id}/evidence", actual + f"/papers/{paper}/evidence")
            capture(
                "post", prefix + "/report-drafts", actual + "/report-drafts", json={"question": "文献综述"}
            )
            bad = client.post(actual + "/answers", json={"question": "模拟失败"}).json()
            assert wait(project, bad["task_id"])["status"] == "failed"
            capture("post", prefix + "/tasks/{task_id}/retry", actual + f"/tasks/{bad['task_id']}/retry")
            version = capture(
                "post",
                prefix + "/reports",
                actual + "/reports",
                json={
                    "title": "Mock 综述",
                    "research_question": "来源核验",
                    "body": "合成报告内容 [1]",
                    "sources": [source],
                },
            ).json()
            capture("get", prefix + "/reports", actual + "/reports")
            capture(
                "get",
                prefix + "/reports/{report_id}/versions",
                actual + f"/reports/{version['report_id']}/versions",
            )
            capture(
                "get",
                prefix + "/reports/{report_id}/versions/{version_id}/text",
                actual + f"/reports/{version['report_id']}/versions/{version['version_id']}/text",
            )
            capture(
                "post",
                prefix + "/secure-queries",
                actual + "/secure-queries",
                json={"question": "按年份统计论文数量"},
            )
            for label, question in (
                ("rejected", "不支持的问题"),
                ("failed", "模拟执行失败"),
                ("unavailable", "模拟网关故障"),
            ):
                response = client.post(actual + "/secure-queries", json={"question": question})
                write_json(
                    ROOT / f"contracts/scenarios/gateway-{label}.json",
                    {
                        "mock": True,
                        "request": {"question": question},
                        "status": response.status_code,
                        "body": response.json(),
                    },
                )
            empty = client.post(actual + "/answers", json={"question": "证据不足"}).json()
            write_json(
                ROOT / "contracts/scenarios/insufficient-evidence.json", wait(project, empty["task_id"])
            )
            capture("get", prefix + "/audit-events", actual + "/audit-events")
            capture(
                "delete",
                prefix + "/papers/{paper_id}",
                actual + f"/papers/{paper}?confirm_source_impact=true",
            )
            capture("post", "/api/v1/auth/logout")
        print(
            f"Exported OpenAPI and {sum(len(v) for v in spec['paths'].values())} operation examples (synthetic only)"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    export(parser.parse_args().check)
