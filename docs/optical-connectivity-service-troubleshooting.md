# Optical Connectivity Service Troubleshooting Guide

## Problem

When creating an **OPTICAL_CONNECTIVITY** service between two devices connected to the TFS controller via the agentic module, the service is created but remains permanently at status `PLANNED` and never transitions to `ACTIVE`.

---

## Environment

| Component | Details |
|-----------|---------|
| TFS Controller | MicroK8s, namespace `tfs` |
| Agentic Module | MicroK8s, namespace `tfs-agentic` |
| Topology files | `docs/topology-optical-emulated.json`, `docs/topology-optical-emulated2.json` |
| Path computation | C-based SP (Shortest Path) engine, port 8081 |

---

## Investigation — Round 1

### Step 1 — Check the service log

```bash
cd tfs-ctrl
./scripts/show_logs_service.sh
```

**Finding:** The service log shows the task scheduler with no tasks queued:

```
INFO:service.service.task_scheduler.TaskScheduler:[execute_all] ordered_task_keys=[]
```

This means the service manager never received a computed path from the path computation engine, so it has nothing to activate. The service stays at `PLANNED`.

---

### Step 2 — Check the path computation logs

```bash
./scripts/show_logs_pathcomp_frontend.sh
./scripts/show_logs_pathcomp_backend.sh
```

**Finding from pathcomp frontend** — Unsupported optical constraints are silently ignored (not the root cause, but worth noting):

```
WARNING: Ignoring unsupported Constraint: bidirectionality = 1
WARNING: Ignoring unsupported Constraint: optical-band-width[GHz] = 200
WARNING: Ignoring unsupported Constraint: type = multi_granular
```

**Finding from pathcomp backend** — The SP algorithm ran and failed immediately:

```
pathComp_sp.c  destination: 5a279858 NOT reachable
pathComp_sp.c  NO PATH FOUND
    431a0876 [44305597] (optical-transponder / TP-DC2)
    --->
    5a279858 [a9d6a4ae] (optical-transponder / TP-DC3)
```

The Dijkstra algorithm explored the source transponder node and immediately found zero outgoing edges — the destination was unreachable. HTTP 200 was returned to the service manager but with a `NO PATH FOUND` payload, so no connection object was created and the service remained at `PLANNED`.

---

### Step 3 — Inspect the topology graph seen by path computation

The pathcomp backend log shows the graph it loaded from TFS (`build_contextSet_linklList`):

```
Link: DC3  (6dedc33c) ──→ TP-DC3 (5a279858)
Link: DC4  (87d0e7ac) ──→ TP-DC4 (63a06ec4)
Link: NET  (2ff85fb5) ──→ R5     (46189420)
Link: DC2  (800d5bd4) ──→ TP-DC2 (431a0876)
Link: DC1  (cda90d2f) ──→ TP-DC1 (59d2def4)
```

Only **5 links** were present. The complete optical topology should have 14 links. The 9 missing links are the entire optical core:

```
TP-DC1 ──→ R1
TP-DC2 ──→ R2
TP-DC3 ──→ R3
TP-DC4 ──→ R4
R1 ──→ R2
R2 ──→ R3
R3 ──→ R4
R4 ──→ R5
R5 ──→ R1
```

Without these links, all transponders are isolated stubs. Path computation cannot find any route between them.

---

### Root Cause 1 — `optical_links` key silently routes to a different database table

The topology descriptor file uses two separate JSON keys:

```json
{
  "links": [ ... ],          // 5 IP/Ethernet links
  "optical_links": [ ... ]   // 9 optical core links
}
```

TFS's `DescriptorLoader` (`src/common/tools/descriptor/Loader.py`) reads both keys, but routes them through **different gRPC calls** to **different database tables**:

| JSON key | gRPC call | Database table | Visible to pathcomp |
|----------|-----------|----------------|---------------------|
| `links` | `SetLink` | regular links table | **Yes** |
| `optical_links` | `SetOpticalLink` | optical links table | **No** |

The path computation engine queries only the regular links table via `GET /tfs-api/links`. `OpticalLink` objects stored via `SetOpticalLink` live in a separate table used exclusively by the optical controller.

The relevant code in `Loader.py`:

```python
# _load_normal_mode()
typed_links = split_links_by_type(self.__links)
typed_normal_links  = typed_links.get('normal',  list())   # → SetLink (pathcomp-visible)
typed_optical_links = typed_links.get('optical', list())   # → SetOpticalLink (invisible)
typed_optical_links.extend(self.__optical_links)           # optical_links key always → SetOpticalLink
```

