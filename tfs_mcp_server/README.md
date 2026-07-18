# TFS MCP Server

A Model Context Protocol (MCP) server exposing the **TeraFlowSDN (TFS)** controller's
Northbound REST API (`/tfs-api`) as MCP tools, so the TFS NAF agent (or any MCP-aware
LLM client) can invoke TFS operations through a uniform tool interface.

Each MCP tool is a thin wrapper around one HTTP endpoint defined in
`tfs-ctrl/src/nbi/service/tfs_api/__init__.py` and implemented in
`tfs-ctrl/src/nbi/service/tfs_api/Resources.py`.

NOTE: the tfs-mcp server is an independent entity, run it separately. It is not part of naf-agent.

## Layout

```
tfs_mcp_server/
+-- main.py                  <- Entry point, matching the NAF agent style
+-- tfs_mcp_server.py        <- Compatibility shim for existing imports/scripts
+-- tfs_mcp/
  +-- __init__.py
  +-- app.py               <- CLI argument parsing and process startup
  +-- dependencies.py      <- Logging, dotenv loading, optional dependency setup
  +-- server.py            <- TFSMCPServer transport + HTTP/tool dispatch
  +-- tool_definitions.py  <- MCP tool catalogue and JSON schemas
```

## Tool catalogue

| Category | Tools |
|---|---|
| Contexts | `tfs_list_context_ids`, `tfs_list_contexts`, `tfs_set_contexts`, `tfs_get_dummy_contexts`, `tfs_get_context`, `tfs_set_context`, `tfs_delete_context` |
| Topologies | `tfs_list_topology_ids`, `tfs_list_topologies`, `tfs_set_topologies`, `tfs_get_topology`, `tfs_set_topology`, `tfs_delete_topology`, `tfs_get_topology_details` |
| Services | `tfs_list_service_ids`, `tfs_list_services`, `tfs_create_services`, `tfs_get_service`, `tfs_update_service`, `tfs_delete_service` |
| Slices | `tfs_list_slice_ids`, `tfs_list_slices`, `tfs_create_slices`, `tfs_get_slice`, `tfs_update_slice`, `tfs_delete_slice` |
| Devices | `tfs_list_device_ids`, `tfs_list_devices`, `tfs_add_devices`, `tfs_get_device`, `tfs_configure_device`, `tfs_delete_device` |
| Links | `tfs_list_link_ids`, `tfs_list_links`, `tfs_set_links`, `tfs_get_link`, `tfs_set_link`, `tfs_delete_link` |
| Connections | `tfs_list_connection_ids`, `tfs_list_connections`, `tfs_get_connection` |
| Policy Rules | `tfs_list_policyrule_ids`, `tfs_list_policyrules`, `tfs_get_policyrule` |
| **Optical Spectrum** | `tfs_optical_slice_create`, `tfs_optical_slice_delete`, `tfs_media_channel_allocate`, `tfs_media_channel_release`, `tfs_dscm_oc_get`, `tfs_dscm_oc_post`, `tfs_dscm_oc_delete` |
| Health | `tfs_health_check` |

## Configuration

| Flag / Env var | Default | Purpose |
|---|---|---|
| `--api-url` / `TFS_NBI_URL` | `http://127.0.0.1:80` | TFS NBI base URL |
| `--api-prefix` / `TFS_NBI_PREFIX` | `/tfs-api` | URL prefix used by `tfs_api/__init__.py` |
| `--username` / `TFS_NBI_USER` | _(none)_ | Optional HTTP basic auth user |
| `--password` / `TFS_NBI_PASS` | _(none)_ | Optional HTTP basic auth password |
| `--port` / `TFS_MCP_PORT` | `3002` | SSE listen port |
| `--transport` | `sse` | `sse` or `stdio` |
| `--insecure` | off | Skip TLS verification |

> The TFS NBI is typically reachable inside the K8s cluster at the
> `nbiservice` Service. From outside the cluster, port-forward or expose
> the ingress and point `--api-url` at it (e.g. `http://127.0.0.1:9999`).

## Run

This component is fully standalone (its own venv + `requirements.txt`).
The easiest way to start it is via the bundled script:

```bash
cd /home/allen/tfs-agentic-module/tfs_mcp_server
./start.sh                              # SSE on :3002 -> http://localhost:3002/sse
TFS_MCP_PORT=4000 ./start.sh            # custom port
TFS_NBI_URL=http://127.0.0.1:9999 ./start.sh   # custom TFS NBI URL
TFS_MCP_TRANSPORT=stdio ./start.sh      # stdio mode
```

Or launch the modular entry point directly:

```bash
cd /home/allen/tfs-agentic-module/tfs_mcp_server
source .venv/bin/activate
python main.py --transport sse --port 3002 --api-url http://127.0.0.1:80
```

The legacy `python tfs_mcp_server.py ...` command still works and forwards into the same package.

Then connect any MCP client to `http://localhost:3002/sse`.

## Embed inside the NAF agent

