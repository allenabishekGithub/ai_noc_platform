# TFS MCP Server - Prerequisites

This document captures the analysis of the TeraFlowSDN (TFS) controller source tree under `/home/allen/tfs-ctrl`, focused on locating the API endpoints exposed by the controller. This information is intended as a reference / prerequisite for building an MCP server that interacts with the TFS controller.

---

## Where the TFS controller's API endpoints live

The TFS SDN controller exposes its external / northbound API through the **NBI (NorthBound Interface) component**, located at:

`tfs-ctrl/src/nbi/`

### Key files

**1. Application bootstrap** - registers all API "modules" onto the Flask app:

- `tfs-ctrl/src/nbi/service/app.py` - main entry point. It instantiates `NbiApplication` and calls every `register_*` function (one per API family).
- `tfs-ctrl/src/nbi/service/NbiApplication.py` - the Flask + Flask-RESTful + SocketIO wrapper used to register REST resources and websocket namespaces.

**2. Per-API endpoint definitions** - each subfolder under `tfs-ctrl/src/nbi/service/` defines a group of endpoints. The `__init__.py` of each folder declares URL patterns and binds them to resource classes (defined in `Resources.py`):

| Folder | API exposed |
|---|---|
| `tfs_api/` | **Native TFS REST API** (contexts, topologies, services, slices, devices, links, connections, policyrules) - see `tfs_api/__init__.py` |
| `etsi_bwm/` | ETSI Bandwidth Management |
| `ietf_acl/`, `ietf_l2vpn/`, `ietf_l3vpn/`, `ietf_network/`, `ietf_network_slice/`, `ietf_hardware/` | IETF RESTCONF / YANG-based APIs |
| `camara_qod/` | CAMARA Quality-on-Demand |
| `osm_nbi/` | OSM-compatible NBI |
| `optical_slice/`, `media_channel/`, `dscm_oc/` | Optical APIs |
| `qkd_app/` | QKD application API |
| `restconf_root/`, `well_known_meta/` | RESTCONF root + `/.well-known/` discovery |
| `sse_telemetry/`, `topology_updates/` | Server-Sent Events / WebSocket streams |
| `health_probes/` | Liveness / readiness probes |
| `vntm_recommend/` | VNT Manager recommendations |

### The "main" TFS API

The most relevant file for the controller's **native API** is:

`tfs-ctrl/src/nbi/service/tfs_api/__init__.py`

It maps URLs (prefixed with `/tfs-api`) to handler classes - for example:

- `GET / POST /tfs-api/contexts`
- `GET /tfs-api/context/<context_uuid>/topology_details/<topology_uuid>`
- `GET / POST /tfs-api/context/<context_uuid>/services`
- `GET / POST /tfs-api/devices`
- `GET / POST /tfs-api/links`

Full URL table from `tfs_api/__init__.py`:

| Endpoint name | URL (prefix `/tfs-api`) |
|---|---|
| `api.context_ids` | `/context_ids` |
| `api.contexts` | `/contexts` |
| `api.dummy_contexts` | `/dummy_contexts` |
| `api.context` | `/context/<context_uuid>` |
| `api.topology_ids` | `/context/<context_uuid>/topology_ids` |
| `api.topologies` | `/context/<context_uuid>/topologies` |
| `api.topology` | `/context/<context_uuid>/topology/<topology_uuid>` |
| `api.topology_details` | `/context/<context_uuid>/topology_details/<topology_uuid>` |
| `api.service_ids` | `/context/<context_uuid>/service_ids` |
| `api.services` | `/context/<context_uuid>/services` |
| `api.service` | `/context/<context_uuid>/service/<service_uuid>` |
| `api.slice_ids` | `/context/<context_uuid>/slice_ids` |
| `api.slices` | `/context/<context_uuid>/slices` |
| `api.slice` | `/context/<context_uuid>/slice/<slice_uuid>` |
| `api.device_ids` | `/device_ids` |
| `api.devices` | `/devices` |
| `api.device` | `/device/<device_uuid>` |
| `api.link_ids` | `/link_ids` |
| `api.links` | `/links` |
| `api.link` | `/link/<link_uuid>` |
| `api.connection_ids` | `/context/<context_uuid>/service/<service_uuid>/connection_ids` |
| `api.connections` | `/context/<context_uuid>/service/<service_uuid>/connections` |
| `api.connection` | `/connection/<connection_uuid>` |
| `api.policyrule_ids` | `/policyrule_ids` |
| `api.policyrules` | `/policyrules` |
| `api.policyrule` | `/policyrule/<policyrule_uuid>` |

The actual handler logic lives in `tfs-ctrl/src/nbi/service/tfs_api/Resources.py`.

### Internal gRPC APIs

Each microservice (context, device, service, slice, etc.) under `tfs-ctrl/src/` also exposes an **internal gRPC API**, defined by the `.proto` files in `tfs-ctrl/proto/`. The NBI is the component that translates external HTTP / REST / RESTCONF calls into those internal gRPC calls.

---

## TL;DR

- Start at `tfs-ctrl/src/nbi/service/app.py`.
- For the native TFS REST endpoints, see `tfs-ctrl/src/nbi/service/tfs_api/__init__.py` and `tfs-ctrl/src/nbi/service/tfs_api/Resources.py`.
- All other northbound API flavors (IETF, ETSI, CAMARA, OSM, optical, QKD, etc.) follow the same pattern: one folder per API under `tfs-ctrl/src/nbi/service/`, registered from `app.py`.