Links in the `links` array with no `link_type` field default to `LINKTYPE_UNKNOWN`, which `split_links_by_type` routes to `SetLink` (regular table, pathcomp-visible). Links in `optical_links` always go to `SetOpticalLink` regardless of type.

### Fix 1 — Duplicate optical core links into the `links` array

Add all optical core links to the `links` array as well (with no `link_type` field so they default to `LINKTYPE_UNKNOWN` → `SetLink`). Keep `optical_links` intact for the optical controller.

```
Before fix 1:  links =  5  |  optical_links = 9   →  5 pathcomp-visible links
After fix 1:   links = 14  |  optical_links = 9   → 14 pathcomp-visible links
```

Both tables are now populated on every upload. Semantic distinction between link types is preserved via the `optical_links` key; pathcomp visibility is ensured via the `links` key.

---

## Investigation — Round 2

After applying Fix 1 and redeploying TFS with the updated topology, services still remained at `PLANNED`.

### Step 4 — Verify link count in TFS

```bash
curl -s http://127.0.0.1/tfs-api/links | python3 -c "
import json,sys
links = json.load(sys.stdin).get('links',[])
print(f'Total links: {len(links)}')
"
```

**Finding:** TFS now shows 14 links — confirming Fix 1 worked. The topology is fully loaded.

### Step 5 — Re-examine pathcomp backend logs

```bash
cd tfs-ctrl
./scripts/show_logs_pathcomp_backend.sh
```

**Finding:** Pathcomp loaded all 14 links but still returned NO PATH FOUND:

```
build_contextSet_linklList [0]  R4     (df5dbb93) ──→ R5     (46189420)
build_contextSet_linklList [1]  TP-DC3 (5a279858) ──→ R3     (0dff8c06)
build_contextSet_linklList [2]  DC3    (6dedc33c) ──→ TP-DC3 (5a279858)
build_contextSet_linklList [3]  DC4    (87d0e7ac) ──→ TP-DC4 (63a06ec4)
build_contextSet_linklList [4]  TP-DC1 (59d2def4) ──→ R1     (68741528)
build_contextSet_linklList [5]  R1     (68741528) ──→ R2     (c944aaeb)
build_contextSet_linklList [6]  R3     (0dff8c06) ──→ R4     (df5dbb93)
build_contextSet_linklList [7]  NET    (2ff85fb5) ──→ R5     (46189420)
build_contextSet_linklList [8]  R2     (c944aaeb) ──→ R3     (0dff8c06)
build_contextSet_linklList [9]  R5     (46189420) ──→ R1     (68741528)
build_contextSet_linklList [10] DC2    (800d5bd4) ──→ TP-DC2 (431a0876)
build_contextSet_linklList [11] DC1    (cda90d2f) ──→ TP-DC1 (59d2def4)
build_contextSet_linklList [12] TP-DC4 (63a06ec4) ──→ R4     (df5dbb93)
build_contextSet_linklList [13] TP-DC2 (431a0876) ──→ R2     (c944aaeb)

dijkstra Explored: TP-DC3 → check_link TP-DC3 ──→ R3   [RELAXED]
dijkstra Explored: R3     → check_link R3     ──→ R4   [RELAXED]
dijkstra Explored: R4     → check_link R4     ──→ R5   [RELAXED]   ← only one outgoing edge from R4
dijkstra Explored: R5     → check_link R5     ──→ R1   [RELAXED]
dijkstra Explored: R1     → check_link R1     ──→ R2   [RELAXED]
dijkstra Explored: R2     → check_link R2     ──→ R3   DISCARD (already visited)

destination: 63a06ec4 (TP-DC4) NOT reachable
NO PATH FOUND: TP-DC3[line] ──→ TP-DC4[line]
```

---

### Root Cause 2 — Pathcomp treats links as directed; transponders are unreachable from ROADMs

The pathcomp C backend builds a **directed graph** where each link produces exactly one directed edge: `endpoint[0] → endpoint[1]`. It does not traverse links in reverse.

Every transponder→ROADM link is defined with the transponder as `endpoint[0]`:

```
TP-DCx.line  →  Rx.adddrop1   (endpoint[0] → endpoint[1])
```

This means Dijkstra can travel **into** the ROADM ring from a source transponder, but can **never exit** to reach a destination transponder. The ROADM nodes have no outgoing edges to any transponder — only incoming ones.

The directed adjacency list with 14 links:

