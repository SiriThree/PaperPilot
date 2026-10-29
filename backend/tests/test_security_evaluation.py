"""Keep the preregistered experiment's categories and success criteria honest."""

from collections import Counter
import importlib.util
from pathlib import Path
from uuid import UUID

import pytest

from app.security.sql_policy import SQLPolicyRejected, prepare_query
from security_contracts.attack_cases import attacks, normal_queries


def test_fixed_attack_categories_and_normal_queries_are_distinct_and_valid():
    project = UUID(int=2)
    cases = attacks(project)
    assert Counter(c["category"] for c in cases) == {
        "sql_injection": 10,
        "cross_project": 10,
        "dangerous_sql": 10,
        "prompt_injection": 10,
    }
    assert len({c["id"] for c in cases}) == 40
    assert Counter(c["origin"] for c in cases if c["category"] == "prompt_injection") == {
        "user": 5,
        "paper": 5,
    }
    for case in cases:
        with pytest.raises(SQLPolicyRejected):
            prepare_query(case["sql"], project)
    normal = normal_queries()
    assert len(normal) == 21 and len({c["id"] for c in normal}) == 21
    for case in normal:
        assert prepare_query(case["sql"], project).parameters == {"project_id": str(project)}


def test_experiment_p95_uses_nearest_rank_and_does_not_drop_slow_samples():
    path = Path(__file__).resolve().parents[2] / "scripts/evaluate_security.py"
    spec = importlib.util.spec_from_file_location("security_evaluation", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.p95(list(range(1, 31))) == 29
    assert module.p95([1] * 28 + [500, 1000]) == 500
