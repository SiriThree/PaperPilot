"""Transactional outbox deliverer: publishes domain events into the audit trail.

Delivery is at-least-once with exactly-once effect: append_audit dedupes by
event_id, so a crash between append and confirmation only causes a harmless
no-op replay. Claims use fenced delivery leases; failures back off exponentially
and enter a replayable dead-letter state at the retry budget. Rows are retained indefinitely until A/D agree on a
retention policy (the business entry refuses outbox deletion).
"""

import time
import json
import math
from datetime import UTC, datetime, timedelta
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
        max_attempts: int = 5,
        lease_seconds: int = 30,
    ):
        if (
            batch_size < 1
            or not math.isfinite(base_backoff)
            or not math.isfinite(max_backoff)
            or base_backoff <= 0
            or max_backoff < base_backoff
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
        self.max_attempts, self.lease_seconds = max_attempts, lease_seconds

    # ---- lifecycle helpers -------------------------------------------------

    def pending_count(self):
        with self.database.transaction() as conn:
            return conn.execute(
                "SELECT count(*) AS pending FROM outbox_events WHERE published_at IS NULL AND dead_lettered_at IS NULL"
            ).fetchone()["pending"]

    def _due_events(self):
        with self.database.transaction() as conn:
            return conn.execute(
                "SELECT event_id, project_id, actor_id, request_id, payload, created_at,"
                " delivery_attempts"
                " FROM outbox_events WHERE published_at IS NULL AND dead_lettered_at IS NULL AND next_attempt_at <= now()"
                " AND (delivery_lease_until IS NULL OR delivery_lease_until<=now())"
                " ORDER BY next_attempt_at, created_at LIMIT %s FOR UPDATE SKIP LOCKED",
                (self.batch_size,),
            ).fetchall()

    def _claim(self, event_id):
        with self.database.transaction(write=True) as conn:
            conn.execute(
                "UPDATE outbox_events SET dead_lettered_at=now(),last_error_code='DELIVERY_RETRY_LIMIT',"
                " claim_token=NULL,delivery_lease_until=NULL WHERE event_id=%s AND published_at IS NULL"
                " AND dead_lettered_at IS NULL AND retry_attempts>=%s"
                " AND (delivery_lease_until IS NULL OR delivery_lease_until<=now())",
                (event_id, self.max_attempts),
            )
            return conn.execute(
                "UPDATE outbox_events SET delivery_attempts = delivery_attempts + 1"
                ",retry_attempts=retry_attempts+1,claim_token=%s,delivery_lease_until=%s"
                " WHERE event_id = %s AND published_at IS NULL AND dead_lettered_at IS NULL AND next_attempt_at <= now()"
                " AND retry_attempts<%s AND (delivery_lease_until IS NULL OR delivery_lease_until<=now()) RETURNING *",
                (
                    uuid4(),
                    datetime.now(UTC) + timedelta(seconds=self.lease_seconds),
                    event_id,
                    self.max_attempts,
                ),
            ).fetchone()

    def _confirm(self, event_id, token):
        with self.database.transaction(write=True) as conn:
            return (
                conn.execute(
                    "UPDATE outbox_events SET published_at = now(),claim_token=NULL,delivery_lease_until=NULL,last_error_code=NULL"
                    " WHERE event_id = %s AND published_at IS NULL AND claim_token=%s",
                    (event_id, token),
                ).rowcount
                == 1
            )

    def _record_failure(self, event_id, token, attempts, code):
        backoff = min(self.max_backoff, self.base_backoff * 2 ** (attempts - 1))
        with self.database.transaction(write=True) as conn:
            conn.execute(
                "UPDATE outbox_events SET next_attempt_at = %s, last_error_code = %s,claim_token=NULL,delivery_lease_until=NULL,"
                " dead_lettered_at=CASE WHEN retry_attempts>=%s THEN now() ELSE NULL END"
                " WHERE event_id = %s AND published_at IS NULL AND claim_token=%s",
                (datetime.now(UTC) + timedelta(seconds=backoff), code, self.max_attempts, event_id, token),
            )

    def status(self):
        with self.database.transaction() as conn:
            result = conn.execute(
                "SELECT count(*) FILTER (WHERE published_at IS NOT NULL) AS published,"
                " count(*) FILTER (WHERE published_at IS NULL AND dead_lettered_at IS NULL) AS pending,"
                " count(*) FILTER (WHERE dead_lettered_at IS NOT NULL) AS dead,"
                " count(*) FILTER (WHERE delivery_lease_until>now() AND published_at IS NULL) AS in_flight,"
                " COALESCE(sum(manual_replays),0)::bigint AS manual_replays FROM outbox_events"
            ).fetchone()
            result["tasks"] = {
                row["status"]: row["count"]
                for row in conn.execute(
                    "SELECT status,count(*) AS count FROM tasks GROUP BY status"
                ).fetchall()
            }
            result["expired_task_leases"] = conn.execute(
                "SELECT count(*) AS n FROM task_attempts WHERE status='processing' AND lease_expires_at<=now()"
            ).fetchone()["n"]
            return result

    def replay_dead(self, event_id):
        if type(event_id) is not UUID:
            raise ValueError("An explicit event UUID is required")
        with self.database.transaction(write=True) as conn:
            return (
                conn.execute(
                    "UPDATE outbox_events SET dead_lettered_at=NULL,retry_attempts=0,"
                    " manual_replays=manual_replays+1,last_replayed_at=now(),next_attempt_at=now(),last_error_code=NULL,"
                    " claim_token=NULL,delivery_lease_until=NULL WHERE event_id=%s AND dead_lettered_at IS NOT NULL"
                    " AND published_at IS NULL",
                    (event_id,),
                ).rowcount
                == 1
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
        claimed = self._claim(row["event_id"])
        if claimed is None:
            return False
        attempts, token = claimed["retry_attempts"], claimed["claim_token"]
        try:
            self.data.append_audit(self._event(claimed))
        except AppError:
            self._record_failure(row["event_id"], token, attempts, "AUDIT_UNAVAILABLE")
            return False
        except ValidationError:
            # A malformed payload can never become a valid audit event; not a transient fault.
            self._record_failure(row["event_id"], token, attempts, "ADAPTER_CONTRACT_ERROR")
            return False
        except Exception:
            self._record_failure(row["event_id"], token, attempts, "DATA_UNAVAILABLE")
            return False
        return self._confirm(row["event_id"], token)

    def run_once(self):
        delivered = 0
        for row in self._due_events():
            if self.deliver_one(row):
                delivered += 1
        return delivered

    def run_forever(self, *, interval: float = 0.5, stop=None):
        if not math.isfinite(interval) or not 0 < interval <= 60:
            raise ValueError("Delivery interval must be between 0 and 60 seconds")
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
    parser.add_argument("--status", action="store_true", help="Print counts only; no event payloads")
    parser.add_argument("--replay-dead", type=UUID, help="Explicitly release one dead event for redelivery")
    parser.add_argument("--max-attempts", type=int, default=5)
    options = parser.parse_args(argv)
    deliverer = build_deliverer(ConnectionConfig.from_env(), max_attempts=options.max_attempts)
    try:
        if options.status:
            print(json.dumps(deliverer.status()))
            return
        if options.replay_dead is not None:
            print("Outbox dead replay released:", deliverer.replay_dead(options.replay_dead))
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
