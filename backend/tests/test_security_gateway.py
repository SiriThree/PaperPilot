"""Gateway/AST unit tests only, never a substitute for real SQL permission or attack tests."""

import asyncio
from uuid import uuid4

import pytest
import sqlglot
from fastapi.testclient import TestClient
from sqlglot import exp
from sqlglot.errors import ParseError

from app.adapters.mock_data import MockData, seed_id
from app.adapters.ports import Context
from app.config import Settings
from app.errors import AppError
from app.main import create_app
from app.security import SecurityAdapter, build_security
from app.security.sql_policy import SQL_BY_QUESTION, SQLPolicyRejected, _StrictParser, prepare_query

ATTACKS = [
    "DELETE FROM security_paper_statistics",
    "UPDATE security_paper_statistics SET title = 'bad'",
    "INSERT INTO security_paper_statistics (title) VALUES ('bad')",
    "DROP TABLE security_paper_statistics",
    "ALTER TABLE security_paper_statistics ADD COLUMN private TEXT",
    "TRUNCATE TABLE security_paper_statistics",
    "GRANT ALL ON security_paper_statistics TO public",
    "COPY security_paper_statistics TO '/tmp/private'",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year; DELETE FROM papers",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year; SELECT 1",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year;;",
    "SELECT /* hidden */ year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year -- hidden",
    "SELECT year, COUNT(*) AS count FROM private.security_paper_statistics GROUP BY year",
    "SELECT year, COUNT(*) AS count FROM users GROUP BY year",
    "SELECT password FROM security_paper_statistics",
    "SELECT * FROM security_paper_statistics",
    "SELECT year, SUM(year) AS count FROM security_paper_statistics GROUP BY year",
    "SELECT year FROM security_paper_statistics JOIN users ON TRUE",
    "SELECT year FROM security_paper_statistics UNION SELECT password FROM users",
    "WITH x AS (SELECT * FROM users) SELECT * FROM x",
    "SELECT year FROM security_paper_statistics WHERE EXISTS (SELECT 1 FROM users)",
    "SELECT (SELECT password FROM users) FROM security_paper_statistics",
    "SELECT pg_sleep(10) FROM security_paper_statistics",
    "SELECT load_file('/etc/passwd') FROM security_paper_statistics",
    "SELECT * FROM read_csv('/tmp/private')",
    "SELECT year INTO copied_data FROM security_paper_statistics",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year FOR UPDATE",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics WHERE TRUE GROUP BY year",
    "SELECT paper_id, title FROM security_paper_statistics WHERE has_evidence = FALSE OR TRUE",
    "SELECT paper_id, title FROM security_paper_statistics WHERE project_id = 'other-project'",
    "SELECT paper_id, title FROM security_paper_statistics WHERE project_id = :project_id",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year ORDER BY random()",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year LIMIT 100000",
    "SELECT year, COUNT(*) OVER () AS count FROM security_paper_statistics",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY ROLLUP(year)",
    "SELECT year + 1, COUNT(*) AS count FROM security_paper_statistics GROUP BY year",
    "SELECT year, COUNT(DISTINCT paper_id) AS count FROM security_paper_statistics GROUP BY year",
    "SELECT CAST(year AS TEXT), COUNT(*) AS count FROM security_paper_statistics GROUP BY year",
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics AS x GROUP BY year",
    'SELECT year, COUNT(*) AS count FROM "security_paper_statistics" GROUP BY year',
    "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year garbage",
    "SELECT paper_id, title FROM security_paper_statistics WHERE has_evidence = TRUE",
    "SELECT paper_id, title FROM security_paper_statistics WHERE has_evidence = FALSE AND project_id = 'x'",
]
ATTACKS = [
    candidate.replace("security_paper_statistics", "paperpilot_security.paper_statistics")
    for candidate in ATTACKS
]


@pytest.fixture
def data():
    return MockData()


@pytest.fixture
def ctx():
    return Context(seed_id("alice"), seed_id("project-a"), uuid4())


