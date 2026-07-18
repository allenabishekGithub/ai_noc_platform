# TFS Error Recovery Scenarios

## Scenario: MCP server is offline or tool not available

**Symptom:** Tool call returns "MCP tool 'tfs_...' not available (server may be offline)."

**Correct behavior:**
1. Check controller health first.
2. Tell the user the MCP server appears unreachable.
3. Do not retry in a loop — ask the user to check the server.

```
tfs_read(resource="health", operation="check")
→ {"result": {"error": "Request timeout", "status": "error"}}

ask_user("The TFS controller appears unreachable (health check failed). Please verify the MCP server is running and the controller URL is correct.")
```

---

## Scenario: Service provisioning fails — HTTP 4xx error from TFS

**Symptom:** `tfs_provision_service` returns "Error reserving service UUID: HTTP 400: ..."

**Correct behavior:**
1. Read the full error message.
2. Common causes and fixes:
   - "Missing required field" → the service_id or endpoint_id structure was malformed. This is a bug in the workflow tool — report it.
   - "UUID already exists" → a service with that UUID already exists. `tfs_provision_service` generates a new UUID each call so this should not happen unless there's a collision (extremely rare).
   - "Device not found" → the device UUID resolved incorrectly. Call `tfs_inventory()` again to re-check.

```
tfs_inventory()
→ re-check that device names and endpoint names match exactly.

ask_user("Service creation failed with error: '<error text>'. Should I retry, or would you like to check the controller configuration?")
```

---

## Scenario: Service provisioning fails — HTTP 5xx error from TFS

**Symptom:** "Error applying service intent: HTTP 500: Internal Server Error"

**Meaning:** The controller accepted the service UUID reservation but failed during path computation or configuration push.

**Correct behavior:**
1. Check whether the service was partially created.
2. Call `tfs_read(resource="service", operation="get", service_uuid="<uuid>")` to check the state.
3. If it exists in PLANNED or ERROR state, tell the user and offer to delete and retry.

```
tfs_read(resource="service", operation="get", service_uuid="f5e6...", context_uuid="admin")
→ {"status": "SERVICESTATUS_PLANNED"}  # or ERROR

ask_user("The service was created but the controller returned a 500 error during provisioning. Current status is PLANNED. Options: (1) wait and check again, (2) delete and retry, (3) leave as-is.",
         candidates=["Wait and check again", "Delete and retry", "Leave as-is"])
```

---

## Scenario: Delete fails — service still appears after deletion

**Symptom:** `tfs_delete_service(...)` returns `"deleted": "pending"`.

**Meaning:** The delete API call was accepted but the service still appears in the listing. The controller may need a moment to propagate.

**Correct behavior:** Wait a moment and check again.

```
tfs_service_status(name_or_uuid="svc-r1-r2")
→ "Service 'svc-r1-r2' not found"   # confirming it was deleted
```

If after 3 checks it still appears, escalate:
```
ask_user("The service delete API returned success but 'svc-r1-r2' still appears in the service list after multiple checks. You may need to investigate the controller state manually.")
```

---

## Scenario: Endpoint resolution fails — partial name match is ambiguous

**Symptom:** `tfs_provision_service` returns "device 'R' endpoint '1/1' not found" when the user said "R".

**Situation:** The user gave a partial device name that matches multiple devices.

**Correct behavior:** Never guess. List all matching candidates and ask.

```
tfs_inventory()
→ devices: ["R1", "R2", "R3"]  # all start with "R"

ask_user("'R' matches multiple devices: R1, R2, R3. Which device did you mean?",
         candidates=["R1", "R2", "R3"])
```

---

## Scenario: Working memory is stale — UUIDs have changed

**Situation:** Working memory from a previous turn contains device UUIDs that no longer match (e.g., after controller restart or topology change).

**Symptom:** Tool calls fail with "Device not found" or "Service not found" despite working memory showing valid UUIDs.

**Correct behavior:** Discard stale working memory, call `tfs_inventory()` to refresh, and update working memory with fresh data.

```
tfs_inventory()
→ (fresh device list with current UUIDs)

update_working_memory("REFRESHED: context=admin, topology=admin, R1 uuid=<new-uuid> ep1/1=<new-ep-uuid>, ...")
```

---

## Scenario: User asks for something TFS does not support

**Trigger:** User asks about a TFS proto RPC that is not exposed by the current MCP server, e.g., event streams, SelectDevice, UnsetService.

**Correct behavior:** Tell the user the operation is not available through the current MCP server interface.

```
tfs_capabilities(detail="full")
→ "proto_capabilities_not_exposed_by_current_mcp": [
    "GetContextEvents / GetTopologyEvents / ... event streams",
    "SelectDevice / SelectService / SelectSlice filter RPCs",
    "UnsetService and UnsetSlice as distinct operations",
    ...
  ]
```

**Response:** "The operation you requested (e.g., event streaming) is defined in the TFS proto but is not exposed by the current MCP server. You would need to extend the MCP server or use the TFS REST API directly."

---

## Scenario: User asks a general TFS question — use RAG

**Trigger:** User asks "how does TFS handle path computation?" or "what is a context in TFS?" or "explain the service lifecycle."

**Correct behavior:** Do not guess. Use `rag_search` first to retrieve relevant documentation.

```
rag_search(query="TFS path computation service lifecycle")
→ (retrieved relevant document chunks)
```

Then answer based on retrieved context. If no relevant documents are found, say so and provide what you know from your training, clearly labeled as general knowledge rather than TFS-specific documentation.

---

## Scenario: Multi-step task — full service end-to-end with verification

**Situation:** User wants to provision a service and verify it is working end-to-end.

**Complete sequence:**
```
1. tfs_inventory()                          → discover devices and existing services
2. tfs_provision_service(...)               → provision immediately (no prior confirmation needed)
3. tfs_service_status(name_or_uuid=...)     → verify ACTIVE
4. tfs_read(resource="connection", ...)     → inspect connections created by controller
```

**Response after step 6:** "Service 'svc-r1-r2' is ACTIVE. The controller has established 1 connection (ID: conn-001). The service is fully operational."
