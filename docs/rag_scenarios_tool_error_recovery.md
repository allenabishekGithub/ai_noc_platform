# TFS Tool Error Recovery Scenarios

Use this playbook whenever a TFS tool returns an error.

## Rule: Never loop on the same invalid call

If a tool returns an error, do not call the same tool again with the same
arguments. Either fix arguments or ask the user for missing information.

## Scenario: `tfs_write` says unsupported write operation

Example error:

```json
{
  "error": "Unsupported write operation: optical_slice.set",
  "resource": "optical_slice",
  "attempted_operation": "set",
  "supported_write_operations": ["create", "delete"]
}
```

Correct behavior:

1. Read supported operations from the error.
2. Retry with a valid operation only.
3. If user intent is create, use `operation="create"`.
4. If user intent is delete, use `operation="delete"`.

## Scenario: `tfs_write` says missing required argument(s)

Example error:

```json
{
  "error": "missing required argument(s)",
  "missing": ["slice_id"],
  "args": {"slice_id": "", "slice": {...}},
  "do_not_retry_same_arguments": true
}
```

Correct behavior:

1. Ask the user only for the missing field(s), by exact name.
2. Do not retry until missing field(s) are provided.
3. Then call the tool once with corrected arguments.

## Scenario: Under-specified optical slice request

User request:

`create an optical slice from dc2 to dc4`

Correct behavior:

1. Resolve endpoint/device grounding from inventory if needed.
2. Collect required optical-slice arguments if missing, especially `slice_id`.
3. Execute:

```python
tfs_create_optical_slice(
  slice_id="slice-dc2-dc4-001",
  slice={...}
)
```

4. Report tool result; do not claim success without successful tool evidence.
