"""Closed PostgreSQL statistics grammar aligned with A's project-import model."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, ParseError, TokenError
from sqlglot.tokens import TokenType

from app.adapters.ports import Context

SQL_BY_QUESTION = {
    "按年份统计论文数量": (
        "SELECT year, COUNT(*) AS count FROM paperpilot_security.paper_statistics GROUP BY year ORDER BY year"
    ),
    "按研究主题统计论文数量": (
        "SELECT topic, COUNT(DISTINCT paper_id) AS count FROM paperpilot_security.paper_topics"
        " GROUP BY topic ORDER BY topic"
    ),
    "查询尚未完成证据提取的论文": (
        "SELECT paper_id, title FROM paperpilot_security.paper_statistics"
        " WHERE has_evidence = FALSE ORDER BY paper_id"
    ),
}
CandidateGenerator = Callable[[Context, str], Awaitable[str | None]]


class SQLPolicyRejected(ValueError):
    """The candidate is outside the grammar; never expose parser diagnostics."""


class _StrictParser(sqlglot.Parser):
    def _warn_unsupported(self) -> None:
        # The pinned parser normally logs raw SQL before falling back to Command.
        self.raise_error("Unsupported SQL")


@dataclass(frozen=True)
class QueryPlan:
    sql: str
    parameters: dict[str, str]


async def generate_candidate(ctx: Context, question: str) -> str | None:
    """Deterministic routing only; a future model may supply untrusted candidates privately."""
    return SQL_BY_QUESTION.get(question.strip())


def _structure(tree: exp.Expression) -> list[dict]:
    # Compare full AST structure, not Expression.__eq__ (which compares hashes).
    return [{key: value for key, value in node.items() if key != "m"} for node in tree.dump()]


_TEMPLATES = tuple(sqlglot.parse_one(sql, read="postgres") for sql in SQL_BY_QUESTION.values())


def prepare_query(candidate: str, project_id: UUID) -> QueryPlan:
    """Accept only the three AST shapes, then rebuild and bind a trusted project parameter."""
    if not isinstance(candidate, str) or not 0 < len(candidate) <= 4096:
        raise SQLPolicyRejected()
    if not isinstance(project_id, UUID):
        raise SQLPolicyRejected()
    try:
        tokens = sqlglot.tokenize(candidate, read="postgres")
        if not tokens or len(tokens) > 128 or any(token.comments for token in tokens):
            raise SQLPolicyRejected()
        if tokens[-1].token_type == TokenType.SEMICOLON:
            tokens = tokens[:-1]
        if (
            not tokens
            or tokens[0].token_type != TokenType.SELECT
            or any(token.token_type == TokenType.SEMICOLON for token in tokens)
        ):
            raise SQLPolicyRejected()
        trees = _StrictParser(error_level=ErrorLevel.IMMEDIATE, max_nodes=128, dialect="postgres").parse(
            tokens, candidate
        )
        if len(trees) != 1 or not isinstance(trees[0], exp.Select):
            raise SQLPolicyRejected()
        structure = _structure(trees[0])
    except (ParseError, TokenError, RecursionError):
        raise SQLPolicyRejected() from None
    for template in _TEMPLATES:
        if structure == _structure(template):
            # Rebuild from the trusted template, never execute or forward the candidate text.
            bound = template.where(
                exp.EQ(this=exp.column("project_id"), expression=exp.Placeholder(this="project_id"))
            ).limit(100)
            return QueryPlan(sql=bound.sql(dialect="postgres"), parameters={"project_id": str(project_id)})
    raise SQLPolicyRejected()