@pytest.mark.parametrize("sql", SQL_BY_QUESTION.values())
@pytest.mark.parametrize("variant", ["original", "lower", "space", "newline", "tabs", "semicolon", "mixed"])
def test_allowed_ast_variants_are_rebuilt_with_project_parameter(sql, variant):
    candidate = {
        "original": sql,
        "lower": sql.lower(),
        "space": "  " + sql.replace(" ", "   ") + "  ",
        "newline": sql.replace(" ", "\n"),
        "tabs": sql.replace(" ", "\t"),
        "semicolon": sql + ";",
        "mixed": sql.replace("SELECT", "SeLeCt").replace("FROM", "fRoM"),
    }[variant]
    project_id = uuid4()
    plan = prepare_query(candidate, project_id)
    assert plan.parameters == {"project_id": str(project_id)}
    assert str(project_id) not in plan.sql
    assert "%(project_id)s" in plan.sql and ":project_id" not in plan.sql
    tree = sqlglot.parse_one(plan.sql, read="postgres")
    tables = list(tree.find_all(exp.Table))
    expected = "paper_topics" if "COUNT(DISTINCT" in sql else "paper_statistics"
    assert [(table.db, table.name) for table in tables] == [("paperpilot_security", expected)]
    assert len(list(tree.find_all(exp.Placeholder))) == 1
    assert tree.args["limit"].expression.this == "100"
    condition = next(eq for eq in tree.find_all(exp.EQ) if eq.this.name == "project_id")
    assert isinstance(condition.expression, exp.Placeholder) and condition.expression.name == "project_id"
    if "has_evidence" in sql:
        assert isinstance(tree.args["where"].this, exp.And)


@pytest.mark.parametrize("candidate", ATTACKS)
def test_candidates_outside_closed_ast_grammar_are_rejected(candidate, caplog):
    with pytest.raises(SQLPolicyRejected):
        prepare_query(candidate, uuid4())
    assert not [record for record in caplog.records if record.name == "sqlglot"]


@pytest.mark.parametrize(
    "candidate", [None, {}, "", "x" * 4097, "SELECT ('unterminated", "SELECT " + "(" * 200]
)
def test_invalid_or_excessive_candidates_fail_closed(candidate):
    with pytest.raises(SQLPolicyRejected):
        prepare_query(candidate, uuid4())


def test_binding_requires_uuid_and_is_independent_per_request():
    sql = next(iter(SQL_BY_QUESTION.values()))
    with pytest.raises(SQLPolicyRejected):
        prepare_query(sql, "untrusted-project")
    left, right = uuid4(), uuid4()
    first, second = prepare_query(sql, left), prepare_query(sql, right)
    assert first.sql == second.sql
    assert first.parameters == {"project_id": str(left)}
    assert second.parameters == {"project_id": str(right)}


@pytest.mark.parametrize(
    "candidate",
    [
        "SELECT year, COUNT(*) AS count FROM security_paper_statistics GROUP BY year",
        SQL_BY_QUESTION["按研究主题统计论文数量"].replace("COUNT(DISTINCT paper_id)", "COUNT(*)"),
        SQL_BY_QUESTION["按年份统计论文数量"].replace("paper_statistics", "paper_topics"),
        SQL_BY_QUESTION["按年份统计论文数量"].replace("paperpilot_security", "public"),
    ],
)
def test_postgres_contract_rejects_old_view_and_wrong_counting_grain(candidate):
    with pytest.raises(SQLPolicyRejected):
        prepare_query(candidate, uuid4())


def test_statistics_migration_is_available_as_package_resource():
    from importlib.resources import files

    assert files("app.security").joinpath("migrations/001_statistics_views.sql").is_file()


def test_unsupported_parser_commands_never_fall_back_or_log_sql(caplog):
    candidate = "SHOW PRIVATE_MODEL_SQL"
    with pytest.raises(ParseError):
        _StrictParser().parse(sqlglot.tokenize(candidate), candidate)
    assert not caplog.records


