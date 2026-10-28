"""Verify the actual checkout using disposable PostgreSQL and explicit real A/C/D providers."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / ".runtime/integration-results.xml")
    parser.add_argument("tests", nargs="*", default=["backend/tests"])
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    os.environ["PAPERPILOT_SECURITY_CURRENT_SOURCE"] = "1"
    from security_contracts.postgres_provider import postgres_workspace

    with postgres_workspace() as pg:
        environment = dict(
            os.environ,
            PAPERPILOT_SECURITY_POSTGRES_TESTS="1",
            PAPERPILOT_SECURITY_TEAM_TESTS="1",
            PAPERPILOT_SECURITY_TEST_A_SOURCE=str(ROOT),
            PAPERPILOT_SECURITY_TEST_C_SOURCE=str(ROOT),
            PAPERPILOT_DATA_HOST=pg.config.host,
            PAPERPILOT_DATA_PORT=str(pg.config.port),
            PAPERPILOT_DATA_USER=pg.config.user,
            PAPERPILOT_DATA_PASSWORD=pg.config.password,
            PAPERPILOT_DATA_DBNAME=pg.config.dbname,
            PAPERPILOT_TEST_DATA_HOST=pg.config.host,
            PAPERPILOT_TEST_DATA_PORT=str(pg.config.port),
            PAPERPILOT_TEST_DATA_USER=pg.config.user,
            PAPERPILOT_TEST_DATA_PASSWORD=pg.config.password,
        )
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *args.tests,
                "-q",
                "--tb=short",
                "--data-contract-provider",
                "app.data.provider:build_harness",
                "--ai-contract-provider",
                "ai_contracts.real_provider:build_harness",
                "--security-contract-provider",
                "security_contracts.postgres_provider:build_harness",
                f"--junitxml={args.report}",
            ],
            cwd=ROOT,
            env=environment,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
