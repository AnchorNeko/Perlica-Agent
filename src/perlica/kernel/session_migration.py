"""Session migration helpers for provider compatibility transitions."""

from __future__ import annotations

from dataclasses import dataclass

from perlica.kernel.session_store import SessionStore


@dataclass(frozen=True)
class SessionMigrationReport:
    deleted_sessions: int = 0
    deleted_messages: int = 0
    deleted_summaries: int = 0
    fixed_current_state_rows: int = 0


def drop_sessions_by_provider(session_store: SessionStore, provider_id: str) -> SessionMigrationReport:
    """Delete all sessions locked to a specific provider and dependent rows."""

    target = str(provider_id or "").strip().lower()
    if not target:
        return SessionMigrationReport()

    conn = session_store._conn  # pylint: disable=protected-access
    lock = session_store._lock  # pylint: disable=protected-access

    with lock:
        rows = conn.execute(
            "SELECT session_id, context_id FROM sessions WHERE lower(coalesce(provider_locked,'')) = ?",
            (target,),
        ).fetchall()
        if not rows:
            return SessionMigrationReport()

        session_ids = [str(row["session_id"]) for row in rows]
        contexts = {str(row["context_id"]) for row in rows}

        deleted_messages = 0
        deleted_summaries = 0
        for session_id in session_ids:
            deleted_messages += conn.execute(
                "DELETE FROM session_messages WHERE session_id = ?",
                (session_id,),
            ).rowcount or 0
            deleted_summaries += conn.execute(
                "DELETE FROM session_summaries WHERE session_id = ?",
                (session_id,),
            ).rowcount or 0
            conn.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )

        fixed_rows = 0
        for context_id in contexts:
            current = conn.execute(
                "SELECT current_session_id FROM session_state WHERE context_id = ?",
                (context_id,),
            ).fetchone()
            if current is None:
                continue
            current_session_id = str(current["current_session_id"] or "")
            if current_session_id not in session_ids:
                continue
            replacement = conn.execute(
                """
                SELECT session_id
                FROM sessions
                WHERE context_id = ?
                ORDER BY last_used_at_ms DESC, created_at_ms DESC
                LIMIT 1
                """,
                (context_id,),
            ).fetchone()
            if replacement is None:
                conn.execute("DELETE FROM session_state WHERE context_id = ?", (context_id,))
            else:
                conn.execute(
                    "UPDATE session_state SET current_session_id = ?, updated_at_ms = strftime('%s','now') * 1000 WHERE context_id = ?",
                    (str(replacement["session_id"]), context_id),
                )
            fixed_rows += 1

        conn.commit()
        return SessionMigrationReport(
            deleted_sessions=len(session_ids),
            deleted_messages=deleted_messages,
            deleted_summaries=deleted_summaries,
            fixed_current_state_rows=fixed_rows,
        )

