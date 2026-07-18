# TFS Mutation Guardrails

Use these rules for any controller-changing request.

## Mandatory Rule

For requests that create, update, delete, allocate, release, or configure
controller resources, do not finalize without at least one relevant `tfs_*`
tool call in the same turn.

If no tool was called, do **not** claim success.

## Reporting Rule

Only report success when the tool observation indicates success.

- If tool result has an `error` field or clear failure text, report failure.
- If tool result is ambiguous, report it as pending/uncertain and recommend
  verification.

## Service Deletion Pattern

When user asks to delete a TFS service:

1. Call `tfs_delete_service(name_or_uuid=...)`.
2. Report the returned `deleted` state:
   - `true` -> deleted confirmed
   - `"pending"` -> delete issued, not yet confirmed
3. If needed, verify with `tfs_service_status(name_or_uuid=...)`.

Never answer "deleted successfully" without a tool call in that turn.

## Optical Slice Pattern

When user asks to create an optical slice:

1. Use `tfs_create_optical_slice(slice_id=<slice_id>, slice=...)`.
2. Do not use `tfs_provision_service` for this intent.
3. Verify status with a read path (for example `tfs_read(resource="slice", operation="list", ...)`) and report
   ACTIVE vs PLANNED from tool evidence.

When user asks to delete an optical slice:

1. Use the exact slice ID used on creation.
2. Call `tfs_delete_optical_slice(slice_id=<slice_id>)`.
3. Report the actual tool result.

If the result is ambiguous, call a verification read path available in the
environment and report that verification outcome.