```python
from tfs_mcp_server import TFSMCPServer

srv = TFSMCPServer(
    transport="sse",
    port=3002,
    api_url="http://127.0.0.1:80",
)
srv.start()
# ... NAF agent uses an MCP client to connect to http://localhost:3002/sse ...
srv.stop()
```

---

## How the TFS NBI is organized (and where the optical APIs live)

`/tfs-api` is **only one of many** REST APIs served by the TFS NBI component.
All of them are sibling **Flask blueprints / Flask-RESTful resources** registered
onto the **same Flask app** (`nbiservice` pod) by
`tfs-ctrl/src/nbi/service/app.py`. They differ only in URL prefix.

### What the optical endpoints actually are

They are **RESTCONF-style HTTP APIs** (loosely IETF RFC 8040 shaped) that
translate REST calls into internal **gRPC calls** to specialized TFS
microservices (the optical controller, the pluggables service, the slice
service, etc.). Each family lives in its own subfolder under
`tfs-ctrl/src/nbi/service/` and follows the same pattern as `tfs_api`:

- `__init__.py` - defines the URL prefix and a `register_*(nbi_app)` function.
- `Resources.py` (or `routes.py`) - Flask-RESTful `Resource` classes / Flask
  Blueprint routes that call internal gRPC clients.

### Where they live

| Family | Folder | URL prefix |
|---|---|---|
| Optical Slice | `tfs-ctrl/src/nbi/service/optical_slice/` | `/restconf/optical-slice/v1` |
| Media Channel (spectrum slot) | `tfs-ctrl/src/nbi/service/media_channel/` | `/restconf/media-channel/v1` |
| DSCM-OC (per-device OpenConfig) | `tfs-ctrl/src/nbi/service/dscm_oc/` | `/restconf/data` |

### How they relate to `tfs_api`

`tfs_api` is just one of many sibling APIs registered on the same Flask app.
Looking at `tfs-ctrl/src/nbi/service/app.py`:

```python
nbi_app = NbiApplication(base_url=BASE_URL)
register_camara_qod      (nbi_app)   # CAMARA QoD
register_dscm_oc         (nbi_app)   # <-- DSCM OpenConfig (optical)
register_etsi_api        (nbi_app)   # ETSI E2E services
register_etsi_bwm_api    (nbi_app)
register_health_probes   (nbi_app)
register_ietf_acl        (nbi_app)
register_ietf_hardware   (nbi_app)
register_ietf_l2vpn      (nbi_app)
register_ietf_l3vpn      (nbi_app)
register_ietf_network    (nbi_app)
register_ietf_nss        (nbi_app)
register_optical_slice   (nbi_app)   # <-- Optical Slice (optical)
register_osm_api         (nbi_app)
register_qkd_app         (nbi_app)
register_restconf_root   (nbi_app)
register_telemetry_subscription(nbi_app)
register_tfs_api         (nbi_app)   # <-- the /tfs-api family
register_vntm_recommend  (nbi_app)
register_media_channel   (nbi_app)   # <-- Media Channel (optical)
register_well_known      (nbi_app)
```

Each `register_*` adds either Flask-RESTful resources
(`nbi_app.add_rest_api_resource(...)`) or a Flask Blueprint
(`nbi_flask_app.register_blueprint(blueprint, url_prefix=...)`). So the **same
`nbiservice` pod** serves all of them - only the URL prefix differs:

- `/tfs-api/...` -> native TFS objects (contexts, services, devices...)
- `/restconf/optical-slice/v1/...` -> optical slice requests
- `/restconf/media-channel/v1/...` -> optical spectrum allocations
- `/restconf/data/device=<uuid>/...` -> per-device OpenConfig (DSCM)
- `/restconf/...`, `/.well-known/...`, `/osm/...`, `/etsi/...`, `/ietf/...`,
  `/camara/...`, `/qkd/...` -> other northbound API flavors

### Why they are split out

- `/tfs-api` is the **TFS-native** API - it speaks TFS's own JSON shape and maps
  directly onto the `context_pb2` / `service_pb2` gRPC definitions.
- The `/restconf/...` ones are **standards-aligned** northbound APIs
  (RESTCONF + OpenConfig YANG, CAMARA, IETF, ETSI, OSM, ...). They exist so
  external systems / OSS / BSS can talk to TFS using their *own* schemas
  without learning TFS-internal types. The optical ones are domain-specific --
  the optical controller and pluggables service have their own gRPC interfaces
  that wouldn't fit cleanly into `/tfs-api`.

### Reaching them

All of them live behind the same `nbiservice` Kubernetes Service. From inside
the cluster:

```
http://nbiservice.tfs.svc.cluster.local:8080/restconf/media-channel/v1/service/<allocation_id>
```

From outside the cluster (port-forward / ingress), they share whatever
host:port you point the MCP server's `--api-url` (or `TFS_NBI_URL`) at. The
MCP tools below (`tfs_optical_slice_*`, `tfs_media_channel_*`,
`tfs_dscm_oc_*`) hit them directly with `use_prefix=False` so the `/tfs-api`
prefix is bypassed.

---

## Optical Spectrum tools (the important ones)

