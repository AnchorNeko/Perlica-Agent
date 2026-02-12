"""Persistent session storage for Perlica conversations."""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from perlica.kernel.types import now_ms


def estimate_tokens_from_text(text: str) -> int:
    """Approximate token count with a chars/4 heuristic."""

    if not text:
        return 0
    return max(1, int(math.ceil(len(text) / 4.0)))


def estimate_tokens_from_payload(payload: Dict[str, Any]) -> int:
    return estimate_tokens_from_text(json.dumps(payload, ensure_ascii=True, sort_keys=True))


@dataclass
class SessionRecord:
    session_id: str
    context_id: str
    name: Optional[str]
    provider_locked: Optional[str]
    is_ephemeral: bool
    saved_at_ms: Optional[int]
    created_at_ms: int
    updated_at_ms: int
    last_used_at_ms: int


@dataclass
class SessionMessageRecord:
    message_id: str
    session_id: str
    seq: int
    role: str
    content: Dict[str, Any]
    estimated_tokens: int
    run_id: str
    created_at_ms: int


@dataclass
class SessionSummaryRecord:
    summary_id: str
    session_id: str
    version: int
    covered_upto_seq: int
    summary_text: str
    estimated_tokens: int
    created_at_ms: int


class SessionStore:
    """Stores sessions, current pointers, message history, and summaries."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                context_id TEXT NOT NULL,
                name TEXT,
                provider_locked TEXT,
                is_ephemeral INTEGER NOT NULL DEFAULT 0,
                saved_at_ms INTEGER,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                last_used_at_ms INTEGER NOT NULL
            )
            """
        )
        self._migrate_sessions_columns(cursor)
        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_context_name
            ON sessions (context_id, name)
            WHERE name IS NOT NULL
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS session_state (
                context_id TEXT PRIMARY KEY,
                current_session_id TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS session_messages (
                message_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                role TEXT NOT NULL,
                content_json TEXT NOT NULL,
                estimated_tokens INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                UNIQUE(session_id, seq)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS session_summaries (
                summary_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                covered_upto_seq INTEGER NOT NULL,
                summary_text TEXT NOT NULL,
                estimated_tokens INTEGER NOT NULL,
                created_at_ms INTEGER NOT NULL,
                UNIQUE(session_id, version)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_messages_session_seq ON session_messages (session_id, seq)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_summaries_session_version ON session_summaries (session_id, version)"
        )
        self._conn.commit()

    @staticmethod
    def _migrate_sessions_columns(cursor: sqlite3.Cursor) -> None:
        cols = {
            str(row["name"])
            for row in cursor.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "is_ephemeral" not in cols:
            cursor.execute(
                "ALTER TABLE sessions ADD COLUMN is_ephemeral INTEGER NOT NULL DEFAULT 0"
            )
        if "saved_at_ms" not in cols:
            cursor.execute("ALTER TABLE sessions ADD COLUMN saved_at_ms INTEGER")

    def create_session(
        self,
        context_id: str,
        name: Optional[str] = None,
        provider_locked: Optional[str] = None,
        is_ephemeral: bool = False,
    ) -> SessionRecord:
        with self._lock:
            ts = now_ms()
            session = SessionRecord(
                session_id="sess_{0}".format(uuid.uuid4().hex),
                context_id=context_id,
                name=name,
                provider_locked=provider_locked,
                is_ephemeral=bool(is_ephemeral),
                saved_at_ms=None,
                created_at_ms=ts,
                updated_at_ms=ts,
                last_used_at_ms=ts,
            )
            self._conn.execute(
                """
                INSERT INTO sessions (
                    session_id, context_id, name, provider_locked,
                    is_ephemeral, saved_at_ms, created_at_ms, updated_at_ms, last_used_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.context_id,
                    session.name,
                    session.provider_locked,
                    1 if session.is_ephemeral else 0,
                    session.saved_at_ms,
                    session.created_at_ms,
                    session.updated_at_ms,
                    session.last_used_at_ms,
                ),
            )
            self._conn.commit()
            return session

    def list_sessions(
        self,
        context_id: Optional[str] = None,
        include_ephemeral: bool = False,
    ) -> List[SessionRecord]:
        ephemeral_filter = ""
        if not include_ephemeral:
            ephemeral_filter = " AND is_ephemeral = 0"

        if context_id:
            rows = self._conn.execute(
                """
                SELECT * FROM sessions
                WHERE context_id = ?{0}
                ORDER BY last_used_at_ms DESC, created_at_ms DESC
                """.format(ephemeral_filter),
                (context_id,),
            ).fetchall()
        else:
            where = ""
            if not include_ephemeral:
                where = "WHERE is_ephemeral = 0"
            rows = self._conn.execute(
                """
                SELECT * FROM sessions
                {0}
                ORDER BY context_id ASC, last_used_at_ms DESC, created_at_ms DESC
                """.format(where)
            ).fetchall()
        return [self._row_to_session(row) for row in rows]

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        row = self._conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def resolve_session_ref(self, context_id: str, session_ref: str) -> SessionRecord:
        session_ref = session_ref.strip()
        if not session_ref:
            raise ValueError("session_ref cannot be empty")

        exact_id = self._conn.execute(
            "SELECT * FROM sessions WHERE context_id = ? AND session_id = ?",
            (context_id, session_ref),
        ).fetchone()
        if exact_id is not None:
            return self._row_to_session(exact_id)

        prefix_rows = self._conn.execute(
            "SELECT * FROM sessions WHERE context_id = ? AND session_id LIKE ? ORDER BY session_id ASC",
            (context_id, session_ref + "%"),
        ).fetchall()
        if len(prefix_rows) == 1:
            return self._row_to_session(prefix_rows[0])
        if len(prefix_rows) > 1:
            candidates = [str(row["session_id"]) for row in prefix_rows[:8]]
            raise ValueError(
                "ambiguous session_ref '{0}', candidates: {1}".format(session_ref, ", ".join(candidates))
            )

        exact_name = self._conn.execute(
            "SELECT * FROM sessions WHERE context_id = ? AND name = ?",
            (context_id, session_ref),
        ).fetchone()
        if exact_name is not None:
            return self._row_to_session(exact_name)

        candidates_rows = self._conn.execute(
            "SELECT session_id, name FROM sessions WHERE context_id = ? ORDER BY last_used_at_ms DESC LIMIT 8",
            (context_id,),
        ).fetchall()
        candidates = [
            "{0}{1}".format(
                str(row["session_id"]),
                " ({0})".format(str(row["name"])) if row["name"] else "",
            )
            for row in candidates_rows
        ]
        suffix = ""
        if candidates:
            suffix = " candidates: {0}".format(", ".join(candidates))
        raise ValueError("session not found: {0}.{1}".format(session_ref, suffix))

    def get_current_session(self, context_id: str) -> Optional[SessionRecord]:
        row = self._conn.execute(
            "SELECT current_session_id FROM session_state WHERE context_id = ?",
            (context_id,),
        ).fetchone()
        if row is None:
            return None

        session = self.get_session(str(row["current_session_id"]))
        if session is None:
            return None
        if session.context_id != context_id:
            return None
        return session

    def is_unsaved_ephemeral(self, session_id: str) -> bool:
        session = self.get_session(session_id)
        if session is None:
            return False
        return bool(session.is_ephemeral and session.saved_at_ms is None)

    def set_current_session(self, context_id: str, session_id: str) -> None:
        with self._lock:
            ts = now_ms()
            self._conn.execute(
                """
                INSERT INTO session_state (context_id, current_session_id, updated_at_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(context_id)
                DO UPDATE SET current_session_id = excluded.current_session_id,
                              updated_at_ms = excluded.updated_at_ms
                """,
                (context_id, session_id, ts),
            )
            self._conn.commit()

    def lock_provider(self, session_id: str, provider_id: str) -> SessionRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT provider_locked FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("session not found: {0}".format(session_id))

            locked = row["provider_locked"]
            if locked and str(locked) != provider_id:
                raise ValueError(
                    "session {0} is locked to provider '{1}', requested '{2}'".format(
                        session_id,
                        str(locked),
                        provider_id,
                    )
                )

            ts = now_ms()
            new_locked = str(locked) if locked else provider_id
            self._conn.execute(
                """
                UPDATE sessions
                SET provider_locked = ?, updated_at_ms = ?, last_used_at_ms = ?
                WHERE session_id = ?
                """,
                (new_locked, ts, ts, session_id),
            )
            self._conn.commit()

        updated = self.get_session(session_id)
        if updated is None:
            raise ValueError("session disappeared: {0}".format(session_id))
        return updated

    def save_session(self, session_id: str, name: Optional[str] = None) -> SessionRecord:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise ValueError("session not found: {0}".format(session_id))

            context_id = str(row["context_id"])
            existing_name = str(row["name"]) if row["name"] is not None else None
            requested_name = (name or "").strip() or existing_name
            if not requested_name:
                requested_name = self._allocate_persistent_name(context_id)

            ts = now_ms()
            saved_at = row["saved_at_ms"] if row["saved_at_ms"] is not None else ts
            try:
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET name = ?,
                        is_ephemeral = 0,
                        saved_at_ms = ?,
                        updated_at_ms = ?,
                        last_used_at_ms = ?
                    WHERE session_id = ?
                    """,
                    (requested_name, saved_at, ts, ts, session_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "session name already exists in context '{0}': {1}".format(
                        context_id,
                        requested_name,
                    )
                ) from exc
            self._conn.commit()

        saved = self.get_session(session_id)
        if saved is None:
            raise ValueError("session disappeared: {0}".format(session_id))
        return saved

    def discard_session(self, session_id: str) -> None:
        with self._lock:
            self._delete_session_locked(session_id)
            self._conn.commit()

    def cleanup_unsaved_ephemeral(self, context_id: Optional[str] = None) -> int:
        with self._lock:
            if context_id:
                rows = self._conn.execute(
                    """
                    SELECT session_id FROM sessions
                    WHERE context_id = ? AND is_ephemeral = 1 AND saved_at_ms IS NULL
                    """,
                    (context_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT session_id FROM sessions
                    WHERE is_ephemeral = 1 AND saved_at_ms IS NULL
                    """
                ).fetchall()

            session_ids = [str(row["session_id"]) for row in rows]
            for session_id in session_ids:
                self._delete_session_locked(session_id)

            self._conn.commit()
            return len(session_ids)

    def touch_session(self, session_id: str) -> None:
        with self._lock:
            ts = now_ms()
            self._conn.execute(
                "UPDATE sessions SET updated_at_ms = ?, last_used_at_ms = ? WHERE session_id = ?",
                (ts, ts, session_id),
            )
            self._conn.commit()

    def append_message(self, session_id: str, role: str, content: Dict[str, Any], run_id: str) -> SessionMessageRecord:
        with self._lock:
            next_seq = self._next_message_seq(session_id)
            record = SessionMessageRecord(
                message_id="msg_{0}".format(uuid.uuid4().hex),
                session_id=session_id,
                seq=next_seq,
                role=role,
                content=dict(content),
                estimated_tokens=estimate_tokens_from_payload(content),
                run_id=run_id,
                created_at_ms=now_ms(),
            )
            self._conn.execute(
                """
                INSERT INTO session_messages (
                    message_id, session_id, seq, role, content_json,
                    estimated_tokens, run_id, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.message_id,
                    record.session_id,
                    record.seq,
                    record.role,
                    json.dumps(record.content, ensure_ascii=True),
                    record.estimated_tokens,
                    record.run_id,
                    record.created_at_ms,
                ),
            )
            self._conn.commit()
            return record

    def get_session_context_counts(self, session_id: str) -> Dict[str, int]:
        with self._lock:
            msg_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM session_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            sum_row = self._conn.execute(
                "SELECT COUNT(*) AS count FROM session_summaries WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return {
                "messages": int(msg_row["count"] or 0),
                "summaries": int(sum_row["count"] or 0),
            }

    def clear_session_context(self, session_id: str) -> int:
        with self._lock:
            deleted_messages = self._conn.execute(
                "DELETE FROM session_messages WHERE session_id = ?",
                (session_id,),
            ).rowcount or 0
            deleted_summaries = self._conn.execute(
                "DELETE FROM session_summaries WHERE session_id = ?",
                (session_id,),
            ).rowcount or 0
            self._conn.commit()
            return int(deleted_messages) + int(deleted_summaries)

    def list_messages(self, session_id: str, after_seq: int = 0) -> List[SessionMessageRecord]:
        rows = self._conn.execute(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (session_id, after_seq),
        ).fetchall()
        return [self._row_to_message(row) for row in rows]

    def get_latest_summary(self, session_id: str) -> Optional[SessionSummaryRecord]:
        row = self._conn.execute(
            """
            SELECT * FROM session_summaries
            WHERE session_id = ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_summary(row)

    def add_summary(self, session_id: str, covered_upto_seq: int, summary_text: str) -> SessionSummaryRecord:
        with self._lock:
            version = self._next_summary_version(session_id)
            record = SessionSummaryRecord(
                summary_id="sum_{0}".format(uuid.uuid4().hex),
                session_id=session_id,
                version=version,
                covered_upto_seq=covered_upto_seq,
                summary_text=summary_text,
                estimated_tokens=estimate_tokens_from_text(summary_text),
                created_at_ms=now_ms(),
            )
            self._conn.execute(
                """
                INSERT INTO session_summaries (
                    summary_id, session_id, version, covered_upto_seq,
                    summary_text, estimated_tokens, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.summary_id,
                    record.session_id,
                    record.version,
                    record.covered_upto_seq,
                    record.summary_text,
                    record.estimated_tokens,
                    record.created_at_ms,
                ),
            )
            self._conn.commit()
            return record

    def close(self) -> None:
        self._conn.close()

    def _delete_session_locked(self, session_id: str) -> None:
        self._conn.execute(
            "DELETE FROM session_messages WHERE session_id = ?",
            (session_id,),
        )
        self._conn.execute(
            "DELETE FROM session_summaries WHERE session_id = ?",
            (session_id,),
        )
        self._conn.execute(
            "DELETE FROM session_state WHERE current_session_id = ?",
            (session_id,),
        )
        self._conn.execute(
            "DELETE FROM sessions WHERE session_id = ?",
            (session_id,),
        )

    def _allocate_persistent_name(self, context_id: str) -> str:
        base = datetime.now().strftime("chat-%Y%m%d-%H%M%S")
        for idx in range(1, 500):
            candidate = base if idx == 1 else "{0}-{1}".format(base, idx)
            if not self._session_name_exists(context_id=context_id, name=candidate):
                return candidate
        return "{0}-saved".format(base)

    def _session_name_exists(
        self,
        context_id: str,
        name: str,
        exclude_session_id: Optional[str] = None,
    ) -> bool:
        if exclude_session_id:
            row = self._conn.execute(
                """
                SELECT 1 FROM sessions
                WHERE context_id = ? AND name = ? AND session_id != ?
                LIMIT 1
                """,
                (context_id, name, exclude_session_id),
            ).fetchone()
            return row is not None
        row = self._conn.execute(
            """
            SELECT 1 FROM sessions
            WHERE context_id = ? AND name = ?
            LIMIT 1
            """,
            (context_id, name),
        ).fetchone()
        return row is not None

    def _next_message_seq(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM session_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["max_seq"] or 0) + 1

    def _next_summary_version(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS max_version FROM session_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["max_version"] or 0) + 1

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> SessionRecord:
        return SessionRecord(
            session_id=str(row["session_id"]),
            context_id=str(row["context_id"]),
            name=str(row["name"]) if row["name"] is not None else None,
            provider_locked=str(row["provider_locked"]) if row["provider_locked"] is not None else None,
            is_ephemeral=bool(int(row["is_ephemeral"] or 0)),
            saved_at_ms=int(row["saved_at_ms"]) if row["saved_at_ms"] is not None else None,
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
            last_used_at_ms=int(row["last_used_at_ms"]),
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> SessionMessageRecord:
        return SessionMessageRecord(
            message_id=str(row["message_id"]),
            session_id=str(row["session_id"]),
            seq=int(row["seq"]),
            role=str(row["role"]),
            content=dict(json.loads(row["content_json"])),
            estimated_tokens=int(row["estimated_tokens"]),
            run_id=str(row["run_id"]),
            created_at_ms=int(row["created_at_ms"]),
        )

    @staticmethod
    def _row_to_summary(row: sqlite3.Row) -> SessionSummaryRecord:
        return SessionSummaryRecord(
            summary_id=str(row["summary_id"]),
            session_id=str(row["session_id"]),
            version=int(row["version"]),
            covered_upto_seq=int(row["covered_upto_seq"]),
            summary_text=str(row["summary_text"]),
            estimated_tokens=int(row["estimated_tokens"]),
            created_at_ms=int(row["created_at_ms"]),
        )
