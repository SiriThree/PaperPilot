"""Opt-in clean Linux image: actual native D readers/audit, no source or data bind mounts."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.security.provision import create_login, install
from security_contracts.postgres_provider import postgres_workspace

pytestmark = [
    pytest.mark.real_database,
    pytest.mark.skipif(
        not os.getenv("PAPERPILOT_SECURITY_CONTAINER_IMAGE"),
        reason="Explicit prebuilt clean security image required",
    ),
]

# This probe runs inside the image and returns only bounded verification metadata.
PROBE = r"""
import json,sys,httpx
mode,first,second,request_id=sys.argv[1:]
with httpx.Client(base_url='http://127.0.0.1:8000',headers={'X-PaperPilot-Client':'web'},timeout=10) as c:
    assert c.get('/api/v1/health').json()['modes']=={'a':'persistent','c':'mock','d':'real'}
    assert c.post('/api/v1/auth/login',json={'username':'alice'}).status_code==200
    base='/api/v1/projects/'+first
    if mode=='fault':
        response=c.post(base+'/secure-queries',json={'question':'按年份统计论文数量'},headers={'X-Request-ID':request_id})
        assert response.status_code==503 and response.json()['code']=='AUDIT_UNAVAILABLE' and 'rows' not in response.json()
        print(json.dumps({'audit_fault_closed':True}));sys.exit()
    if mode=='restart':
        events=c.get(base+'/audit-events',params={'limit':100}).json()['items']
        assert any(e['request_id']==request_id and e['action']=='security.query.finished' and e['details']['sql'] for e in events)
        print(json.dumps({'audit_survived_restart':True}));sys.exit()
    response=c.post(base+'/secure-queries',json={'question':'按年份统计论文数量'},headers={'X-Request-ID':request_id})
    result=response.json();assert response.status_code==200 and result['execution_status']=='executed' and not result['mock']
    assert result['rows'] and not any(row['year']==2025 for row in result['rows'])
    rejected=c.post(base+'/secure-queries',json={'question':'不支持的问题'}).json()
    assert rejected['decision']=='rejected' and rejected['execution_status']=='not_executed' and rejected['rows']==[]
    events=c.get(base+'/audit-events',params={'limit':100}).json()['items']
    assert any(e['request_id']==request_id and e['action']=='security.query.finished' and e['details']['sql'] for e in events)
    assert c.post('/api/v1/auth/login',json={'username':'bob'}).status_code==200
    assert c.get(base+'/audit-events').status_code==403
    assert c.post('/api/v1/projects/'+second+'/secure-queries',json={'question':'按年份统计论文数量'}).status_code==404
    assert c.post('/api/v1/auth/login',json={'username':'eve'}).status_code==200
    assert c.post(base+'/secure-queries',json={'question':'按年份统计论文数量'}).status_code==404
    print(json.dumps({'real_query':True,'scope_isolated':True,'unsupported_rejected':True,'audit_details':True,'member_audit_denied':True,'nonmember_denied':True}))
