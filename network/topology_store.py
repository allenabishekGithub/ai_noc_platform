# File: topology_store.py
"""
In-memory network topology graph.

The /upload endpoint also feeds parsed YAML/JSON topology files in here so
the agent (and skill-dispatched A2A handlers) have a *queryable* graph
view, not just FAISS text chunks.

Accepted shapes (best-effort: extra keys are ignored):

  # Explicit nodes + links (recommended)
  nodes:
    - {id: DC1, type: dc, domain: A}
    - {id: R1,  type: router, domain: A}
  links:
    - {id: A-B-1, src: R1, dst: R2, border: true,
       slot_width_ghz: 12.5, n_min: -32, n_max: 32}

  # Or under a top-level "topology:" key (same fields beneath it).

  # Or adjacency-style:
  topology:
    DC1: [R1, R2]
    R1: [DC1, R2, R3]

The graph is built with NetworkX. We keep it deliberately simple \u2014 each
upload replaces the topology for that source, indexed by filename.
"""
import logging
import threading
import time
from typing import Any, Dict, List, Optional
from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)

try:
    import networkx as nx
    NX_AVAILABLE = True
except ImportError:  # graceful degradation \u2014 store still parses
    nx = None  # type: ignore
    NX_AVAILABLE = False
    LOGGER.warning(
        "networkx not installed \u2014 TopologyStore will run in dict-only mode. "
        "Install with: pip install networkx"
    )


