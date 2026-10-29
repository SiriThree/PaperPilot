"""Transactional outbox deliverer: publishes domain events into the audit trail.

Delivery is at-least-once: append_audit dedupes audit records by event_id,
but external side effects require their own idempotency. Claims use a bounded
lease and attempt fencing; failures back off
exponentially. Published rows are retained indefinitely until A/D agree on a
retention policy (the business entry refuses outbox deletion).
"""

import logging
import time
from uuid import UUID, uuid4

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
        lease_seconds: int = 30,
        max_attempts: int = 5,
    ):
        if (
            batch_size < 1
            or base_backoff <= 0
            or max_backoff < base_backoff
            or lease_seconds <= 0
            or max_attempts < 1
        ):
            raise ValueError("invalid deliverer tuning parameters")
        if (
            type(max_attempts) is not int
            or not 1 <= max_attempts <= 100
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 3600
        ):
            raise ValueError("invalid delivery retry/lease budget")
        self.database = database
        self.data = data
        self.batch_size = batch_size
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts

    # ---- lifecycle helpers -------------------------------------------------

    def pending_count(self):
        with self.database.transaction() as conn:
            return conn.execute(
                "SELECT count(*) AS pending FROM outbox_events"
                " WHERE published_at IS NULL AND dead_lettered_at IS NULL"
            ).fetchone()["pending"]

    def dead_letters(self, *, limit=100):
        """Operator-only metadata; no payload, credentials or automatic replay."""
        if not 1 <= limit <= 1000:
            raise ValueError("invalid dead-letter limit")
        with self.database.transaction() as conn:
            return conn.execute(
                "SELECT event_id, request_id, delivery_attempts, last_error_code,"
                " last_attempt_at, dead_lettered_at FROM outbox_events"
                " WHERE dead_lettered_at IS NOT NULL ORDER BY dead_lettered_at, event_id LIMIT %s",
                (limit,),
            ).fetchall()

    def requeue_dead_letter(self, event_id, *, unknown_reconciled=False):
        """Explicit operator action; retain the ID and append its prior failure state."""
        if not isinstance(event_id, UUID) or type(unknown_reconciled) is not bool:
            raise ValueError("Invalid reconciliation input")
        with self.database.transaction(write=True) as conn:
            row = conn.execute(
                "SELECT delivery_attempts,last_error_code,dead_lettered_at FROM outbox_events"
                " WHERE event_id=%s AND published_at IS NULL AND dead_lettered_at IS NOT NULL FOR UPDATE",
                (event_id,),
            ).fetchone()
            if row is None:
                return False
            if row["last_error_code"] == "DELIVERY_OUTCOME_UNKNOWN" and not unknown_reconciled:
                raise ValueError("Unknown delivery must be reconciled before requeue")
            conn.execute(
                "INSERT INTO outbox_requeue_history(requeue_id,event_id,database_user,prior_attempts,"
                " prior_error_code,prior_dead_lettered_at,unknown_reconciled)"
                " VALUES(%s,%s,SESSION_USER,%s,%s,%s,%s)",
                (
                    uuid4(),
                    event_id,
                    row["delivery_attempts"],
                    row["last_error_code"],
                    row["dead_lettered_at"],
                    unknown_reconciled,
                ),
            )
            conn.execute(
                "UPDATE outbox_events SET retry_base_attempts=delivery_attempts, dead_lettered_at=NULL,"
                " next_attempt_at=now() WHERE event_id=%s",
                (event_id,),
            )
            return True

    def _due_events(self):
        with self.database.transaction() as conn:
            return conn.execute(
                "SELECT event_id, project_id, actor_id, request_id, payload, created_at,"
                " delivery_attempts"
                " FROM outbox_events WHERE published_at IS NULL AND dead_lettered_at IS NULL"
                " AND next_attempt_at <= now()"
                " ORDER BY next_attempt_at, created_at LIMIT %s",
                (self.batch_size,),
            ).fetchall()

    def _claim(self, event_id):
        with self.database.transaction(write=True) as conn:
            # A process may disappear after delivery and before confirmation.
            # Exhausted leases therefore mean an *unknown* outcome, not a
            # definite failure: retain the event for operator reconciliation.
            conn.execute(
                "UPDATE outbox_events SET dead_lettered_at = now(),"
                " last_error_code = 'DELIVERY_OUTCOME_UNKNOWN'"
                " WHERE event_id = %s AND published_at IS NULL AND dead_lettered_at IS NULL"
                " AND next_attempt_at <= now() AND delivery_attempts - retry_base_attempts >= %s",
                (event_id, self.max_attempts),
            )
            row = conn.execute(
                "UPDATE outbox_events SET delivery_attempts = delivery_attempts + 1,"
                " next_attempt_at = now() + (%s * interval '1 second'), last_attempt_at = now()"
                " WHERE event_id = %s AND published_at IS NULL AND dead_lettered_at IS NULL"
                " AND next_attempt_at <= now() AND delivery_attempts - retry_base_attempts < %s"
                " RETURNING delivery_attempts",
                (self.lease_seconds, event_id, self.max_attempts),
            ).fetchone()
            return row["delivery_attempts"] if row else None

    def _confirm(self, event_id, attempts):
        with self.database.transaction(write=True) as conn:
            cursor = conn.execute(
                "UPDATE outbox_events SET published_at = now() WHERE event_id = %s"
                " AND published_at IS NULL AND dead_lettered_at IS NULL"
                " AND delivery_attempts = %s AND next_attempt_at > now()",
                (event_id, attempts),
            )
            return cursor.rowcount == 1

    def _record_failure(self, event_id, attempts, code):
        backoff = min(self.max_backoff, self.base_backoff * 2 ** min(attempts - 1, 32))
        with self.database.transaction(write=True) as conn:
            conn.execute(
                "UPDATE outbox_events SET next_attempt_at = now() + (%s * interval '1 second'), last_error_code = %s,"
                " dead_lettered_at = CASE WHEN %s - retry_base_attempts >= %s THEN now() ELSE NULL END"
                " WHERE event_id = %s AND published_at IS NULL AND dead_lettered_at IS NULL"
                " AND delivery_attempts = %s"
                " AND next_attempt_at > now()",
                (
                    backoff,
                    code,
                    attempts,
                    self.max_attempts,
                    event_id,
                    attempts,
                ),
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
        attempts = self._claim(row["event_id"])
        if attempts is None:
            return False
        try:
            self.data.append_audit(self._event(row))
        except AppError:
            self._record_failure(row["event_id"], attempts, "AUDIT_UNAVAILABLE")
            return False
        except (ValidationError, KeyError, TypeError):
            # A malformed payload can never become a valid audit event; not a transient fault.
            self._record_failure(row["event_id"], attempts, "ADAPTER_CONTRACT_ERROR")
            return False
        except Exception:
            self._record_failure(row["event_id"], attempts, "DATA_UNAVAILABLE")
            return False
        return self._confirm(row["event_id"], attempts)

    def run_once(self):
        delivered = 0
        for row in self._due_events():
            if self.deliver_one(row):
                delivered += 1
        return delivered

    def run_forever(self, *, interval: float = 0.5, stop=None):
        if interval <= 0:
            raise ValueError("Polling interval must be positive")
        while stop is None or not stop.is_set():
            try:
                idle = self.run_once() == 0
            except AppError:
                # An unknown commit is reconciled by the existing lease and event ID.
                logging.getLogger("paperpilot.outbox").warning("outbox_storage_unavailable")
                idle = True
            if idle:
                if stop is None:
                    time.sleep(interval)
                else:
                    stop.wait(interval)


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
    parser.add_argument("--max-attempts", type=int, default=5, help="Maximum delivery attempts")
    parser.add_argument("--requeue-event", type=UUID, help="Explicitly requeue one reconciled dead letter")
    parser.add_argument(
        "--confirm-unknown-reconciled", action="store_true", help="Confirm unknown delivery was reconciled"
    )
    options = parser.parse_args(argv)
    deliverer = build_deliverer(ConnectionConfig.from_env(), max_attempts=options.max_attempts)
    try:
        # Fail visibly before polling when the configured database is unavailable or outdated.
        deliverer.database.ready()
        if options.requeue_event is not None:
            if not deliverer.requeue_dead_letter(
                options.requeue_event, unknown_reconciled=options.confirm_unknown_reconciled
            ):
                raise SystemExit("Dead letter not found or already requeued")
            print("Outbox event requeued")
            return
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