@pytest.mark.parametrize("question", SQL_BY_QUESTION)
def test_normal_questions_are_allowed_but_never_executed(data, ctx, monkeypatch, question):
    def forbidden(*args):
        pytest.fail("DataPort statistics must not bypass the SQL gateway")

    monkeypatch.setattr(data, "controlled_statistics", forbidden)
    response = asyncio.run(build_security(data).execute(ctx, question))
    assert (response.decision, response.execution_status) == ("allowed", "not_executed")
    assert response.reason.code == "QUERY_EXECUTION_DISABLED"
    assert response.policy_id.endswith("-template")
    assert response.request_id == ctx.request_id and response.mock is False
    assert not response.rows and not response.columns
    assert "SELECT" not in response.model_dump_json()


@pytest.mark.parametrize("candidate", [ATTACKS[8], ATTACKS[29], {}, ""])
def test_injected_candidate_generator_cannot_bypass_validation(data, ctx, candidate):
    async def generate(actual_ctx, question):
        assert actual_ctx is ctx and question == "统计论文"
        return candidate

    security = SecurityAdapter(data, candidate_generator=generate)
    response = asyncio.run(security.execute(ctx, "统计论文"))
    assert (response.decision, response.execution_status) == ("rejected", "not_executed")
    assert response.reason.code == "SQL_POLICY_REJECTED" and not response.rows
    assert response.policy_id.endswith("-injected")
    assert "SELECT" not in response.model_dump_json()


def test_candidate_generator_failure_is_safe(data, ctx):
    async def broken(*args):
        raise AppError(500, "PRIVATE_SQL", "PRIVATE_DATABASE_URL")

    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, candidate_generator=broken).execute(ctx, "统计论文"))
    assert (error.value.status, error.value.code) == (503, "GATEWAY_UNAVAILABLE")
    assert "PRIVATE" not in error.value.message


def test_policy_internal_failure_is_safe(data, ctx, monkeypatch):
    def broken(*args):
        raise RuntimeError("PRIVATE_SQL")

    monkeypatch.setattr("app.security.adapter.prepare_query", broken)
    with pytest.raises(AppError) as error:
        asyncio.run(build_security(data).execute(ctx, "按年份统计论文数量"))
    assert (error.value.status, error.value.code) == (503, "GATEWAY_UNAVAILABLE")
    assert "PRIVATE" not in error.value.message


def test_membership_is_rechecked_after_candidate_generation(data):
    ctx = Context(seed_id("bob"), seed_id("project-a"), uuid4())

    async def revoke(actual_ctx, question):
        owner = Context(seed_id("alice"), ctx.project_id, ctx.request_id)
        data.remove_member(owner, actual_ctx.user_id)
        return next(iter(SQL_BY_QUESTION.values()))

    with pytest.raises(AppError) as error:
        asyncio.run(SecurityAdapter(data, candidate_generator=revoke).execute(ctx, "统计论文"))
    assert (error.value.status, error.value.code) == (404, "RESOURCE_NOT_FOUND")


@pytest.mark.parametrize("question", [None, "", 123, "x" * 2001])
def test_internal_question_validation_matches_public_schema(data, ctx, question):
    with pytest.raises(AppError) as error:
        asyncio.run(build_security(data).execute(ctx, question))
    assert (error.value.status, error.value.code) == (422, "VALIDATION_ERROR")


def test_http_never_accepts_sql_or_client_context(tmp_path):
    app = create_app(
        Settings(environment="test", d_mode="real", storage_dir=tmp_path / "uploads"),
        security_factory=build_security,
    )
    path = f"/api/v1/projects/{seed_id('project-a')}/secure-queries"
    with TestClient(app, headers={"X-PaperPilot-Client": "web"}) as client:
        assert client.post("/api/v1/auth/login", json={"username": "alice"}).status_code == 200
        sql = next(iter(SQL_BY_QUESTION.values()))
        response = client.post(path, json={"question": sql})
        assert response.status_code == 200
        assert response.json()["reason"]["code"] == "UNSUPPORTED_QUERY"
        assert client.post(path, json={"question": "统计", "sql": sql}).status_code == 422
        assert client.post(path, json={"question": "统计", "ctx": {"project_id": "other"}}).status_code == 422
