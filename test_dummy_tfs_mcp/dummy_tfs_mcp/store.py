from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class DummyInventoryStore:
    def __init__(self, topology_path: str | None = None):
        default_path = Path(__file__).resolve().parents[2] / "docs" / "dummy_contexts.json"
        self.topology_path = Path(topology_path) if topology_path else default_path
        payload = json.loads(self.topology_path.read_text(encoding="utf-8"))
        self.contexts = payload.get("contexts", [])
        self.topologies = payload.get("topologies", [])
        self.devices = payload.get("devices", [])
        self.links = payload.get("links", [])

        self._context_index = {
            self._context_uuid(context): context for context in self.contexts
        }
        self._topology_index = {
            self._topology_uuid(topology): topology for topology in self.topologies
        }
        self._device_index = {
            self._device_uuid(device): device for device in self.devices
        }
        self._link_index = {
            self._link_uuid(link): link for link in self.links
        }

    @staticmethod
    def _context_uuid(context: dict[str, Any]) -> str:
        return (
            context.get("context_id", {})
            .get("context_uuid", {})
            .get("uuid", "")
        )

    @staticmethod
    def _topology_uuid(topology: dict[str, Any]) -> str:
        return (
            topology.get("topology_id", {})
            .get("topology_uuid", {})
            .get("uuid", "")
        )

    @staticmethod
    def _device_uuid(device: dict[str, Any]) -> str:
        return (
            device.get("device_id", {})
            .get("device_uuid", {})
            .get("uuid", "")
        )

    @staticmethod
    def _link_uuid(link: dict[str, Any]) -> str:
        return (
            link.get("link_id", {})
            .get("link_uuid", {})
            .get("uuid", "")
        )

    def list_context_ids(self) -> dict[str, Any]:
        return {
            "context_ids": [
                {"context_uuid": {"uuid": context_uuid}}
                for context_uuid in self._context_index
            ]
        }

    def list_contexts(self) -> dict[str, Any]:
        contexts = []
        for context in self.contexts:
            context_uuid = self._context_uuid(context)
            topology_ids = [
                topology["topology_id"]
                for topology in self.topologies
                if topology.get("topology_id", {})
                .get("context_id", {})
                .get("context_uuid", {})
                .get("uuid", "") == context_uuid
            ]
            contexts.append(
                {
                    "context_id": context["context_id"],
                    "name": context_uuid,
                    "topology_ids": topology_ids,
                    "service_ids": [],
                    "slice_ids": [],
                }
            )
        return {"contexts": contexts}

    def get_context(self, context_uuid: str) -> dict[str, Any]:
        if context_uuid not in self._context_index:
            return {"status": "error", "error": f"Unknown context_uuid: {context_uuid}"}
        contexts = self.list_contexts()["contexts"]
        for context in contexts:
            if self._context_uuid(context) == context_uuid:
                return context
        return {"status": "error", "error": f"Unknown context_uuid: {context_uuid}"}

    def list_topology_ids(self, context_uuid: str) -> dict[str, Any]:
        topology_ids = [
            topology["topology_id"]
            for topology in self.topologies
            if topology.get("topology_id", {})
            .get("context_id", {})
            .get("context_uuid", {})
            .get("uuid", "") == context_uuid
        ]
        return {"topology_ids": topology_ids}

    def list_topologies(self, context_uuid: str) -> dict[str, Any]:
        if context_uuid not in self._context_index:
            return {"status": "error", "error": f"Unknown context_uuid: {context_uuid}"}
        topologies = []
        for topology in self.topologies:
            if topology.get("topology_id", {}).get("context_id", {}).get("context_uuid", {}).get("uuid", "") != context_uuid:
                continue
            topologies.append(
                {
                    "topology_id": topology["topology_id"],
                    "name": self._topology_uuid(topology),
                    "device_ids": [
                        device["device_id"] for device in self.devices
                    ],
                    "link_ids": [
                        link["link_id"] for link in self.links
                    ],
                }
            )
        return {"topologies": topologies}

    def get_topology(self, context_uuid: str, topology_uuid: str) -> dict[str, Any]:
        topologies = self.list_topologies(context_uuid).get("topologies", [])
        for topology in topologies:
            if topology.get("topology_id", {}).get("topology_uuid", {}).get("uuid", "") == topology_uuid:
                return topology
        return {"status": "error", "error": f"Unknown topology {context_uuid}/{topology_uuid}"}

    def get_topology_details(self, context_uuid: str, topology_uuid: str) -> dict[str, Any]:
        topology = self.get_topology(context_uuid, topology_uuid)
        if topology.get("status") == "error":
            return topology
        return {
            "topology_id": topology["topology_id"],
            "name": topology.get("name", topology_uuid),
            "devices": self.devices,
            "links": self.links,
        }

    def list_device_ids(self) -> dict[str, Any]:
        return {"device_ids": [device["device_id"] for device in self.devices]}

    def list_devices(self) -> dict[str, Any]:
        return {"devices": self.devices}

    def get_device(self, device_uuid: str) -> dict[str, Any]:
        device = self._device_index.get(device_uuid)
        if device is None:
            return {"status": "error", "error": f"Unknown device_uuid: {device_uuid}"}
        return device

    def list_link_ids(self) -> dict[str, Any]:
        return {"link_ids": [link["link_id"] for link in self.links]}

    def list_links(self) -> dict[str, Any]:
        return {"links": self.links}

    def get_link(self, link_uuid: str) -> dict[str, Any]:
        link = self._link_index.get(link_uuid)
        if link is None:
            return {"status": "error", "error": f"Unknown link_uuid: {link_uuid}"}
        return link

    def get_dummy_contexts(self) -> dict[str, Any]:
        return {
            "dummy_mode": True,
            "contexts": self.list_contexts()["contexts"],
            "topologies": self.list_topologies("admin").get("topologies", []),
            "devices": self.devices,
            "links": self.links,
        }
