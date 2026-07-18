# Planner + Executor Analysis

## Summary

An earlier request path in this repository was dominated by static keyword
matching rules. That approach was too rigid for
controller-backed service creation because it narrowed the tool set before the
agent had discovered the prerequisites needed to execute the request.

The most visible symptom was the response:

`No topologies are currently configured in the controller.`

In practice, that answer was often produced when the local topology cache was
empty and the routed tool subset did not permit the agent to perform the
controller-side discovery needed to recover.

## Root Causes

1. Query routing was based on substring matching rather than task planning.
2. Tool availability was scoped to a fixed MCP subset before prerequisite
   discovery had happened.
3. Service creation was modeled as a one-shot action instead of a dependency
   chain.
4. Local topology state was populated only from manual uploads, not from the
   controller on demand.
5. Many TFS tools require `context_uuid`, but routing rules did not model
   context selection and discovery as a prerequisite step.

## Important TFS Assumptions

For this repository, the controller-side defaults should be treated as:

- default context: `admin`
- default topology: `admin`

The `admin` topology inside the `admin` context is expected to contain the full
controller inventory. Other topologies may exist for specific technologies or
domains, but `admin/admin` is the natural starting point for discovery.

## Recommended Architecture

Replace static routing as the primary orchestration mechanism with a
planner-plus-executor design:

1. Task understanding
   - infer intent
   - infer service family
   - extract constraints
   - extract endpoint labels
2. Plan construction
   - produce ordered, explainable steps
   - represent controller discovery as explicit prerequisites
3. Execution with a blackboard
   - persist intermediate state
   - compose outputs from earlier steps into later steps
   - replan or stop when inputs are missing
4. Result synthesis
   - answer the user with a grounded summary
   - expose the plan and blackboard for explainability

## Minimal Execution Pattern for Service Creation

For requests such as:

`create a 1 Gbps L3VPN between cluster a and cluster b`

the agent should follow a pattern like:

1. refresh controller inventory
2. select context, defaulting to `admin`
3. load `admin/admin` topology details into the local topology cache
4. resolve endpoint labels against devices and endpoints
5. compute path or candidate connectivity
6. build the service payload
7. create the service only if required inputs are grounded
8. refresh stale service state
9. return a grounded explanation, including any ambiguity

## Explainable AI Requirement

The web UI should surface:

- the current plan
- per-step status
- the blackboard state
- cache freshness information

That allows the user to inspect why the agent selected specific tools, what it
learned from them, and where any ambiguity remains.

## Cache Design

The local controller cache should:

- populate devices, links, services, topologies, and contexts on demand
- load `admin/admin` topology data into the local deterministic topology store
- carry timestamps and TTL-based freshness checks
- allow stale data to be shown explicitly rather than silently trusted
- invalidate or refresh action-related resources after controller mutations

## Bottom Line

The problem is not only prompt quality. It is architectural. The system needs
to separate:

- task understanding
- prerequisite discovery
- ordered tool execution
- answer synthesis

The implementation added in this refactor follows that direction by centering
execution on a planner, an executor, a blackboard, and an expiring
controller-backed cache.

Update note:

The current implementation now follows the real TFS service lifecycle for
controller-backed service creation:

1. reserve the service UUID through `CreateService`
2. update the reserved service with endpoints, constraints, and config rules
3. verify the resulting service state through `GetService`

This means path computation is treated as controller-driven. The local topology
graph is still useful for endpoint grounding, topology context, and explainable
execution traces, but not as the final provisioning authority.