| Node | Outgoing edges |
|------|----------------|
| DC1 | → TP-DC1 |
| DC2 | → TP-DC2 |
| DC3 | → TP-DC3 |
| DC4 | → TP-DC4 |
| NET-EXT | → R5 |
| TP-DC1 | → R1 |
| TP-DC2 | → R2 |
| TP-DC3 | → R3 |
| TP-DC4 | → R4 |
| R1 | → R2 |
| R2 | → R3 |
| R3 | → R4 |
| R4 | → R5 |
| R5 | → R1 |

From TP-DC3, Dijkstra traverses: `TP-DC3 → R3 → R4 → R5 → R1 → R2 → (R3, discard)`. It visits all 5 ROADMs but can never exit to TP-DC4, because TP-DC4 only appears as a *source* node (TP-DC4→R4), never as a *destination*.

### Fix 2 — Add reverse ROADM→transponder links

For each transponder↔ROADM pair, add a reverse link so the pathcomp directed graph can exit the ring to reach the destination transponder's line port:

```json
{"link_id": {"link_uuid": {"uuid": "R1==TP-DC1"}}, "link_endpoint_ids": [
    {"device_id": {"device_uuid": {"uuid": "R1"}},    "endpoint_uuid": {"uuid": "adddrop1"}},
    {"device_id": {"device_uuid": {"uuid": "TP-DC1"}}, "endpoint_uuid": {"uuid": "line"}}
]},
{"link_id": {"link_uuid": {"uuid": "R2==TP-DC2"}}, "link_endpoint_ids": [
    {"device_id": {"device_uuid": {"uuid": "R2"}},    "endpoint_uuid": {"uuid": "adddrop1"}},
    {"device_id": {"device_uuid": {"uuid": "TP-DC2"}}, "endpoint_uuid": {"uuid": "line"}}
]},
{"link_id": {"link_uuid": {"uuid": "R3==TP-DC3"}}, "link_endpoint_ids": [
    {"device_id": {"device_uuid": {"uuid": "R3"}},    "endpoint_uuid": {"uuid": "adddrop1"}},
    {"device_id": {"device_uuid": {"uuid": "TP-DC3"}}, "endpoint_uuid": {"uuid": "line"}}
]},
{"link_id": {"link_uuid": {"uuid": "R4==TP-DC4"}}, "link_endpoint_ids": [
    {"device_id": {"device_uuid": {"uuid": "R4"}},    "endpoint_uuid": {"uuid": "adddrop1"}},
    {"device_id": {"device_uuid": {"uuid": "TP-DC4"}}, "endpoint_uuid": {"uuid": "line"}}
]}
```

```
After fix 2:   links = 18  |  optical_links = 9   → 18 pathcomp-visible links
```

With these links, the directed adjacency for ROADMs gains transponder exit edges:

| Node | Outgoing edges (after fix 2) |
|------|------------------------------|
| R1 | → R2, **→ TP-DC1** |
| R2 | → R3, **→ TP-DC2** |
| R3 | → R4, **→ TP-DC3** |
| R4 | → R5, **→ TP-DC4** |
| R5 | → R1 |

Example path TP-DC3 → TP-DC4 (now computable):
```
TP-DC3.line → R3.adddrop1 → [within R3] → R3.102 → R4.111 → [within R4] → R4.adddrop1 → TP-DC4.line
```

---

## Fixes Applied

### Permanent fix — topology descriptor files updated

Both topology files were updated to include the correct links in the `links` array:

**`docs/topology-optical-emulated.json`**

| Fix | Before | After |
|-----|--------|-------|
| Fix 1: optical core links added to `links` | links = 5 | links = 14 |
| Fix 2: reverse ROADM→transponder links added | links = 14 | links = 18 |

New entries added in Fix 2:
- `R1==TP-DC1`: R1.adddrop1 → TP-DC1.line
- `R2==TP-DC2`: R2.adddrop1 → TP-DC2.line
- `R3==TP-DC3`: R3.adddrop1 → TP-DC3.line
- `R4==TP-DC4`: R4.adddrop1 → TP-DC4.line

**`docs/topology-optical-emulated2.json`**

| Fix | Before | After |
|-----|--------|-------|
| Fix 1: optical core links added to `links` | links = 3 | links = 8 |
| Fix 2: reverse ROADM→transponder links added | links = 8 | links = 10 |

New entries added in Fix 2:
- `ROADM-A==TP-DC-A`: ROADM-A.adddrop1 → TP-DC-A.line
- `ROADM-B==TP-DC-B`: ROADM-B.adddrop1 → TP-DC-B.line

The `optical_links` array in both files is **unchanged** — it is kept intact for the optical controller.

---

### Live fix — links added directly to running TFS (round 2)

