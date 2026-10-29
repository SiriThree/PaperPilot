"""Queue one explicit owner-approved process task; the running worker selects its configured model revision."""

import argparse
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.adapters.ports import Context  # noqa: E402
from app.data.adapter import PostgresData  # noqa: E402
from app.data.database import ConnectionConfig, PostgresDatabase  # noqa: E402
from app.data.settings import DataSettings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("actor", "project", "paper", "request-id"):
        parser.add_argument("--" + name, type=UUID, required=True)
    args = parser.parse_args()
    database = PostgresDatabase(ConnectionConfig.from_env())
    try:
        database.ready()
        data = PostgresData(database, DataSettings.from_env())
        ctx = Context(args.actor, args.project, args.request_id)
        task = data.reindex_paper(ctx, args.paper, "reindex-" + args.request_id.hex)
        current = data.task(ctx, task.task_id)
        print("Process task:", current.task_id, current.status)
        return 0
    except Exception:
        print(
            "Reindex enqueue not confirmed; inspect the original request-id before retrying. No secrets displayed.",
            file=sys.stderr,
        )
        return 1
    finally:
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
