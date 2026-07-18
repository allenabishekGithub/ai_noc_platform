# File: a2a_client.py
"""
Outbound A2A client.

Lets this agent *talk to* peer agents:

  * Discover a peer's Agent Card at /.well-known/agent-card.json
    (falling back to the legacy /.well-known/agent.json).
  * Fetch the peer's live spectrum-state snapshot exposed at /a2a/state
    (this module's own extension; gracefully degrades if absent).
  * Send free-form A2A messages to the peer's /a2a/message endpoint.
  * Send structured skill-typed task requests to /a2a/task and receive a
    structured artifact in reply.

Designed to be safe when called from sync FastAPI handlers - a thin
synchronous wrapper using `httpx.Client` is provided alongside the async
API.
"""
import logging
from typing import Any, Dict, Optional

import httpx
from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15.0


class A2AClient:
    def __init__(self, sender_id: str, timeout: float = DEFAULT_TIMEOUT):
        self.sender_id = sender_id
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # Discovery                                                          #
    # ------------------------------------------------------------------ #
    def discover(self, peer_base_url: str) -> Dict[str, Any]:
        """Fetch the peer's Agent Card. Tries current spec, then legacy path."""
        base = peer_base_url.rstrip("/")
        with timed_event(LOGGER, "a2a_discover", peer_base_url=base):
            for path in (
                "/.well-known/agent-card.json",
                "/.well-known/agent.json",
            ):
                url = f"{base}{path}"
                try:
                    with httpx.Client(timeout=self.timeout) as c:
                        r = c.get(url)
                    if r.status_code == 200:
                        return r.json()
                except Exception as e:
                    LOGGER.warning(f"discover({url}) failed: {e}")
        raise RuntimeError(f"Could not fetch Agent Card from {peer_base_url}")

    def fetch_state(self, peer_base_url: str) -> Dict[str, Any]:
        """Fetch the peer's live spectrum-state snapshot (this module's API)."""
        url = f"{peer_base_url.rstrip('/')}/a2a/state"
        with timed_event(LOGGER, "a2a_fetch_state", url=url):
            with httpx.Client(timeout=self.timeout) as c:
                r = c.get(url)
                r.raise_for_status()
                return r.json()

    # ------------------------------------------------------------------ #
    # Messaging                                                          #
    # ------------------------------------------------------------------ #
    def send_message(
        self,
        peer_base_url: str,
        message: str,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a free-form A2A message to the peer's /a2a/message endpoint."""
        url = f"{peer_base_url.rstrip('/')}/a2a/message"
        payload = {
            "sender": self.sender_id,
            "message": message,
            "session_id": session_id,
            "metadata": metadata or {},
        }
        with timed_event(LOGGER, "a2a_send_message", url=url):
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(url, json=payload)
                r.raise_for_status()
                return r.json()

    def send_task(
        self,
        peer_base_url: str,
        skill_id: str,
        input_payload: Dict[str, Any],
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a structured, skill-typed A2A task request.

        The peer's /a2a/task handler dispatches by skill_id and returns a
        structured artifact (rather than free-form LLM text).
        """
        url = f"{peer_base_url.rstrip('/')}/a2a/task"
        payload = {
            "sender": self.sender_id,
            "skill_id": skill_id,
            "input": input_payload,
            "session_id": session_id,
        }
        with timed_event(LOGGER, "a2a_send_task", url=url, skill_id=skill_id):
            with httpx.Client(timeout=self.timeout) as c:
                r = c.post(url, json=payload)
                r.raise_for_status()
                return r.json()
