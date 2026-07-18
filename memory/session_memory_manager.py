# File: session_memory_manager.py
"""
SessionMemoryManager — centralised, thread-safe per-session memory for the
TFS Agentic Module.

Owns all mutable state that the LangChain agent carries between turns:

  * conversation_history  — LangChain HumanMessage / AIMessage pairs, capped
                            to avoid unbounded context growth.
  * working_memory        — free-text facts the agent saves across turns via
                            the update_working_memory tool.
  * traces                — the last execution trace per session (for the UI).

Each entry is isolated by session_id. Concurrent access is safe via a
per-session RLock plus a global lock protecting the sessions index.
"""
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage

from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)

_DEFAULT_MAX_HISTORY = 20


class _SessionState:
    """All memory for a single session. Internal to SessionMemoryManager."""

    def __init__(self, session_id: str, max_history: int) -> None:
        self.session_id = session_id
        self.max_history = max_history
        self.lock = threading.RLock()
        self.history: List[Any] = []
        self.working_memory: str = ""
        self.last_trace: Dict[str, Any] = {}
        self.created_at: float = time.time()
        self.last_active_at: float = time.time()


class SessionMemoryManager:
    """
    Centralised per-session memory store for the TFS Agentic Module.

    Instantiated once by AutonomousManager and injected into LangChainAgent.
    Replaces the scattered _sessions, _working_memory, and _latest_traces dicts
    that previously lived inside the agent.
    """

    def __init__(self, max_history: int = _DEFAULT_MAX_HISTORY) -> None:
        self._max_history = max(2, int(max_history))
        self._global_lock = threading.RLock()
        self._sessions: Dict[str, _SessionState] = {}
        LOGGER.info(
            "SessionMemoryManager initialised (max_history=%d per session)",
            self._max_history,
        )

    # ------------------------------------------------------------------ #
    # Session lifecycle                                                   #
    # ------------------------------------------------------------------ #

    def _get_or_create(self, session_id: str) -> _SessionState:
        with self._global_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = _SessionState(
                    session_id, self._max_history
                )
                LOGGER.debug("SessionMemoryManager: created session '%s'", session_id)
            state = self._sessions[session_id]
        state.last_active_at = time.time()
        return state

    def clear_session(self, session_id: str) -> None:
        """Remove all memory for a session (conversation, working memory, pending ops)."""
        with self._global_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                LOGGER.info(
                    "SessionMemoryManager: cleared session '%s'", session_id
                )

    def active_sessions(self) -> List[str]:
        """Return the list of session IDs with allocated state."""
        with self._global_lock:
            return list(self._sessions.keys())

    def session_summary(self, session_id: str) -> Dict[str, Any]:
        """Return a lightweight status dict for one session."""
        state = self._get_or_create(session_id)
        with state.lock:
            return {
                "session_id": session_id,
                "history_turns": len(state.history) // 2,
                "working_memory_chars": len(state.working_memory),
                "created_at": state.created_at,
                "last_active_at": state.last_active_at,
            }

    def all_summaries(self) -> List[Dict[str, Any]]:
        """Return status dicts for all active sessions."""
        with self._global_lock:
            session_ids = list(self._sessions.keys())
        return [self.session_summary(sid) for sid in session_ids]

    # ------------------------------------------------------------------ #
    # Conversation history                                                #
    # ------------------------------------------------------------------ #

    def get_history(self, session_id: str) -> List[Any]:
        """Return the LangChain message list for a session (a live reference)."""
        state = self._get_or_create(session_id)
        with state.lock:
            return state.history

    def add_turn(self, session_id: str, query: str, answer: str) -> None:
        """
        Append a human/AI message pair and cap history at max_history messages.
        Call this once after every completed agent turn.
        """
        state = self._get_or_create(session_id)
        with state.lock:
            state.history.append(HumanMessage(content=query))
            state.history.append(AIMessage(content=answer))
            if len(state.history) > self._max_history:
                del state.history[: len(state.history) - self._max_history]

    # ------------------------------------------------------------------ #
    # Working memory                                                      #
    # ------------------------------------------------------------------ #

    def get_working_memory(self, session_id: str) -> str:
        """Return the agent's current working memory notes for a session."""
        state = self._get_or_create(session_id)
        with state.lock:
            return state.working_memory

    def set_working_memory(self, session_id: str, content: str) -> None:
        """Overwrite the working memory for a session (called via the agent tool)."""
        state = self._get_or_create(session_id)
        with state.lock:
            state.working_memory = (content or "").strip()
        LOGGER.debug(
            "SessionMemoryManager: working_memory updated for '%s' (%d chars)",
            session_id,
            len(state.working_memory),
        )

    # ------------------------------------------------------------------ #
    # Traces                                                              #
    # ------------------------------------------------------------------ #

    def set_trace(self, session_id: str, trace: Dict[str, Any]) -> None:
        """Store the most recent execution trace for a session."""
        state = self._get_or_create(session_id)
        with state.lock:
            state.last_trace = trace

    def get_trace(self, session_id: str) -> Dict[str, Any]:
        """Return the most recent execution trace for a session."""
        state = self._get_or_create(session_id)
        with state.lock:
            return dict(state.last_trace)

    # ------------------------------------------------------------------ #
    # Input augmentation                                                  #
    # ------------------------------------------------------------------ #

    def build_augmented_input(self, session_id: str, query: str) -> str:
        """
        Build the full input string passed to the LangChain agent, prepending
        active working memory before the user's query. Returns the raw query
        unchanged if no working memory is active.
        """
        state = self._get_or_create(session_id)
        with state.lock:
            if not state.working_memory:
                return query
            return (
                f"[Working memory from previous turns]\n{state.working_memory}"
                f"\n\n[User message]\n{query}"
            )
