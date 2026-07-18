# A2A Packet Service Scenarios

These examples teach the agent how to handle packet Ethernet/IP service requests
that span two TFS controller domains. They are intended for RAG retrieval during
WebUI conversations.

## Network Model

Two TFS agentic-module instances can each manage a separate TFS controller:

- Domain A: local controller with `topology-emulated-net-ext.json`.
- Domain B: peer controller with `topology-emulated-net-ext2.json`.

The shared external/inter-domain attachment point is represented by device
`NET-EXT`. In packet service workflows, treat `NET-EXT` as the handoff device
between domains. Each domain provisions only the service segment that is visible
in its own controller.

For a request such as:

```text
create an l2vpn service between dc1 and dc6
create an l3vpn service between dc1 and dc7
create an l3vpn service between dc2 and dc5
```

The agent must not pretend a single local controller can create an end-to-end
service if one endpoint is remote. It should negotiate with the peer agent via
A2A and create local/remote service segments. If either side cannot resolve or
provision its segment, report that the requested end-to-end service is not
possible.

Important privacy and discovery rule: one agent must not depend on knowing the
other controller's internal topology. If a requested node such as `DC5`, `DC6`,
`DC7`, or any other endpoint is not present in the local inventory, that does
not prove the node is invalid. It may belong to another SDN controller domain.
The local agent should contact the peer agent through A2A and let the peer
decide from its own local inventory whether it owns or can provision toward that
node. For L2VPN/L3VPN, send a structured A2A task such as
`NET-EXT/access0` to `<remote-dc>/eth0`. For other service types, send a generic
A2A message describing the requested service and remote node. Only report the
node as unavailable after the peer rejects or fails the A2A request.

## Required Tool Pattern

Use this general sequence for cross-domain packet services:

1. Call `tfs_inventory()` locally.
2. Determine whether both endpoint devices are local.
3. If both endpoints are local, use `tfs_provision_service()` directly.
4. If one endpoint/node is not local and `NET-EXT` exists locally, treat the
   missing endpoint/node as remote. Do not ask the user to rename/add it yet.
5. For L2VPN/L3VPN, prefer `a2a_provision_cross_domain_packet_service()`.
   This one tool provisions the peer segment first and creates the local
   `NET-EXT` segment only after the peer reports success.
6. If using individual tools instead of the one-shot tool, call
   `a2a_list_peers()`, `a2a_discover_peer()`, then
   `a2a_provision_peer_packet_service()` before `tfs_provision_service()`.
7. For other service types, use `a2a_send_peer_message()` to ask the peer
   agent whether it can handle the remote node/segment through its controller.
8. Report success only if both local and peer results indicate success/ACTIVE.
9. If local or peer provisioning fails, report that the end-to-end service is
   not possible and identify which side failed.

Never create the local `DCx/eth0 -> NET-EXT/access0` segment before the peer
`NET-EXT/access0 -> remote-DC/eth0` segment has succeeded.

Keep the service type consistent across both segments. If the user requests
L2VPN, the peer segment and local segment must both use `service_type="l2vpn"`.
If the user requests L3VPN, both segments must use `service_type="l3vpn"`. Do
not create an L3 local handoff after an L2 peer segment, and do not claim that
an L2 cross-domain service is established when the local segment is L3.

Do not ask for confirmation before provisioning. Service creation is additive,
and the user request is the intent.

When following these examples, execute the named tools through the agent tool
interface. Do not write JSON-looking tool-call text in the final answer and stop;
that does not contact the peer agent or controller.

## Endpoint Selection Rules

For packet topologies:

- Datacenter devices such as `DC1`, `DC5`, `DC6`, `DC7` usually use endpoint
  `eth0` for access links.
- `NET-EXT` uses endpoint `access0`.
- Packet routers use `eth*` endpoints. Use the exact endpoint names returned by
  `tfs_inventory()`.

When creating a cross-domain service, use `NET-EXT` as the boundary endpoint.
Typical segmentation:

- Local segment: local endpoint to local `NET-EXT` endpoint.
- Peer segment: peer `NET-EXT` endpoint to remote endpoint.

For packet service requests, never ask the peer to create the full end-to-end
path. The peer should only receive the segment that exists inside its own
controller domain. For example, for a request received in Domain A to create
L3VPN from `DC1` to a remote datacenter, the peer A2A task is
`NET-EXT/access0` to `<remote-dc>/eth0`; it is not `DC1/eth0` to
`<remote-dc>/eth0`. Apply this pattern to `DC5`, `DC6`, `DC7`, or any other
remote datacenter returned by the peer domain.

If `NET-EXT` is not present in local inventory, the local domain has no known
inter-domain handoff. Report that the service is not possible until a handoff is
configured.