After Fix 2 was identified, the 4 reverse links were pushed directly to the running TFS instance via the NBI REST API:

```bash
curl -s -X POST http://127.0.0.1/tfs-api/links \
  -H "Content-Type: application/json" \
  -d '{
    "links": [
      {"link_id": {"link_uuid": {"uuid": "R1==TP-DC1"}}, "link_endpoint_ids": [
        {"device_id": {"device_uuid": {"uuid": "68741528-2e94-5274-ab3c-fddcd8dc05ef"}}, "endpoint_uuid": {"uuid": "cbdc5f0a-72b6-5432-8792-c0c6504ded5a"}},
        {"device_id": {"device_uuid": {"uuid": "59d2def4-0614-5a51-be7b-ff21f688d831"}}, "endpoint_uuid": {"uuid": "f1111dac-97d3-5cca-9196-0cacb46b0558"}}
      ]},
      {"link_id": {"link_uuid": {"uuid": "R2==TP-DC2"}}, "link_endpoint_ids": [
        {"device_id": {"device_uuid": {"uuid": "c944aaeb-bbdf-5f2d-b31c-8cc8903045b6"}}, "endpoint_uuid": {"uuid": "a32337dd-6d05-5551-8a14-4d3b51fdd76c"}},
        {"device_id": {"device_uuid": {"uuid": "431a0876-67fa-5486-aaab-8298fb3a1484"}}, "endpoint_uuid": {"uuid": "44305597-ad1c-5d43-8e4a-0394fa1fd888"}}
      ]},
      {"link_id": {"link_uuid": {"uuid": "R3==TP-DC3"}}, "link_endpoint_ids": [
        {"device_id": {"device_uuid": {"uuid": "0dff8c06-873b-5799-ac54-c0452252bae1"}}, "endpoint_uuid": {"uuid": "8320c62d-51df-53db-8dba-8fba6783dd87"}},
        {"device_id": {"device_uuid": {"uuid": "5a279858-a6fe-59c8-84f8-518874cc21c0"}}, "endpoint_uuid": {"uuid": "a9d6a4ae-5f98-57ac-ae96-48c9365bf1af"}}
      ]},
      {"link_id": {"link_uuid": {"uuid": "R4==TP-DC4"}}, "link_endpoint_ids": [
        {"device_id": {"device_uuid": {"uuid": "df5dbb93-a154-5587-8195-f74f12bbcdf9"}}, "endpoint_uuid": {"uuid": "c16edd43-c964-582f-9160-2f643629b72d"}},
        {"device_id": {"device_uuid": {"uuid": "63a06ec4-9d06-5c96-945b-e5b9de9dc65c"}}, "endpoint_uuid": {"uuid": "4044098e-e0c6-5cc9-8129-ba8bbe2eae6c"}}
      ]}
    ]
  }'
```

TFS now has **18 links** in the regular links table.

---

## UUID Reference Table (topology-optical-emulated.json)

| Logical name | TFS device UUID | Endpoint | Endpoint UUID |
|-------------|-----------------|----------|---------------|
| TP-DC1 | `59d2def4-0614-5a51-be7b-ff21f688d831` | client | *(not used for optical path)* |
| TP-DC1 | `59d2def4-0614-5a51-be7b-ff21f688d831` | line | `f1111dac-97d3-5cca-9196-0cacb46b0558` |
| TP-DC2 | `431a0876-67fa-5486-aaab-8298fb3a1484` | line | `44305597-ad1c-5d43-8e4a-0394fa1fd888` |
| TP-DC3 | `5a279858-a6fe-59c8-84f8-518874cc21c0` | line | `a9d6a4ae-5f98-57ac-ae96-48c9365bf1af` |
| TP-DC4 | `63a06ec4-9d06-5c96-945b-e5b9de9dc65c` | line | `4044098e-e0c6-5cc9-8129-ba8bbe2eae6c` |
| R1 | `68741528-2e94-5274-ab3c-fddcd8dc05ef` | adddrop1 | `cbdc5f0a-72b6-5432-8792-c0c6504ded5a` |
| R1 | `68741528-2e94-5274-ab3c-fddcd8dc05ef` | 101 | `811b34f9-112c-5cc5-a4e0-6e5f5e0bdada` |
| R1 | `68741528-2e94-5274-ab3c-fddcd8dc05ef` | 111 | `4255273d-5038-512d-9838-d5dd921cb858` |
| R2 | `c944aaeb-bbdf-5f2d-b31c-8cc8903045b6` | adddrop1 | `a32337dd-6d05-5551-8a14-4d3b51fdd76c` |
| R2 | `c944aaeb-bbdf-5f2d-b31c-8cc8903045b6` | 111 | `d777e553-af96-5ee7-a957-ce70b49e36ac` |
| R2 | `c944aaeb-bbdf-5f2d-b31c-8cc8903045b6` | 102 | `1a257f6c-5f82-519a-8919-1c43576c2184` |
| R3 | `0dff8c06-873b-5799-ac54-c0452252bae1` | adddrop1 | `8320c62d-51df-53db-8dba-8fba6783dd87` |
| R3 | `0dff8c06-873b-5799-ac54-c0452252bae1` | 111 | `8daacff1-ce2e-5b2c-a9df-58e2eb174f68` |
| R3 | `0dff8c06-873b-5799-ac54-c0452252bae1` | 102 | `4097c02b-dca3-5f42-a2d0-5e75ba2b8516` |
| R4 | `df5dbb93-a154-5587-8195-f74f12bbcdf9` | adddrop1 | `c16edd43-c964-582f-9160-2f643629b72d` |
| R4 | `df5dbb93-a154-5587-8195-f74f12bbcdf9` | 111 | `3b6eecd6-f39b-522f-b8e1-e20f381bc6b2` |
| R4 | `df5dbb93-a154-5587-8195-f74f12bbcdf9` | 102 | `8435136e-2274-54bf-ae5c-2c244255e3fc` |
| R5 | `46189420-5879-563c-b5a1-2fc5ff6f8915` | 111 | `3b7af1bb-9aae-5236-929c-31ea6b31242a` |
| R5 | `46189420-5879-563c-b5a1-2fc5ff6f8915` | 102 | `3c57eb43-abdc-5fa0-9c1a-f34ae08a5911` |

