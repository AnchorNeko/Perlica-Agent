"""SQLite persistence for service bridge pairing and cursors."""

from __future__ import annotations

import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from perlica.kernel.types import now_ms
from perlica.service.types import PairingState


class ServiceStore:
    """Persists channel bindings, listener cursors, and pairing codes."""

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
            CREATE TABLE IF NOT EXISTS channel_binding (
                channel TEXT PRIMARY KEY,
                paired INTEGER NOT NULL,
                contact_id TEXT,
                chat_id TEXT,
                session_id TEXT,
                paired_at_ms INTEGER,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS channel_cursor (
                channel TEXT PRIMARY KEY,
                last_event_id TEXT,
                updated_at_ms INTEGER NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pairing_code (
                channel TEXT NOT NULL,
                code TEXT NOT NULL,
                expires_at_ms INTEGER NOT NULL,
                used INTEGER NOT NULL DEFAULT 0,
                created_at_ms INTEGER NOT NULL,
                PRIMARY KEY(channel, code)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_pairing_code_lookup
            ON pairing_code(channel, used, expires_at_ms)
            """
        )
        self._conn.commit()

    def get_binding(self, channel: str) -> PairingState:
        row = self._conn.execute(
            "SELECT * FROM channel_binding WHERE channel = ?",
            (channel,),
        ).fetchone()
        if row is None:
            ts = now_ms()
            return PairingState(
                channel=channel,
                paired=False,
                contact_id=None,
                chat_id=None,
                session_id=None,
                paired_at_ms=None,
                updated_at_ms=ts,
            )
        return PairingState(
            channel=str(row["channel"]),
            paired=bool(int(row["paired"] or 0)),
            contact_id=str(row["contact_id"]) if row["contact_id"] is not None else None,
            chat_id=str(row["chat_id"]) if row["chat_id"] is not None else None,
            session_id=str(row["session_id"]) if row["session_id"] is not None else None,
            paired_at_ms=int(row["paired_at_ms"]) if row["paired_at_ms"] is not None else None,
            updated_at_ms=int(row["updated_at_ms"]),
        )

    def set_binding(
        self,
        channel: str,
        *,
        contact_id: str,
        chat_id: Optional[str],
        session_id: str,
    ) -> PairingState:
        with self._lock:
            ts = now_ms()
            current = self.get_binding(channel)
            paired_at_ms = current.paired_at_ms if current.paired_at_ms else ts
            self._conn.execute(
                """
                INSERT INTO channel_binding (
                    channel, paired, contact_id, chat_id, session_id, paired_at_ms, updated_at_ms
                ) VALUES (?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(channel)
                DO UPDATE SET
                    paired = 1,
                    contact_id = excluded.contact_id,
                    chat_id = excluded.chat_id,
                    session_id = excluded.session_id,
                    paired_at_ms = excluded.paired_at_ms,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (channel, contact_id, chat_id, session_id, paired_at_ms, ts),
            )
            self._conn.commit()
        return self.get_binding(channel)

    def clear_binding(self, channel: str) -> PairingState:
        with self._lock:
            ts = now_ms()
            self._conn.execute(
                """
                INSERT INTO channel_binding (
                    channel, paired, contact_id, chat_id, session_id, paired_at_ms, updated_at_ms
                ) VALUES (?, 0, NULL, NULL, NULL, NULL, ?)
                ON CONFLICT(channel)
                DO UPDATE SET
                    paired = 0,
                    contact_id = NULL,
                    chat_id = NULL,
                    session_id = NULL,
                    paired_at_ms = NULL,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (channel, ts),
            )
            self._conn.commit()
        return self.get_binding(channel)

    def get_cursor(self, channel: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT last_event_id FROM channel_cursor WHERE channel = ?",
            (channel,),
        ).fetchone()
        if row is None:
            return None
        if row["last_event_id"] is None:
            return None
        return str(row["last_event_id"])

    def set_cursor(self, channel: str, last_event_id: str) -> None:
        with self._lock:
            ts = now_ms()
            self._conn.execute(
                """
                INSERT INTO channel_cursor (channel, last_event_id, updated_at_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(channel)
                DO UPDATE SET
                    last_event_id = excluded.last_event_id,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (channel, last_event_id, ts),
            )
            self._conn.commit()

    def create_pairing_code(self, channel: str, ttl_seconds: int = 300) -> str:
        with self._lock:
            now = now_ms()
            self._conn.execute(
                "UPDATE pairing_code SET used = 1 WHERE channel = ? AND used = 0",
                (channel,),
            )

            for _ in range(50):
                code = "{0:06d}".format(secrets.randbelow(1000000))
                try:
                    self._conn.execute(
                        """
                        INSERT INTO pairing_code (channel, code, expires_at_ms, used, created_at_ms)
                        VALUES (?, ?, ?, 0, ?)
                        """,
                        (channel, code, now + max(30, int(ttl_seconds)) * 1000, now),
                    )
                    self._conn.commit()
                    return code
                except sqlite3.IntegrityError:
                    continue

        raise RuntimeError("failed to allocate pairing code")

    def consume_pairing_code(self, channel: str, code: str) -> bool:
        normalized = str(code or "").strip()
        if not normalized:
            return False

        with self._lock:
            now = now_ms()
            row = self._conn.execute(
                """
                SELECT channel, code FROM pairing_code
                WHERE channel = ? AND code = ? AND used = 0 AND expires_at_ms >= ?
                """,
                (channel, normalized, now),
            ).fetchone()
            if row is None:
                return False

            self._conn.execute(
                """
                UPDATE pairing_code
                SET used = 1
                WHERE channel = ? AND code = ?
                """,
                (channel, normalized),
            )
            self._conn.commit()
            return True

    def get_active_pairing_code(self, channel: str) -> Optional[str]:
        now = now_ms()
        row = self._conn.execute(
            """
            SELECT code FROM pairing_code
            WHERE channel = ? AND used = 0 AND expires_at_ms >= ?
            ORDER BY created_at_ms DESC
            LIMIT 1
            """,
            (channel, now),
        ).fetchone()
        if row is None:
            return None
        return str(row["code"])

    def close(self) -> None:
        self._conn.close()