"""


def test_native_security_image_query_isolation_audit_fault_and_restart(tmp_path):
    image = os.environ["PAPERPILOT_SECURITY_CONTAINER_IMAGE"]
    image_id = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image], text=True
    ).strip()
    child = None
    with postgres_workspace() as pg:
        owner = pg.contexts[0]
        pg.data.add_member(owner, "bob")
        environment = os.environ.copy()
        configuration = {
            "database": {"host": "127.0.0.1", "port": 5432, "dbname": pg.config.dbname},
            "readers": [],
        }
        with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
            install(admin, existing_views=True)
            business, business_password = "pp_business_" + uuid4().hex, secrets.token_urlsafe(32)
            audit, audit_password = "pp_audit_" + uuid4().hex, secrets.token_urlsafe(32)
            create_login(admin, business, business_password, business=True)
            create_login(admin, audit, audit_password)
            configuration["audit"] = {"user": audit, "password_env": "CLEAN_AUDIT_PASSWORD"}
            environment["CLEAN_AUDIT_PASSWORD"] = audit_password
            for index, ctx in enumerate(pg.contexts):
                reader, password = "pp_reader_" + uuid4().hex, secrets.token_urlsafe(32)
                create_login(admin, reader, password, project_id=ctx.project_id)
                name = f"CLEAN_READER_{index}_PASSWORD"
                environment[name] = password
                configuration["readers"].append(
                    {"project_id": str(ctx.project_id), "user": reader, "password_env": name}
                )
        config_path = tmp_path / "security.json"
        config_path.write_text(json.dumps(configuration), encoding="utf-8")
        variables = {
            "PAPERPILOT_ENV": "test",
            "PAPERPILOT_AUTH_MODE": "development",
            "PAPERPILOT_A_MODE": "persistent",
            "PAPERPILOT_C_MODE": "mock",
            "PAPERPILOT_D_MODE": "real",
            "PAPERPILOT_DATA_HOST": "127.0.0.1",
            "PAPERPILOT_DATA_PORT": "5432",
            "PAPERPILOT_DATA_DBNAME": pg.config.dbname,
            "PAPERPILOT_DATA_USER": business,
            "PAPERPILOT_DATA_PASSWORD": business_password,
            "PAPERPILOT_SECURITY_CONFIG": "/tmp/security.json",
            "PAPERPILOT_STORAGE_DIR": "/tmp/uploads",
            "PAPERPILOT_ALLOWED_ORIGINS": "http://localhost:8000",
        }
        environment.update(variables)
        names = [*variables, "CLEAN_AUDIT_PASSWORD", "CLEAN_READER_0_PASSWORD", "CLEAN_READER_1_PASSWORD"]
        command = ["docker", "create", "--network", "container:" + pg.container_id]
        for name in names:
            command.extend(["--env", name])
        command.append(image_id)

        def probe(mode, request_id):
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    child,
                    "python",
                    "-c",
                    PROBE,
                    mode,
                    str(owner.project_id),
                    str(pg.contexts[1].project_id),
                    str(request_id),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, "Clean image API probe failed (no private diagnostics emitted)"
            return json.loads(result.stdout)

        def ready():
            for _ in range(50):
                response = subprocess.run(
                    [
                        "docker",
                        "exec",
                        child,
                        "python",
                        "-c",
                        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=1)",
                    ],
                    capture_output=True,
                    timeout=5,
                )
                if response.returncode == 0:
                    return
                time.sleep(0.1)
            pytest.fail("Clean image did not become ready; private startup logs retained by Docker")

        try:
            result = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, "Clean image creation failed"
            child = result.stdout.strip()
            subprocess.run(
                ["docker", "cp", str(config_path), child + ":/tmp/security.json"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            subprocess.run(["docker", "start", child], check=True, capture_output=True, timeout=30)
            ready()
            request_id = uuid4()
            results = probe("normal", request_id)
            with psycopg.connect(**pg.config.conninfo(), autocommit=True) as admin:
                admin.execute(sql.SQL("GRANT SELECT ON public.papers TO {}").format(sql.Identifier(audit)))
                try:
                    results.update(probe("fault", uuid4()))
                finally:
                    admin.execute(
                        sql.SQL("REVOKE SELECT ON public.papers FROM {}").format(sql.Identifier(audit))
                    )
            results.update(probe("normal", uuid4()))
            subprocess.run(
                ["docker", "restart", "--time", "5", child], check=True, capture_output=True, timeout=30
            )
            ready()
            results.update(probe("restart", request_id))
            report = {
                "generated_at": datetime.now(UTC).isoformat(),
                "image": image_id,
                "scope": "clean_linux_image_native_D_owned_postgres",
                "authentication": "development_identity_only_not_password_acceptance",
                "host_sources_mounted": False,
                "results": results,
            }
            target = Path(__file__).resolve().parents[2] / ".runtime/clean-security-container.json"
            target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        finally:
            if child:
                subprocess.run(["docker", "rm", "--force", child], capture_output=True, timeout=30)
