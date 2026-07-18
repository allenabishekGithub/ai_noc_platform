# Optical Connectivity Service Deletion Troubleshooting

## Scope

This document explains why `SERVICETYPE_OPTICAL_CONNECTIVITY` services can fail to delete and remain in `SERVICESTATUS_PENDING_REMOVAL` in TFS.

It focuses on controller-side behavior in `tfs-ctrl` and how that appears in `tfs-agentic-module` and WebUI.

## Observed Symptoms

- Delete request is issued from agent or WebUI.
- Service is reported as deleted (or accepted for deletion), but still appears in controller.
- Service status remains `SERVICESTATUS_PENDING_REMOVAL` (or temporarily `ACTIVE`/inconsistent).
- WebUI delete can return:
  - `Problem deleting service "<uuid>": 'NoneType' object has no attribute 'value'`

## End-to-End Delete Path

1. `tfs-agentic-module` invokes workflow delete tool:
   - `tfs_service_delete_optical_connectivity` (or alias)
2. MCP server calls TFS NBI:
   - `DELETE /tfs-api/context/<context_uuid>/service/<service_uuid>`
3. TFS NBI forwards to service microservice `DeleteService`.
4. Optical delete path executes optical deconfigure + task scheduler logic.

## Root Cause

The failure is controller-side in optical handler delete logic:

- File:
  - `tfs-ctrl/src/service/service/service_handlers/oc/OCServiceHandler.py`
- Method:
  - `DeleteEndpoint(...)`
- Problem:
  - Code fetches service settings with `self.__settings_handler.get('/settings')`
  - Then dereferences `settings.value` without checking if `settings` is `None`.
- If `/settings` is missing in service config, this raises:
  - `'NoneType' object has no attribute 'value'`

This matches the WebUI error seen during manual delete.

## Why Service Gets Stuck in PENDING_REMOVAL

`DeleteService` transitions service state into removal workflow first, then executes optical deconfigure/delete tasks.

If optical deconfigure path throws the `NoneType.value` exception, the deletion sequence does not fully complete (service object is not removed), leaving service present and often stuck in `SERVICESTATUS_PENDING_REMOVAL`.

## Important Notes About Agentic Module

- This is not caused by hardcoded workflow routing in `tfs-agentic-module`.
- Agent-side false-success responses were separately guarded by requiring mutation tool evidence.
- In this case, the underlying controller delete path is failing after request reaches TFS.

## Impact Scope (Safety)

A fix scoped to `OCServiceHandler.DeleteEndpoint()` affects optical connectivity delete path only.

It does **not** change L2VPN/L3VPN generic delete handlers when implemented as a local null-safe guard in OC handler code.

## Recommended Controller Fix

In `OCServiceHandler.DeleteEndpoint()`:

- Add null-check for settings returned by `self.__settings_handler.get('/settings')`.
- If missing, use safe defaults (for example `bidir=None`, `op_mode=None`) and continue teardown logic.
- Avoid direct `.value` dereference unless settings object is present.

Also recommended:

- Improve exception message to explicitly state missing `/settings` in service config.
- Keep delete idempotent and best-effort where safe, so stale optical services can be removed cleanly.

## Operational Verification After Fix

1. Create optical connectivity service.
2. Delete service via WebUI and via agent tool.
3. Confirm:
   - No `NoneType` exception in logs.
   - Service disappears from `ListServices`.
   - No prolonged `SERVICESTATUS_PENDING_REMOVAL`.

