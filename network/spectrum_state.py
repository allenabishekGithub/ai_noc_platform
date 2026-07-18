# File: spectrum_state.py
"""
Thread-safe in-memory store for live optical spectrum state.

Holds the latest information returned by MCP tools (e.g. feasible candidate
slot ranges per border-link, soft reservations, commit/rollback outcomes)
so that:

  * The dynamic A2A Agent Card can publish a snapshot of the agent's
    current spectrum state for peers to read.
  * Skill-dispatched A2A handlers can answer peer agents without
    re-querying the MCP server on every request.
  * The LangChain agent can inspect the current state via a tool.

The store is intentionally simple (a dict + RLock) - it is the local,
non-authoritative cache. The MCP server / SDN controller remain the
source of truth.
"""
import logging
import threading
import time
import uuid
from typing import Any, Dict, List, Optional
from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)


class SpectrumState:
    def __init__(self):
        self._lock = threading.RLock()
        # Per-border-link entries:
        #   { border_link: {"candidate_ranges": [...], "required_slots": int,
        #                    "guard_band_slots": int, "updated_at": float,
        #                    "source_tool": str} }
        self._candidates: Dict[str, Dict[str, Any]] = {}
        # Soft / committed reservations:
        #   { reservation_id: {"border_link": str, "n_start": int, "n_end": int,
        #                       "service_id": str, "status": "soft|committed|rolled_back",
        #                       "updated_at": float} }
        self._reservations: Dict[str, Dict[str, Any]] = {}
        # Capabilities discovered from MCP servers:
        #   { tool_name: {"server": url, "description": str} }
        self._mcp_capabilities: Dict[str, Dict[str, Any]] = {}
        # Monotonic state version, bumped on every mutation. Lets peers
        # detect whether the Agent Card has new information without
        # diffing the whole payload.
        self._version: int = 0
        self._updated_at: float = time.time()

    # ------------------------------------------------------------------ #
    # Internal                                                           #
    # ------------------------------------------------------------------ #
    def _bump(self):
        self._version += 1
        self._updated_at = time.time()

    # ------------------------------------------------------------------ #
    # Capabilities                                                       #
    # ------------------------------------------------------------------ #
    def set_capabilities(self, capabilities: Dict[str, Dict[str, Any]]):
        with timed_event(LOGGER, "spectrum_set_capabilities", count=len(capabilities)):
            with self._lock:
                self._mcp_capabilities = dict(capabilities)
                self._bump()
                LOGGER.info(
                    f"SpectrumState: registered {len(self._mcp_capabilities)} "
                    f"MCP capability/ies"
                )

    def capabilities(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return dict(self._mcp_capabilities)

    # ------------------------------------------------------------------ #
    # Candidate ranges                                                   #
    # ------------------------------------------------------------------ #
    def update_candidates(
        self,
        border_link: str,
        candidate_ranges: List[Dict[str, int]],
        required_slots: Optional[int] = None,
        guard_band_slots: Optional[int] = None,
        source_tool: str = "unknown",
    ):
        if not border_link:
            return
        with timed_event(LOGGER, "spectrum_update_candidates", border_link=border_link):
            with self._lock:
                entry = self._candidates.get(border_link, {})
                entry.update({
                    "candidate_ranges": candidate_ranges or [],
                    "updated_at": time.time(),
                    "source_tool": source_tool,
                })
                if required_slots is not None:
                    entry["required_slots"] = required_slots
                if guard_band_slots is not None:
                    entry["guard_band_slots"] = guard_band_slots
                self._candidates[border_link] = entry
                self._bump()
                LOGGER.info(
                    f"SpectrumState: candidates for {border_link} updated "
                    f"({len(entry['candidate_ranges'])} range(s), v{self._version})"
                )

    def get_candidates(self, border_link: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._candidates.get(border_link, {}))

    def all_candidates(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._candidates.items()}

    # ------------------------------------------------------------------ #
    # Reservations                                                       #
    # ------------------------------------------------------------------ #
    def add_reservation(
        self,
        border_link: str,
        n_start: int,
        n_end: int,
        service_id: str,
        status: str = "soft",
        reservation_id: Optional[str] = None,
    ) -> str:
        rid = reservation_id or f"res-{uuid.uuid4().hex[:8]}"
        with timed_event(
            LOGGER,
            "spectrum_add_reservation",
            border_link=border_link,
            reservation_id=rid,
        ):
            with self._lock:
                self._reservations[rid] = {
                    "border_link": border_link,
                    "n_start": n_start,
                    "n_end": n_end,
                    "service_id": service_id,
                    "status": status,
                    "updated_at": time.time(),
                }
                self._bump()
                LOGGER.info(
                    f"SpectrumState: reservation {rid} {status} "
                    f"on {border_link} [{n_start},{n_end}]"
                )
                return rid

    def update_reservation(self, reservation_id: str, status: str):
        with timed_event(
            LOGGER,
            "spectrum_update_reservation",
            reservation_id=reservation_id,
            status=status,
        ):
            with self._lock:
                r = self._reservations.get(reservation_id)
                if not r:
                    return
                r["status"] = status
                r["updated_at"] = time.time()
                self._bump()

    def reservations(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._reservations.items()}

    # ------------------------------------------------------------------ #
    # Snapshot for the Agent Card / peer queries                         #
    # ------------------------------------------------------------------ #
    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state_version": self._version,
                "updated_at": self._updated_at,
                "candidates": {k: dict(v) for k, v in self._candidates.items()},
                "reservations": {
                    k: dict(v) for k, v in self._reservations.items()
                },
                "mcp_capabilities": list(self._mcp_capabilities.keys()),
            }

    @property
    def version(self) -> int:
        with self._lock:
            return self._version
