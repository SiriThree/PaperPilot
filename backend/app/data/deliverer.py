"""Transactional outbox deliverer: publishes domain events into the audit trail.

Delivery is at-least-once with exactly-once effect: append_audit dedupes by
event_id, so a crash between append and confirmation only causes a harmless
no-op replay. Rows are claimed with FOR UPDATE SKIP LOCKED; failures back off
exponentially. Published rows are retained indefinitely until A/D agree on a
retention policy (the business entry refuses outbox deletion).
"""

import time
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.data.adapter import PostgresData
from app.data.database import PostgresDatabase
from app.data.settings import DataSettings
from app.errors import AppError
from app.schemas import AuditEvent


class OutboxDeliverer:
    def __init__(
        self,
        database: PostgresDatabase,
        data: PostgresData,
        *,
        batch_size: int = 50,
        base_backoff: float = 1.0,
        max_backoff: float = 60.0,
    ):
        if batch_size < 1 or base_backoff <= 0 or max_backoff < base_backoff:
            raise ValueError("invalid deliverer tuning parameters")
        self.database = database
        self.data = data
        self.batch_size = batch_size
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff

    # ---- lifecycle helpers -------------------------------------------------

    def pending_count(self):
        with self.database.transaction() as conn:
            return conn.execute(
                "SELECT count(*) AS pending FROM outbox_events WHERE published_at IS NULL"
            ).fetchone()["pending"]

    def _due_events(self):
        with self.database.transaction() as conn:
            return conn.execute(
                "SELECT event_id, project_id, actor_id, request_id, payload, created_at,"
                " delivery_attempts"
                " FROM outbox_events WHERE published_at IS NULL AND next_attempt_at <= now()"
                " ORDER BY next_attempt_at, created_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (self.batch_size,),
            ).fetchall()

    def _claim(self, event_id):
        with self.database.transaction(write=True) as conn:
            cursor = conn.execute(
                "UPDATE outbox_events SET delivery_attempts = delivery_attempts + 1"
                " WHERE event_id = %s AND published_at IS NULL AND next_attempt_at <= now()",
                (event_id,),
            )
            return cursor.rowcount == 1

    def _confirm(self, event_id):
        with self.database.transaction(write=True) as conn:
            conn.execute(
                "UPDATE outbox_events SET published_at = now()"
                " WHERE event_id = %s AND published_at IS NULL",
                (event_id,),
            )

    def _record_failure(self, event_id, attempts, code):
        backoff = min(self.max_backoff, self.base_backoff * 2 ** (attempts - 1))
        with self.database.transaction(write=True) as conn:
            conn.execute(
                "UPDATE outbox_events SET next_attempt_at = %s, last_error_code = %s"
                " WHERE event_id = %s AND published_at IS NULL",
                (datetime.now(UTC) + timedelta(seconds=backoff), code, event_id),
            )

    # ---- delivery ------------------------------------------------------------

    @staticmethod
    def _event(row):
        payload = row["payload"]
        return AuditEvent(
            event_id=row["event_id"],
            timestamp=row["created_at"],
            request_id=row["request_id"],
            actor_id=row["actor_id"],
            project_id=row["project_id"],
            action=payload["action"],
            resource_type=payload["resource_type"],
            decision=payload["decision"],
            policy_id=None,
            execution_status=payload["execution_status"],
            duration_ms=payload["duration_ms"],
            mock=payload["mock"],
        )

    def deliver_one(self, row):
        if not self._claim(row["event_id"]):
            return False
        attempts = row["delivery_attempts"] + 1
        try:
            self.data.append_audit(self._event(row))
        except AppError as exc:
            self._record_failure(row["event_id"], attempts, exc.code)
            return False
        except ValidationError:
            # A malformed payload can never become a valid audit event; not a transient fault.
            self._record_failure(row["event_id"], attempts, "ADAPTER_CONTRACT_ERROR")
            return False
        except Exception:
            self._record_failure(row["event_id"], attempts, "DATA_UNAVAILABLE")
            return False
        self._confirm(row["event_id"])
        return True

    def run_once(self):
        delivered = 0
        for row in self._due_events():
            if self.deliver_one(row):
                delivered += 1
        return delivered

    def run_forever(self, *, interval: float = 0.5, stop=None):
        while stop is None or not stop.is_set():
            if self.run_once() == 0:
                time.sleep(interval)


def build_deliverer(config, **tuning):
    """Standalone deliverer on its own pool; for ops processes and tests."""
    database = PostgresDatabase(config)
    data = PostgresData(database, DataSettings.from_env())
    return OutboxDeliverer(database, data, **tuning)


def main(argv=None):
    import argparse

    from app.data.database import ConnectionConfig

    parser = argparse.ArgumentParser(description="Deliver outbox_events into the audit trail")
    parser.add_argument("--once", action="store_true", help="Deliver due events once and exit")
    parser.add_argument("--interval", type=float, default=0.5, help="Polling interval in loop mode")
    options = parser.parse_args(argv)
    deliverer = build_deliverer(ConnectionConfig.from_env())
    try:
        if options.once:
            print(f"outbox delivered: {deliverer.run_once()}")
            return
        stop = None
        deliverer.run_forever(interval=options.interval, stop=stop)
    except KeyboardInterrupt:
        pass
    finally:
        deliverer.database.close()


if __name__ == "__main__":
    main()