Do not inspect or assume the peer domain's internal router/path structure from
the local agent. The local agent only sends the requested remote endpoint and
peer handoff in the A2A payload. If the peer does not expose `NET-EXT` or the
remote endpoint to its own provisioning workflow, the peer should reject the
task; then report that the peer domain cannot support the requested service.

## Scenario: Create L3VPN from DC1 to DC6

**Trigger phrases:** "create l3vpn between dc1 and dc6", "connect dc1 to dc6
with l3vpn", "create layer 3 vpn from dc1 to dc6"

Assume:

- Local domain contains `DC1` and `NET-EXT`.
- Peer domain contains `NET-EXT` and `DC6`.
- `A2A_PEERS` contains the peer agent URL, or the user supplied a peer URL.

Preferred tool flow:

```text
tfs_inventory()
→ local devices include DC1 and NET-EXT but do not include DC6.

a2a_provision_cross_domain_packet_service(
  service_type="l3vpn",
  src_device="DC1",
  src_endpoint="eth0",
  dst_device="DC6",
  dst_endpoint="eth0",
  peer_url="http://peer-agent:8800"
)
→ peer segment NET-EXT/access0 to DC6/eth0 is ACTIVE.
→ local segment DC1/eth0 to NET-EXT/access0 is ACTIVE.
```

Equivalent expanded flow, only if the one-shot tool is unavailable:

```text
a2a_list_peers()
a2a_discover_peer(peer_url="http://peer-agent:8800")
a2a_provision_peer_packet_service(
  peer_url="http://peer-agent:8800",
  service_type="l3vpn",
  src_device="NET-EXT",
  src_endpoint="access0",
  dst_device="DC6",
  dst_endpoint="eth0",
  name="l3vpn-netext-dc6"
)
→ peer response indicates service ACTIVE.

tfs_provision_service(
  service_type="l3vpn",
  src_device="DC1",
  src_endpoint="eth0",
  dst_device="NET-EXT",
  dst_endpoint="access0",
  name="l3vpn-dc1-netext"
)
→ local response indicates service ACTIVE.
```

Response to user:

```text
The cross-domain L3VPN request DC1 to DC6 was accepted. I provisioned the local
segment DC1 to NET-EXT and the peer domain provisioned NET-EXT to DC6. Both
segments are ACTIVE.
```

If the peer response is not successful:

```text
I could not create the end-to-end L3VPN from DC1 to DC6. The local endpoint DC1
is available, but the peer domain could not provision NET-EXT to DC6. No
end-to-end service was established.
```

## Scenario: Create L3VPN from DC1 to DC7

**Trigger phrases:** "create l3vpn between dc1 and dc7", "connect dc1 to dc7
with l3vpn", "create layer 3 vpn from dc1 to dc7"

This is the same pattern as `DC1` to `DC6`. Do not memorize `DC6`; substitute
the requested missing remote datacenter. If local inventory contains `DC1` and
`NET-EXT` but does not contain `DC7`, treat `DC7` as a possible peer-domain
endpoint and ask the peer to verify/provision it.

Preferred tool flow:

```text
tfs_inventory()
→ local devices include DC1 and NET-EXT but do not include DC7.

a2a_provision_cross_domain_packet_service(
  service_type="l3vpn",
  src_device="DC1",
  src_endpoint="eth0",
  dst_device="DC7",
  dst_endpoint="eth0",
  peer_url="http://peer-agent:8800"
)
→ peer segment NET-EXT/access0 to DC7/eth0 is ACTIVE.
→ local segment DC1/eth0 to NET-EXT/access0 is ACTIVE.
```

Equivalent expanded flow, only if the one-shot tool is unavailable:

```text
a2a_list_peers()
a2a_discover_peer(peer_url="http://peer-agent:8800")
a2a_provision_peer_packet_service(
  peer_url="http://peer-agent:8800",
  service_type="l3vpn",
  src_device="NET-EXT",
  src_endpoint="access0",
  dst_device="DC7",
  dst_endpoint="eth0",
  name="l3vpn-netext-dc7"
)
→ peer response indicates service ACTIVE.

tfs_provision_service(
  service_type="l3vpn",
  src_device="DC1",
  src_endpoint="eth0",
  dst_device="NET-EXT",
  dst_endpoint="access0",
  name="l3vpn-dc1-netext"
)
→ local response indicates service ACTIVE.
```

Response to user:

```text
The cross-domain L3VPN request DC1 to DC7 was accepted. I provisioned the local
segment DC1 to NET-EXT and the peer domain provisioned NET-EXT to DC7. Both
segments are ACTIVE.
```

## Scenario: Generic Local DC to Remote DC

Use this when the requested pair is not exactly shown in an example, such as
`DC2` to `DC5`, `DC4` to `DC7`, or `DC1` to another peer-domain datacenter.
The device names are variables. Do not assume only `DC6` is valid.

If `<local-dc>` is present in local inventory and `<remote-dc>` is absent, but
`NET-EXT` and A2A peers are available:

```text
a2a_provision_cross_domain_packet_service(
  service_type="l3vpn" or "l2vpn",
  src_device="<local-dc>",
  src_endpoint="eth0",
  dst_device="<remote-dc>",
  dst_endpoint="eth0"
)
```

The tool should request this peer segment:

```text
NET-EXT/access0 -> <remote-dc>/eth0
```

After peer success, the local segment is:

```text
<local-dc>/eth0 -> NET-EXT/access0
```

Report success only if both segments are ACTIVE.

## Scenario: Create L2VPN from DC1 to DC6 or DC7

**Trigger phrases:** "create l2vpn between dc1 and dc6", "create l2vpn between
dc1 and dc7", "create layer 2 vpn from dc1 to a remote datacenter", "ethernet
service dc1 dc7"

Use the same cross-domain sequence as L3VPN, but set `service_type="l2vpn"`.
Substitute the requested remote datacenter, for example `DC6` or `DC7`, in the
peer segment.

Peer segment:

```text
a2a_provision_peer_packet_service(
  peer_url="http://peer-agent:8800",
  service_type="l2vpn",
  src_device="NET-EXT",
  src_endpoint="access0",
  dst_device="DC6",
  dst_endpoint="eth0",
  name="l2vpn-netext-dc6"
)
```

Local segment:

```text
declare_tfs_intent(
  intent="packet_service",
  selected_tool="tfs_provision_service",
  reason="Local segment for cross-domain L2VPN DC1 to DC6."
)

tfs_provision_service(
  service_type="l2vpn",
  src_device="DC1",
  src_endpoint="eth0",
  dst_device="NET-EXT",
  dst_endpoint="access0",
  name="l2vpn-dc1-netext"
)
```

Response to user:

```text
The cross-domain L2VPN request DC1 to DC6 was accepted. I provisioned the local
segment DC1 to NET-EXT and the peer domain provisioned NET-EXT to DC6. Both
segments are ACTIVE.
```

## Scenario: User asks from the peer side

If the user asks from the peer-domain agent:

```text
create l3vpn between dc6 and dc1
create l3vpn between dc7 and dc1
```

The peer-domain agent should reverse the segmentation:

- Local segment: requested local datacenter, for example `DC6:eth0` or
  `DC7:eth0`, to `NET-EXT:access0`.
- Remote A2A segment: ask the other agent to create `NET-EXT:access0` to
  `DC1:eth0`, or to whichever remote datacenter was requested.

Use exact endpoint names from each local inventory.

## Scenario: No A2A peer configured

If `a2a_list_peers()` returns an empty list and the user did not provide a peer
URL, do not invent a peer URL.

Response:

```text
I cannot create a cross-domain service because DC6 is not in this local
controller and no A2A peer is configured. Configure A2A_PEERS with the peer
agent URL or provide the peer URL, then retry.
```

## Scenario: Peer not reachable

If `a2a_discover_peer()`, `a2a_provision_peer_packet_service()`, or
`a2a_send_peer_message()` fails due to connection timeout, HTTP error, or invalid
response:

```text
I cannot create the cross-domain service because the peer agent is unreachable
or did not accept the A2A task. The end-to-end service was not established.
```

Do not provision the local segment by itself after a peer discovery/provisioning
failure. A local-only segment would be an orphan and must not be reported as an
end-to-end cross-domain service. Do not claim local-only success as end-to-end
success.

## Scenario: Remote endpoint not found

If the peer returns `service_not_found`, `source_endpoint_not_found`,
`destination_endpoint_not_found`, `inventory_empty`, or another provisioning
failure:

```text
The end-to-end service is not possible with the current peer topology. The peer
could not resolve/provision the requested remote endpoint. Check that the requested remote datacenter, for example DC6 or DC7, exists
in the peer controller and that its packet endpoint is available.
```

## Scenario: Local segment fails after peer segment succeeds

If the peer segment succeeds but local `tfs_provision_service()` fails:

1. Report that the end-to-end service was not established.
2. Include the local error.
3. Tell the operator that the peer segment may need cleanup by deleting the peer
   service name returned in the peer artifact.

Example response:

```text
The peer segment NET-EXT to the requested remote datacenter was created, but
the local segment DC1 to NET-EXT failed, so the end-to-end L3VPN was not
established. The peer service returned in the A2A artifact, such as
'l3vpn-netext-dc6' or 'l3vpn-netext-dc7', may need to be deleted before
retrying.
```

## Success Criteria

Only report cross-domain success when both sides have a successful result:

- peer A2A task success is true; and
- peer artifact indicates the TFS service reached ACTIVE or equivalent success;
  and
- local `tfs_provision_service()` indicates the local service reached ACTIVE.

If any side reports failure, timeout, missing endpoint, missing peer, or planned
status that does not become active, report that the requested end-to-end service
is not possible at this time.
