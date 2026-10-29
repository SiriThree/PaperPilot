"""Check owned HTTPS deployment, real audited SQL and cookie persistence across a web restart."""

import json
import ssl
import sys

import httpx

from local_deployment import STATE, configuration, start, stop, write_json


def main():
    config = configuration()
    report = {"scope": "owned local deployment; not a live-model acceptance"}
    context = ssl.create_default_context(cafile=str(STATE / "tls/localhost.crt"))
    with httpx.Client(
        base_url="https://localhost:8443",
        verify=context,
        trust_env=False,
        timeout=10,
        headers={"X-PaperPilot-Client": "web", "Origin": "https://localhost:8443"},
    ) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        report["modes"] = response.json()["modes"]
        assert "authentication_credentials" in response.json()["limitations"]
        client.headers["X-CSRF-Token"] = client.get("/api/v1/auth/csrf").json()["csrf_token"]
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 401
        body = {"username": "owner", "password": (STATE / "secrets/owner-password").read_text().strip()}
        login = client.post("/api/v1/auth/login", json=body)
        assert login.status_code == 200 and login.json()["authentication"] == "authentication_credentials"
        assert "Secure" in login.headers["set-cookie"] and "HttpOnly" in login.headers["set-cookie"]
        body.clear()
        report["password_cookie"] = True
        project = config["project_id"]
        query = client.post(
            f"/api/v1/projects/{project}/secure-queries", json={"question": "按年份统计论文数量"}
        )
        assert query.status_code == 200
        result = query.json()
        assert (
            result["decision"] == "allowed"
            and result["execution_status"] == "executed"
            and result["mock"] is False
        )
        assert len(result["rows"]) <= 100
        audit = client.get(f"/api/v1/projects/{project}/audit-events", params={"limit": 100}).json()
        actions = {event["action"] for event in audit["items"] if event["request_id"] == result["request_id"]}
        assert {"security.query.prepared", "security.query.finished"}.issubset(actions)
        report["restricted_sql_and_confirmed_audit"] = True
        stop()
        start()
        assert client.get("/api/v1/auth/me").status_code == 200
        report["session_survives_web_restart"] = True
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.get("/api/v1/auth/me").status_code == 401
        report["logout_revokes_session"] = True
        assert client.get("/api/v1/nonexistent").status_code == 404
        assert "<!doctype html>" in client.get(f"/projects/{project}/security").text.lower()
        report["frontend_history_and_api_boundaries"] = True
    write_json(STATE / "acceptance.json", report)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(
            f"Local acceptance failed ({type(error).__name__}); credentials and cookies suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
