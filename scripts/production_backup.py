"""Back up the owned deployment and restore into a network-isolated disposable PostgreSQL."""

from datetime import UTC, datetime
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tarfile
import time
from uuid import uuid4

from production_deployment import ROOT, STATE, compose, metadata, run, service_id


def psql(container, statement):
    return run(
        [
            "docker",
            "exec",
            "-u",
            "postgres",
            container,
            "psql",
            "-X",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            "paperpilot_production",
            "-c",
            statement,
        ],
        capture=True,
        text=True,
    ).strip()


def snapshot(container):
    names = json.loads(
        psql(
            container,
            "SELECT json_agg(json_build_array(schemaname,tablename) ORDER BY schemaname,tablename) FROM pg_tables WHERE schemaname IN ('public','paperpilot_security')",
        )
    )

    def quote(value):
        return '"' + value.replace('"', '""') + '"'

    counts = {}
    for schema, table in names:
        counts[schema + "." + table] = int(
            psql(container, "SELECT count(*) FROM " + quote(schema) + "." + quote(table))
        )
    migrations = psql(
        container,
        "SELECT md5(string_agg(version::text||checksum,',' ORDER BY version)) FROM schema_migrations",
    )
    return {"counts": counts, "migration_fingerprint": migrations}


def main():
    meta = metadata()
    backup = STATE / "backups" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8])
    backup.mkdir()
    original = service_id("postgres")
    proxy = service_id("proxy")
    restore = None
    nonce = uuid4().hex
    name = "paperpilot-production-restore-" + nonce[:12]
    compose("stop", "proxy", "backend", capture=True)
    try:
        before = snapshot(original)
        with (backup / "database.dump").open("wb") as stream:
            run(
                [
                    "docker",
                    "exec",
                    "-u",
                    "postgres",
                    original,
                    "pg_dump",
                    "-U",
                    "postgres",
                    "-d",
                    "paperpilot_production",
                    "-Fc",
                    "--create",
                ],
                stdout=stream,
            )
        roles = run(
            ["docker", "exec", "-u", "postgres", original, "pg_dumpall", "-U", "postgres", "--roles-only"],
            capture=True,
            text=True,
        )
        (backup / "roles.sql").write_text(roles, encoding="utf-8")
        for file in (
            "deployment.json",
            "postgres-admin-password",
            "owner-credentials.txt",
            "acceptance-secrets.json",
            "local-ca.crt",
            "compose.env",
        ):
            shutil.copy2(STATE / file, backup / file)
        if (STATE / "sql-model.json").is_file():
            shutil.copy2(STATE / "sql-model.json", backup / "sql-model.json")
        if (STATE / "research-model.json").is_file():
            shutil.copy2(STATE / "research-model.json", backup / "research-model.json")
            shutil.copytree(STATE / "embedding-model", backup / "embedding-model")
        with (backup / "uploads.tar.gz").open("wb") as stream:
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    "0:0",
                    "--mount",
                    "type=volume,src=paperpilot-production_production-uploads,dst=/uploads,readonly",
                    meta["images"]["BACKEND_IMAGE"],
                    "python",
                    "-c",
                    "import tarfile,sys; a=tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz'); a.add('/uploads',arcname='uploads'); a.close()",
                ],
                stdout=stream,
            )
        upload_digests = {}
        restored_files = backup / "restored-files"
        restored_files.mkdir()
        with tarfile.open(backup / "uploads.tar.gz") as archive:
            for member in archive:
                if member.isdir():
                    continue
                if (
                    not member.isfile()
                    or not member.name.startswith("uploads/")
                    or ".." in member.name.split("/")
                ):
                    raise RuntimeError("Unexpected upload archive entry")
                with archive.extractfile(member) as stream:
                    upload_digests[member.name] = hashlib.file_digest(stream, "sha256").hexdigest()
            archive.extractall(restored_files, filter="data")
        for upload_name, expected in upload_digests.items():
            with (restored_files / upload_name).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise RuntimeError("Restored upload differs from its archive")
        research = None
        if (STATE / "literature/corpus-state.json").is_file():
            shutil.copy2(STATE / "literature/corpus-state.json", backup / "literature-state.json")
            literature = json.loads((backup / "literature-state.json").read_text(encoding="utf-8"))
            for project in literature["projects"].values():
                for paper in project["papers"].values():
                    if upload_digests.get("uploads/" + paper["paper_id"] + ".pdf") != paper["sha256"]:
                        raise RuntimeError("Uploaded PDF differs from its pinned corpus digest")
            project = literature["projects"]["rag-evidence"]
            research = {"project": project["project_id"], "paper": project["papers"]["rag"]}
        runtime_volume = "paperpilot-production_runtime-secrets"
        secret = run(
            [
                "docker",
                "run",
                "--rm",
                "--user",
                "0:0",
                "--mount",
                f"type=volume,src={runtime_volume},dst=/runtime,readonly",
                meta["images"]["BACKEND_IMAGE"],
                "python",
                "-c",
                "from pathlib import Path; import sys; sys.stdout.buffer.write(Path('/runtime/runtime.json').read_bytes())",
            ],
            capture=True,
        )
        (backup / "runtime.json").write_bytes(secret)
        run(["docker", "cp", proxy + ":/data/caddy", str(backup / "caddy")])
        identifier = run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--network",
                "none",
                "--name",
                name,
                "--label",
                "org.paperpilot.restore=" + nonce,
                "--env",
                "POSTGRES_PASSWORD",
                "--env",
                "POSTGRES_DB=paperpilot_production",
                meta["images"]["POSTGRES_IMAGE"],
            ],
            capture=True,
            text=True,
            env={**os.environ, "POSTGRES_PASSWORD": secrets.token_urlsafe(40)},
        ).strip()
        if len(identifier) != 64 or any(c not in "0123456789abcdef" for c in identifier):
            raise RuntimeError("Invalid restore container identity")
        restore = identifier
        label = run(
            ["docker", "inspect", restore, "--format", '{{index .Config.Labels "org.paperpilot.restore"}}'],
            capture=True,
            text=True,
        ).strip()
        if label != nonce:
            raise RuntimeError("Restore ownership mismatch; restore refused")
        for _ in range(100):
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-u",
                    "postgres",
                    restore,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "postgres",
                    "-d",
                    "paperpilot_production",
                ],
                capture_output=True,
            )
            if probe.returncode == 0:
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Restore database did not become ready")
        # Retain service password verifiers; the disposable restore administrator has its own password.
        restore_roles = "\n".join(
            line
            for line in roles.splitlines()
            if not line.startswith(("CREATE ROLE postgres;", "ALTER ROLE postgres "))
        )
        run(
            [
                "docker",
                "exec",
                "-i",
                "-u",
                "postgres",
                restore,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "postgres",
                "-d",
                "paperpilot_production",
            ],
            input=restore_roles.encode(),
            capture=True,
        )
        with (backup / "database.dump").open("rb") as stream:
            run(
                [
                    "docker",
                    "exec",
                    "-i",
                    "-u",
                    "postgres",
                    restore,
                    "pg_restore",
                    "--create",
                    "--clean",
                    "--if-exists",
                    "--exit-on-error",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                ],
                stdin=stream,
                capture=True,
            )
        psql(
            restore,
            "UPDATE paperpilot_security.service_logins s SET role_oid=r.oid FROM pg_roles r WHERE s.login_role=r.rolname",
        )
        restored = snapshot(restore)
        if restored != before:
            raise RuntimeError("Restored row counts or migration fingerprint differ")
        verification = r"""
import asyncio,json,sys
from uuid import UUID,uuid4
from app.production import runtime_config,verify_outbox
from app.data.database import ConnectionConfig,PostgresDatabase
from app.data.adapter import PostgresData
from app.data.settings import DataSettings
from app.data.authentication_boundary import AuthenticationBoundary
from app.data.business_boundary import verify_business_privileges
from app.data.audit_boundary import verify_audit_boundary
from app.security.audit import compose_audit
from app.security.adapter import build_security
from app.security.executor import ReaderConfig
from app.adapters.ports import Context
c=runtime_config(); info=json.loads(sys.stdin.read())
if getattr(c,'research_model',None) is not None: c.research_model.verify_files()
def config(login): return ConnectionConfig('127.0.0.1',5432,login.user,login.password.get_secret_value(),c.dbname)
b=PostgresDatabase(config(c.business)); a=PostgresDatabase(config(c.authentication)); outbox=PostgresDatabase(config(c.outbox))
try:
 AuthenticationBoundary(b,a,trusted_lock_owner=c.auth_lock_owner).ready()
 with b.transaction() as conn:
  verify_business_privileges(conn,project_audit_read=True,**({'indexed_ai':True} if getattr(c,'research_model',None) is not None else {}))
  verify_audit_boundary(conn,trusted_owner=c.audit_owner)
 with outbox.transaction() as conn: verify_outbox(conn,c.outbox.user)
 writer=compose_audit(config(c.audit))
 try:
  readers={v.project_id:ReaderConfig('127.0.0.1',5432,c.dbname,v.user,v.password.get_secret_value()) for v in c.readers}
  gateway=build_security(PostgresData(b,DataSettings()),readers=readers,audit_append=writer.append)
  result=asyncio.run(gateway.execute(Context(UUID(info['user']),UUID(info['project']),uuid4()),'按年份统计论文数量'))
  assert result.execution_status=='executed' and not result.mock
  if info.get('research'):
   import hashlib
   from pathlib import Path
   from app.ai.adapter import RealAI
   from app.ai.files import LocalPDFReader
   from app.adapters.ports import FileReference
   from app.schemas import SearchRequest
   details=info['research']; context=Context(UUID(info['user']),UUID(details['project']),uuid4())
   pdf_reader=LocalPDFReader(Path('/uploads'))
   pdf=pdf_reader._read(FileReference(details['paper']['paper_id']+'.pdf',UUID(details['paper']['paper_id']),context.project_id))
   assert hashlib.sha256(pdf).hexdigest()==details['paper']['sha256']
   ai=RealAI(PostgresData(b,DataSettings()),c.research_model.settings(),reader=pdf_reader)
   for mode in ('keyword','semantic'):
    hits=asyncio.run(ai.search(context,SearchRequest(query='retrieval augmented generation',mode=mode,limit=3)))
    assert hits.items and not hits.mock and all(not h.source.synthetic for h in hits.items)
 finally: writer.close()
 print('Restored native security query, authentication and audit preflights passed')
finally:
 b.close(); a.close(); outbox.close()
"""
        run(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--network",
                "container:" + restore,
                "--mount",
                f"type=volume,src={runtime_volume},dst=/run/paperpilot,readonly",
                "--mount",
                f"type=bind,src={restored_files / 'uploads'},dst=/uploads,readonly",
                *(
                    [
                        "--mount",
                        f"type=bind,src={backup / 'embedding-model'},dst=/opt/paperpilot-model,readonly",
                    ]
                    if (backup / "embedding-model").is_dir()
                    else []
                ),
                "--env",
                "PAPERPILOT_PRODUCTION_CONFIG=/run/paperpilot/runtime.json",
                meta["images"]["BACKEND_IMAGE"],
                "python",
                "-c",
                verification,
            ],
            capture=True,
            input=json.dumps(
                {
                    "user": meta["users"]["owner"]["user_id"],
                    "project": meta["projects"]["accept_a"],
                    "research": research,
                }
            ).encode(),
        )
        digests = {
            str(path.relative_to(backup)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in backup.rglob("*")
            if path.is_file()
        }
        report = {
            "deployment_id": meta["deployment_id"],
            "source_fingerprint": meta["source_fingerprint"],
            "completed_at": datetime.now(UTC).isoformat(),
            "backup": str(backup.relative_to(ROOT)),
            "restored_snapshot_matches": True,
            "native_security_and_auth_after_restore": True,
            "files_sha256": digests,
            "snapshot": before,
            "upload_files_sha256": upload_digests,
            "upload_files": len(upload_digests),
            "research_retrieval_after_restore": research is not None,
        }
        (backup / "manifest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (STATE / "evidence/backup-restore.json").write_text(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"files_sha256", "snapshot"}},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Protected backup and isolated restore verification passed:", backup)
    finally:
        try:
            if restore:
                label = run(
                    [
                        "docker",
                        "inspect",
                        restore,
                        "--format",
                        '{{index .Config.Labels "org.paperpilot.restore"}}',
                    ],
                    capture=True,
                    text=True,
                ).strip()
                if label != nonce:
                    raise RuntimeError("Restore ownership changed; cleanup refused")
                run(["docker", "rm", "--force", "--volumes", restore], capture=True)
        finally:
            compose("up", "-d", "--wait", "backend", "proxy", "--wait-timeout", "150", capture=True)


if __name__ == "__main__":
    main()
