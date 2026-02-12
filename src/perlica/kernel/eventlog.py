"""SQLite-backed append-only event log."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from perlica.kernel.types import EventEnvelope, new_id, now_ms


class EventLog:
    """Append-only event storage.

    Writes are serialized with a lock to keep ordering deterministic in tests.
    """

    def __init__(self, db_path: Path, context_id: str) -> None:
        self._db_path = db_path
        self._context_id = context_id
        self._lock = threading.Lock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS event_log (
                event_id TEXT PRIMARY KEY,
                ts_ms INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                context_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                parent_node_id TEXT,
                actor TEXT NOT NULL,
                run_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                causation_id TEXT,
                correlation_id TEXT,
                idempotency_key TEXT,
                payload_json TEXT NOT NULL,
                meta_json TEXT NOT NULL,
                prev_event_hash TEXT,
                event_hash TEXT
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_conv_ts ON event_log (context_id, conversation_id, ts_ms)"
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_run_ts ON event_log (run_id, ts_ms)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_event_type_ts ON event_log (event_type, ts_ms)")
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uniq_event_idempotency
            ON event_log (context_id, conversation_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        self._conn.commit()

    def append(
        self,
        event_type: str,
        payload: Dict[str, Any],
        conversation_id: str,
        parent_node_id: Optional[str] = None,
        actor: str = "system",
        meta: Optional[Dict[str, Any]] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        node_id: Optional[str] = None,
    ) -> EventEnvelope:
        with self._lock:
            envelope = EventEnvelope(
                event_id=new_id("evt"),
                event_type=event_type,
                schema_version=2,
                ts_ms=now_ms(),
                context_id=self._context_id,
                conversation_id=conversation_id,
                node_id=node_id or new_id("node"),
                parent_node_id=parent_node_id,
                actor=actor,
                run_id=run_id or new_id("run"),
                trace_id=trace_id or new_id("trace"),
                causation_id=causation_id,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                payload=payload,
                meta=meta or {},
            )

            try:
                self._conn.execute(
                    """
                    INSERT INTO event_log (
                        event_id, ts_ms, event_type, schema_version, context_id,
                        conversation_id, node_id, parent_node_id, actor, run_id,
                        trace_id, causation_id, correlation_id, idempotency_key,
                        payload_json, meta_json, prev_event_hash, event_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope.event_id,
                        envelope.ts_ms,
                        envelope.event_type,
                        envelope.schema_version,
                        envelope.context_id,
                        envelope.conversation_id,
                        envelope.node_id,
                        envelope.parent_node_id,
                        envelope.actor,
                        envelope.run_id,
                        envelope.trace_id,
                        envelope.causation_id,
                        envelope.correlation_id,
                        envelope.idempotency_key,
                        json.dumps(envelope.payload, ensure_ascii=True),
                        json.dumps(envelope.meta, ensure_ascii=True),
                        envelope.prev_event_hash,
                        envelope.event_hash,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError:
                if not idempotency_key:
                    raise
                existing = self._conn.execute(
                    """
                    SELECT * FROM event_log
                    WHERE context_id = ? AND conversation_id = ? AND idempotency_key = ?
                    ORDER BY ts_ms ASC LIMIT 1
                    """,
                    (self._context_id, conversation_id, idempotency_key),
                ).fetchone()
                if existing is None:
                    raise
                return self._row_to_event(existing)

            return envelope

    def list_events(self, limit: int = 1000) -> List[EventEnvelope]:
        rows = self._conn.execute(
            "SELECT * FROM event_log ORDER BY rowid ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_by_conversation(self, conversation_id: str, limit: int = 1000) -> List[EventEnvelope]:
        rows = self._conn.execute(
            """
            SELECT * FROM event_log
            WHERE conversation_id = ?
            ORDER BY rowid ASC
            LIMIT ?
            """,
            (conversation_id, limit),
        ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> EventEnvelope:
        return EventEnvelope(
            event_id=row["event_id"],
            event_type=row["event_type"],
            schema_version=row["schema_version"],
            ts_ms=row["ts_ms"],
            context_id=row["context_id"],
            conversation_id=row["conversation_id"],
            node_id=row["node_id"],
            parent_node_id=row["parent_node_id"],
            actor=row["actor"],
            run_id=row["run_id"],
            trace_id=row["trace_id"],
            causation_id=row["causation_id"],
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            payload=json.loads(row["payload_json"]),
            meta=json.loads(row["meta_json"]),
            prev_event_hash=row["prev_event_hash"],
            event_hash=row["event_hash"],
        )
