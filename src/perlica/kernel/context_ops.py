"""Context-level utility operations for sessions."""

from __future__ import annotations

from typing import Dict

from perlica.kernel.session_store import SessionStore


def clear_session_context(
    session_store: SessionStore,
    *,
    context_id: str,
    session_id: str,
) -> Dict[str, int]:
    """Clear message/summary context for one session while preserving session metadata."""

    session = session_store.get_session(session_id)
    if session is None:
        raise ValueError("session not found: {0}".format(session_id))
    if session.context_id != context_id:
        raise ValueError(
            "session context mismatch: expected={0} actual={1}".format(
                context_id,
                session.context_id,
            )
        )

    before = session_store.get_session_context_counts(session_id)
    total_deleted = session_store.clear_session_context(session_id)
    return {
        "deleted_messages": int(before.get("messages") or 0),
        "deleted_summaries": int(before.get("summaries") or 0),
        "total_deleted": int(total_deleted),
    }