class TopologyStore:
    def __init__(self):
        self._lock = threading.RLock()
        # source filename -> {"graph": nx.MultiGraph | None,
        #                      "raw": dict, "updated_at": float,
        #                      "border_links": [link_id, ...]}
        self._topologies: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Loading                                                            #
    # ------------------------------------------------------------------ #
    def load(self, source: str, parsed: Any) -> Dict[str, Any]:
        """Parse a previously-decoded YAML/JSON object into a graph.

        Returns a small summary dict for the caller (chunk count, node
        count, link count, border links).
        """
        with timed_event(LOGGER, "topology_load", source=source):
            if not isinstance(parsed, dict):
                return {"loaded": False, "reason": "top-level is not a mapping"}

            # Allow either {"topology": {...}} or fields at the top level.
            body = parsed.get("topology", parsed)

            nodes, links = self._normalize(body)
            graph = self._build_graph(nodes, links) if NX_AVAILABLE else None
            border_links = [l["id"] for l in links if l.get("border")]

            with self._lock:
                self._topologies[source] = {
                    "graph": graph,
                    "raw": {"nodes": nodes, "links": links},
                    "updated_at": time.time(),
                    "border_links": border_links,
                }

            LOGGER.info(
                f"TopologyStore: loaded '{source}' "
                f"({len(nodes)} node(s), {len(links)} link(s), "
                f"{len(border_links)} border link(s))"
            )
            return {
                "loaded": True,
                "source": source,
                "nodes": len(nodes),
                "links": len(links),
                "border_links": border_links,
            }

    # ------------------------------------------------------------------ #
    # Normalisation                                                      #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tfs_format(body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Detect & convert the TFS (TeraFlowSDN) descriptor format.

        TFS uses deeply nested UUID wrappers, e.g.:
            {"device_id": {"device_uuid": {"uuid": "R1"}},
             "device_type": "emu-packet-router", ...}

        Returns a normalised ``{"nodes": [...], "links": [...]}`` dict
        with flat ``id``/``src``/``dst`` keys, or ``None`` if the input
        is not in TFS format.
        """
        if not isinstance(body, dict):
            return None
        if "devices" not in body and "links" not in body:
            return None

        def _uuid(obj: Any, *keys) -> Optional[str]:
            for k in keys:
                if not isinstance(obj, dict):
                    return None
                obj = obj.get(k)
            if isinstance(obj, dict):
                return obj.get("uuid")
            return obj if isinstance(obj, str) else None

        nodes: List[Dict[str, Any]] = []
        for d in body.get("devices") or []:
            if not isinstance(d, dict):
                continue
            nid = _uuid(d, "device_id", "device_uuid")
            if not nid:
                continue
            dtype = d.get("device_type", "")
            # Heuristics: 'datacenter' nodes act like edge endpoints,
            # router-type devices live in the core.
            kind = (
                "dc" if "datacenter" in dtype.lower()
                else "router" if "router" in dtype.lower()
                else dtype or "device"
            )
            nodes.append({
                "id": nid,
                "type": kind,
                "device_type": dtype,
            })

        # Deduplicate undirected links (TFS often lists both A==B and B==A).
        links: List[Dict[str, Any]] = []
        seen_pairs: set = set()
        for l in body.get("links") or []:
            if not isinstance(l, dict):
                continue
            link_id = _uuid(l, "link_id", "link_uuid")
            endpoints = l.get("link_endpoint_ids") or []
            if len(endpoints) < 2:
                continue
            src = _uuid(endpoints[0], "device_id", "device_uuid")
            dst = _uuid(endpoints[1], "device_id", "device_uuid")
            if not src or not dst:
                continue
            pair = tuple(sorted((src, dst)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            src_ep = endpoints[0].get("endpoint_uuid", {}).get("uuid")
            dst_ep = endpoints[1].get("endpoint_uuid", {}).get("uuid")
            links.append({
                "id": link_id or f"{src}=={dst}",
                "src": src,
                "dst": dst,
                "src_endpoint": src_ep,
                "dst_endpoint": dst_ep,
            })

        # Mark inter-domain links: any link between a router and another
        # router not connected directly to the same datacenter cluster is
        # treated as a candidate border link. As a safer default, we mark
        # router-to-router links as 'border' candidates.
        node_kind = {n["id"]: n.get("type") for n in nodes}
        for l in links:
            if (
                node_kind.get(l["src"]) == "router"
                and node_kind.get(l["dst"]) == "router"
            ):
                l["border"] = True

        return {"nodes": nodes, "links": links}

    @staticmethod
    def _normalize(body: Dict[str, Any]):
        """Coerce the various accepted shapes into (nodes, links) lists."""
        # TFS (TeraFlowSDN) descriptor format - pre-convert to flat form.
        tfs = TopologyStore._tfs_format(body)
        if tfs is not None:
            body = tfs

        nodes: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        seen_node_ids: set = set()

        # Explicit list-of-dicts form -----------------------------------
        for n in body.get("nodes", []) or []:
            if isinstance(n, str):
                nid = n
                node: Dict[str, Any] = {"id": nid}
            elif isinstance(n, dict):
                nid = n.get("id") or n.get("name")
                if not nid:
                    continue
                node = {**n, "id": nid}
            else:
                continue
            if nid not in seen_node_ids:
                seen_node_ids.add(nid)
                nodes.append(node)

        link_entries = body.get("links") or body.get("edges") or []
        for i, l in enumerate(link_entries):
            if not isinstance(l, dict):
                continue
            src = l.get("src") or l.get("source") or l.get("a")
            dst = l.get("dst") or l.get("target") or l.get("b")
            if not src or not dst:
                continue
            link = {**l, "src": src, "dst": dst}
            link.setdefault("id", f"L-{i}-{src}-{dst}")
            links.append(link)
            for end in (src, dst):
                if end not in seen_node_ids:
                    seen_node_ids.add(end)
                    nodes.append({"id": end})

        # Adjacency form ------------------------------------------------
        # Only consumed if the explicit form produced nothing.
        if not links:
            adjacency = body if not body.get("nodes") else body.get("topology", {})
            if isinstance(adjacency, dict):
                for src, neighbours in adjacency.items():
                    if not isinstance(neighbours, (list, tuple)):
                        continue
                    if src not in seen_node_ids:
                        seen_node_ids.add(src)
                        nodes.append({"id": src})
                    for dst in neighbours:
                        if not isinstance(dst, str):
                            continue
                        if dst not in seen_node_ids:
                            seen_node_ids.add(dst)
                            nodes.append({"id": dst})
                        links.append({
                            "id": f"L-{src}-{dst}",
                            "src": src,
                            "dst": dst,
                        })

        return nodes, links

    @staticmethod
    def _build_graph(nodes, links):
        g = nx.MultiGraph()
        for n in nodes:
            nid = n["id"]
            attrs = {k: v for k, v in n.items() if k != "id"}
            g.add_node(nid, **attrs)
        for l in links:
            attrs = {k: v for k, v in l.items() if k not in ("src", "dst")}
            g.add_edge(l["src"], l["dst"], key=l.get("id"), **attrs)
        return g

    # ------------------------------------------------------------------ #
    # Queries                                                            #
    # ------------------------------------------------------------------ #
    def sources(self) -> List[str]:
        with self._lock:
            return list(self._topologies.keys())

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            out = {}
            for src, entry in self._topologies.items():
                raw = entry["raw"]
                out[src] = {
                    "nodes": len(raw["nodes"]),
                    "links": len(raw["links"]),
                    "border_links": entry["border_links"],
                    "updated_at": entry["updated_at"],
                }
            return out

    def get(self, source: Optional[str] = None) -> Dict[str, Any]:
        """Return the raw nodes+links for a source (or the most recent one)."""
        with self._lock:
            if not self._topologies:
                return {}
            if source is None:
                source = max(
                    self._topologies, key=lambda s: self._topologies[s]["updated_at"]
                )
            entry = self._topologies.get(source)
            if not entry:
                return {}
            return {
                "source": source,
                "nodes": entry["raw"]["nodes"],
                "links": entry["raw"]["links"],
                "border_links": entry["border_links"],
            }

    def neighbors(self, node_id: str, source: Optional[str] = None) -> List[str]:
        graph = self._pick_graph(source)
        if graph is None or node_id not in graph:
            return []
        return sorted(set(graph.neighbors(node_id)))

    def shortest_path(
        self, src: str, dst: str, source: Optional[str] = None
    ) -> Optional[List[str]]:
        with timed_event(LOGGER, "topology_shortest_path", src=src, dst=dst):
            graph = self._pick_graph(source)
            if graph is None or src not in graph or dst not in graph:
                return None
            try:
                return nx.shortest_path(graph, src, dst)
            except Exception:
                return None

    def border_links(self, source: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            entry = self._pick_entry(source)
            if not entry:
                return []
            return [
                l for l in entry["raw"]["links"] if l.get("border")
            ]

    # ------------------------------------------------------------------ #
    def _pick_entry(self, source: Optional[str]) -> Optional[Dict[str, Any]]:
        if not self._topologies:
            return None
        if source is None:
            source = max(
                self._topologies, key=lambda s: self._topologies[s]["updated_at"]
            )
        return self._topologies.get(source)

    def _pick_graph(self, source: Optional[str]):
        if not NX_AVAILABLE:
            return None
        with self._lock:
            entry = self._pick_entry(source)
            return entry["graph"] if entry else None