The TFS controller exposes three families of optical / spectrum endpoints in the
NBI. They live **outside** the `/tfs-api` prefix, so the MCP handlers wrap them
with `use_prefix=False`:

| Component | Source | Mounted at |
|---|---|---|
| Optical Slice | `tfs-ctrl/src/nbi/service/optical_slice/` | `/restconf/optical-slice/v1/...` |
| Media Channel | `tfs-ctrl/src/nbi/service/media_channel/` | `/restconf/media-channel/v1/...` |
| DSCM-OC (Dynamic Spectrum & Capacity Mgmt - OpenConfig) | `tfs-ctrl/src/nbi/service/dscm_oc/` | `/restconf/data/device=<uuid>/...` |

### MCP tool <-> HTTP endpoint mapping

| MCP tool | HTTP call | Purpose |
|---|---|---|
| `tfs_optical_slice_create` | `POST /restconf/optical-slice/v1/service/<slice_id>` | Request an end-to-end optical slice. The controller resolves it into one or more spectrum allocations (media channels). |
| `tfs_optical_slice_delete` | `DELETE /restconf/optical-slice/v1/service/<slice_id>` | Release the optical slice and its spectrum allocations. |
| **`tfs_media_channel_allocate`** | `POST /restconf/media-channel/v1/service/<allocation_id>` | **Primary spectrum-allocation endpoint.** Allocates an optical spectrum slot (media channel) defined by center frequency + slot width on a fiber/ROADM path. Used to commit a spectrum-negotiation result. |
| `tfs_media_channel_release` | `DELETE /restconf/media-channel/v1/service/<allocation_id>` | Tear down an optical spectrum allocation. |
| `tfs_dscm_oc_get` | `GET /restconf/data/device=<uuid>/<rc_path>` | Read OpenConfig optical-channel state on a transponder/ROADM (frequency, operational-mode, target-output-power, ...). |
| `tfs_dscm_oc_post` | `POST /restconf/data/device=<uuid>/<rc_path>` | Push an OpenConfig optical-channel / spectrum config to a device. |
| `tfs_dscm_oc_delete` | `DELETE /restconf/data/device=<uuid>/<rc_path>` | Remove an optical-channel configuration from a device. |

### Tool inputs (JSON Schema)

```jsonc
// tfs_optical_slice_create
{
  "slice_id": "slice-abc-123",
  "slice": { /* TFS optical-slice schema: endpoints, bandwidth, constraints, ... */ }
}

// tfs_optical_slice_delete
{ "slice_id": "slice-abc-123" }

// tfs_media_channel_allocate
{
  "allocation_id": "mc-001",
  "media_channel": {
    /* Typical fields: source/destination endpoints, center frequency
       (THz, in 0.0125 THz units for flexgrid n), slot width (m),
       optical-channel name, target-output-power, ... */
  }
}

// tfs_media_channel_release
{ "allocation_id": "mc-001" }

// tfs_dscm_oc_get
{
  "device_uuid": "<device-uuid>",
  "rc_path": "openconfig-platform:components/component=och-1/optical-channel/config"
}

// tfs_dscm_oc_post
{
  "device_uuid": "<device-uuid>",
  "rc_path": "openconfig-platform:components/component=och-1/optical-channel/config",
  "config": {
    "openconfig-terminal-device:optical-channel": {
      "config": {
        "frequency": 193100000,        // MHz
        "target-output-power": -1.0,   // dBm
        "operational-mode": 1
      }
    }
  }
}

// tfs_dscm_oc_delete
{
  "device_uuid": "<device-uuid>",
  "rc_path": "openconfig-platform:components/component=och-1/optical-channel/config"
}
```

### Typical spectrum-negotiation flow (NAF agent -> MCP -> TFS)

1. **Inspect topology** - `tfs_list_devices`, `tfs_list_links`, `tfs_get_topology_details`
   to find candidate optical paths (ROADMs, transponders, fibers).
2. **Request an optical slice** - `tfs_optical_slice_create` with the desired
   endpoints and bandwidth. The TFS optical controller computes the path and
   the candidate spectrum slot(s).
3. **Allocate the spectrum slot** - `tfs_media_channel_allocate` to commit a
   specific center frequency + slot width along the resolved path.
   This is what "spectrum negotiation" ultimately produces.
4. **Push device config (optional / advanced)** - `tfs_dscm_oc_post` to
   program the optical-channel parameters directly on a transponder/ROADM via
   OpenConfig (frequency, operational-mode, target-output-power).
5. **Verify** - `tfs_dscm_oc_get` to read back the optical-channel state.
6. **Release** when done - `tfs_media_channel_release` and/or
   `tfs_optical_slice_delete`.

> Content-type note: `dscm_oc` enforces RESTCONF media types
> (`application/yang-data+json`). The MCP server currently sends standard
> `application/json`. If the TFS NBI rejects the request, set the appropriate
> `Accept` / `Content-Type` headers on the device side or extend
> `api_request()` to include them for these tools.
## to see logs of webui server of tfs
```bash
kubectl logs -n tfs -f webuiservice-7bcd755b59-jz664 -c server
```