---

## Secondary Findings

### Unsupported optical constraints

The path computation frontend logs warnings for optical-specific constraints that the SP algorithm does not understand. These are silently dropped and do not affect path computation correctness, but they indicate the constraints are not being enforced:

```
Ignoring unsupported Constraint: bidirectionality = 1
Ignoring unsupported Constraint: optical-band-width[GHz] = 200
Ignoring unsupported Constraint: type = multi_granular
```

These constraints are passed by the agentic module when creating optical connectivity services. The pathcomp frontend (`TopologyTools`) does not have handlers for them. No action is required for basic connectivity, but bandwidth and bidirectionality constraints are not enforced at the path computation level.

### Optical controller not deployed

```bash
./scripts/show_logs_opticalcontroller.sh
# Error: deployments.apps "opticalcontrollerservice" not found
```

The optical controller is not running in this deployment. Optical connectivity services are handled directly by the path computation engine and service manager without optical controller involvement.

---

## Diagnostic Commands Reference

```bash
# TFS controller logs (run from tfs-ctrl/)
./scripts/show_logs_service.sh            # Service manager — task scheduler state
./scripts/show_logs_pathcomp_frontend.sh  # Constraint parsing, algorithm selection
./scripts/show_logs_pathcomp_backend.sh   # Dijkstra execution, graph loaded, path found/not found
./scripts/show_logs_device.sh             # Device manager — connection activation
./scripts/show_logs_nbi.sh               # NBI — incoming REST requests

# Verify links registered in TFS (expect 18 after both fixes)
curl -s http://127.0.0.1/tfs-api/links | python3 -c "
import json,sys
links = json.load(sys.stdin).get('links',[])
print(f'Total links: {len(links)}')
for l in links: print(f'  {l.get(\"name\",\"?\")}')
"

# Check Kubernetes pod status
kubectl get pods -n tfs
kubectl get pods -n tfs-agentic
```

**Interpreting pathcomp backend log for this topology:**
- Look for `build_contextSet_linklList` lines to see how many links were loaded and their direction
- Look for `dijkstra Explored` + `check_link` lines to trace the Dijkstra traversal
- If a ROADM node is explored but only one outgoing edge is checked (to the next ring node), reverse links are missing
- `destination: <uuid> NOT reachable` followed by `NO PATH FOUND` → path computation failed; service stays at PLANNED

---

## Files Changed

| File | Change |
|------|--------|
| `docs/topology-optical-emulated.json` | Fix 1: Added 9 optical core links to `links` array; Fix 2: Added 4 reverse ROADM→transponder links (`links` = 18, `optical_links` = 9) |
| `docs/topology-optical-emulated2.json` | Fix 1: Added 5 optical core links to `links` array; Fix 2: Added 2 reverse ROADM→transponder links (`links` = 10, `optical_links` = 5) |

No changes were made to `tfs-ctrl` source code or the agentic module Python code.
