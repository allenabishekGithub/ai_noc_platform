# File: controller_cache.py
"""
Thread-safe TTL cache for controller-backed inventory.

The agent uses this cache as a local, expiring working set for controller
resources such as contexts, topologies, devices, links, services, and topology
details. The controller remains the source of truth.
"""
import copy
import logging
import threading
import time
from typing import Any, Dict, Optional

from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)


class ControllerCache:
    def __init__(self, default_ttl_seconds: int = 120):
        self._lock = threading.RLock()
        self._default_ttl_seconds = max(1, int(default_ttl_seconds or 120))
        self._entries: Dict[str, Dict[str, Any]] = {}

    def _make_key(self, resource_type: str, scope: str) -> str:
        return f"{resource_type}::{scope}"

    def _age_seconds(self, updated_at: float) -> float:
        return max(0.0, time.time() - float(updated_at or 0.0))

    def _entry_summary(self, payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            summary = {"keys": sorted(payload.keys())[:12]}
            for key in (
                "contexts",
                "context_ids",
                "topologies",
                "topology_ids",
                "devices",
                "device_ids",
                "links",
                "link_ids",
                "services",
                "service_ids",
                "slices",
                "slice_ids",
                "connections",
                "connection_ids",
            ):
                value = payload.get(key)
                if isinstance(value, list):
                    summary[key] = len(value)
            return summary
        if isinstance(payload, list):
            return {"items": len(payload)}
        return {"type": type(payload).__name__}

    def put(
        self,
        resource_type: str,
        payload: Any,
        scope: str = "default",
        ttl_seconds: Optional[int] = None,
        source_tool: str = "unknown",
    ) -> None:
        ttl = max(1, int(ttl_seconds or self._default_ttl_seconds))
        key = self._make_key(resource_type, scope)
        with timed_event(
            LOGGER,
            "controller_cache_put",
            resource_type=resource_type,
            scope=scope,
        ):
            with self._lock:
                self._entries[key] = {
                    "resource_type": resource_type,
                    "scope": scope,
                    "payload": copy.deepcopy(payload),
                    "updated_at": time.time(),
                    "ttl_seconds": ttl,
                    "source_tool": source_tool,
                }

    def get(
        self,
        resource_type: str,
        scope: str = "default",
        allow_stale: bool = True,
    ) -> Optional[Dict[str, Any]]:
        key = self._make_key(resource_type, scope)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            age = self._age_seconds(entry["updated_at"])
            fresh = age <= float(entry["ttl_seconds"])
            if not fresh and not allow_stale:
                return None
            payload = copy.deepcopy(entry["payload"])
            return {
                "resource_type": entry["resource_type"],
                "scope": entry["scope"],
                "payload": payload,
                "updated_at": entry["updated_at"],
                "ttl_seconds": entry["ttl_seconds"],
                "age_seconds": round(age, 3),
                "fresh": fresh,
                "source_tool": entry["source_tool"],
            }

    def is_fresh(self, resource_type: str, scope: str = "default") -> bool:
        entry = self.get(resource_type, scope=scope, allow_stale=True)
        return bool(entry and entry["fresh"])

    def invalidate(
        self,
        resource_type: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> None:
        with self._lock:
            if resource_type is None and scope is None:
                self._entries.clear()
                return

            to_delete = []
            for key, entry in self._entries.items():
                if resource_type is not None and entry["resource_type"] != resource_type:
                    continue
                if scope is not None and entry["scope"] != scope:
                    continue
                to_delete.append(key)
            for key in to_delete:
                self._entries.pop(key, None)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            resources: Dict[str, Dict[str, Any]] = {}
            for key, entry in self._entries.items():
                age = self._age_seconds(entry["updated_at"])
                resources[key] = {
                    "resource_type": entry["resource_type"],
                    "scope": entry["scope"],
                    "updated_at": entry["updated_at"],
                    "ttl_seconds": entry["ttl_seconds"],
                    "age_seconds": round(age, 3),
                    "fresh": age <= float(entry["ttl_seconds"]),
                    "source_tool": entry["source_tool"],
                    "summary": self._entry_summary(entry["payload"]),
                }
            return {
                "default_ttl_seconds": self._default_ttl_seconds,
                "resources": resources,
            }
