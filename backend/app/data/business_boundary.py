"""Fail-closed candidate grant profile for the existing B business adapter.

This checks operation-level PostgreSQL privileges, not project-row isolation.
Use only with separately reviewed, explicitly isolated deployment fixtures.
"""

# Reviewed against PostgresData and its worker. Outbox delivery has its own
# identity and must not gain UPDATE/DELETE through this business profile.
BUSINESS_TABLE_PRIVILEGES = {
    "schema_migrations": {"SELECT"},
    "projects": {"SELECT", "INSERT"},
    "project_members": {"SELECT", "INSERT", "DELETE"},
    "papers": {"SELECT", "INSERT"},
    "project_papers": {"SELECT", "INSERT", "UPDATE"},
    "topics": {"SELECT"},
    "paper_topics": {"SELECT"},
    "chunks": {"SELECT", "INSERT"},
    "tasks": {"SELECT", "INSERT", "UPDATE"},
    "task_papers": {"SELECT", "INSERT"},
    "task_attempts": {"SELECT", "INSERT", "UPDATE"},
    "task_results": {"SELECT", "INSERT"},
    "evidence_fields": {"SELECT", "INSERT"},
    "result_sources": {"SELECT", "INSERT"},
    "reports": {"SELECT", "INSERT", "UPDATE"},
    "report_versions": {"SELECT", "INSERT"},
    "report_version_sources": {"SELECT", "INSERT"},
    "idempotency_records": {"SELECT", "INSERT"},
    "outbox_events": {"INSERT"},
    "audit_events": {"INSERT"},
}
_ALLOWED_USER_COLUMNS = {"user_id", "username", "display_name"}


def verify_business_privileges(connection):
    """Check effective grants, including column and inherited/PUBLIC grants."""
    identity = connection.execute(
        "SELECT current_user=session_user AS identity, rolcanlogin AND NOT "
        "(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication OR rolinherit) AS role_ok, "
        "NOT EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) AS no_membership, "
        "NOT has_database_privilege(current_user,current_database(),'CREATE,TEMP') AS database_ok, "
        "NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE left(n.nspname,3)<>'pg_' "
        "AND n.nspname<>'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE')) AS schemas_ok "
        "FROM pg_roles r WHERE r.rolname=current_user"
    ).fetchone()
    if not identity or not all(identity.values()):
        raise PermissionError("Business identity invalid")
    rows = connection.execute(
        "SELECT n.nspname,c.relname,c.relkind, "
        "has_table_privilege(current_user,c.oid,'SELECT') AS can_select, "
        "has_table_privilege(current_user,c.oid,'INSERT') AS can_insert, "
        "has_table_privilege(current_user,c.oid,'UPDATE') AS can_update, "
        "has_table_privilege(current_user,c.oid,'DELETE') AS can_delete, "
        "has_table_privilege(current_user,c.oid,'TRUNCATE,REFERENCES,TRIGGER') AS dangerous, "
        "has_any_column_privilege(current_user,c.oid,'SELECT') AS column_select, "
        "has_any_column_privilege(current_user,c.oid,'INSERT') AS column_insert, "
        "has_any_column_privilege(current_user,c.oid,'UPDATE') AS column_update, "
        "has_any_column_privilege(current_user,c.oid,'REFERENCES') AS column_references "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' "
        "AND c.relkind IN ('r','p','v','m','f')"
    ).fetchall()
    found = set()
    for row in rows:
        name = row["relname"]
        public = row["nspname"] == "public"
        is_users = public and name == "users"
        expected = BUSINESS_TABLE_PRIVILEGES.get(name, set()) if public else set()
        if expected:
            if row["relkind"] not in {"r", "p"}:
                raise PermissionError("Business relation type invalid")
            found.add(name)
        if row["dangerous"] or row["column_references"]:
            raise PermissionError("Business role has dangerous privileges")
        for mode in ("select", "insert", "update", "delete"):
            actual = row["can_" + mode]
            permitted = mode.upper() in expected
            if actual != permitted:
                raise PermissionError("Business table privileges invalid")
            if mode != "delete" and not permitted and not (is_users and mode == "select"):
                if row["column_" + mode]:
                    raise PermissionError("Business column privileges invalid")
    if found != set(BUSINESS_TABLE_PRIVILEGES):
        raise PermissionError("Business grant baseline incomplete")
    columns = connection.execute(
        "SELECT attname,has_column_privilege(current_user,attrelid,attnum,'SELECT') AS can_read "
        "FROM pg_attribute WHERE attrelid='public.users'::regclass AND attnum>0 AND NOT attisdropped"
    ).fetchall()
    if {row["attname"] for row in columns if row["can_read"]} != _ALLOWED_USER_COLUMNS:
        raise PermissionError("Business user column privileges invalid")
    unexpected = connection.execute(
        "SELECT NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND c.relkind='S' "
        "AND has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE')) AS sequences_ok, "
        "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE left(n.nspname,3)<>'pg_' AND n.nspname<>'information_schema' AND p.prosecdef "
        "AND p.oid<>COALESCE(to_regprocedure('paperpilot_audit.event_fingerprint(uuid)')::oid,0::oid) "
        "AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS definer_ok, "
        "NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='pg_catalog' AND p.proname=ANY(ARRAY['pg_read_file','pg_read_binary_file','pg_ls_dir','pg_stat_file']) "
        "AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS files_ok"
    ).fetchone()
    if not unexpected or not all(unexpected.values()):
        raise PermissionError("Business function or sequence privileges invalid")
