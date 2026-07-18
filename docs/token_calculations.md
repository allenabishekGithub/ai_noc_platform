# Token Budget Analysis — TFS Agentic Module

**Date:** 2026-05-12  
**Models compared:** qwen2.5:7b-instruct vs qwen2.5:1.5b-instruct  
**OLLAMA_NUM_CTX:** 8192 tokens  
**Hardware:** NVIDIA A30 GPU (Ollama inference)

---

## 1. What Occupies the Context Window

Every time a user sends a query, the following items are assembled into the LLM's context window in order:

1. **System prompt** — static instructions and tool list
2. **Tool schemas** — JSON Schema definitions for each exposed tool (sent by LangChain)
3. **RAG chunks** — up to 4 document chunks retrieved from FAISS by similarity search
4. **User query** — the message typed by the operator
5. **Agent reasoning** — the model's chain-of-thought before each tool call
6. **Tool responses** — JSON results returned by MCP tools (compacted)

Token estimates use the rule of thumb: **1 token ≈ 4 characters** for English/JSON text.

---

## 2. Per-Item Token Breakdown

### System Prompt (~1,450 characters → ~360 tokens)

The system prompt is intentionally kept short — it lists the 9 available tools by name with one-line descriptions, then 6 behavioural rules. It does not embed examples or verbose explanations.

```
You are an AI agent that manages a TeraFlowSDN (TFS) network controller.

## Primary tools (prefer these)
- tfs_inventory         — get all devices, endpoints, and existing services in one call.
- tfs_provision_service — create a service by passing simple names (no JSON required).
- tfs_delete_service    — prepare or execute service deletion by name or UUID.
- tfs_service_status    — check service status by name or UUID.
- tfs_capabilities      — list proto/NBI/MCP-aligned TFS operations available through workflow tools.
- tfs_read              — generic read for context/topology/service/slice/device/link/connection.
- tfs_write             — generic write/delete for proto-shaped payloads and RESTCONF operations.

## Supporting tools
- rag_search            — search TFS documentation and policies.
- topology_lookup       — query the local topology graph.
- update_working_memory — save facts across turns.
- ask_user              — ask the human when information is missing.
...
```

### Tool Schemas (~2,700 characters → ~675 tokens)

LangChain serialises each tool's name, description, and JSON Schema input definition into the context. With 9 workflow tools exposed (`AGENT_EXPOSE_RAW_MCP_TOOLS=false`), this is fixed at ~675 tokens per request regardless of query content.

If `AGENT_EXPOSE_RAW_MCP_TOOLS=true` were enabled, this would jump to 52 tools × ~300 chars ≈ **~3,900 tokens** — consuming nearly half the 8,192 budget before the user even types anything. This is why the raw tools are kept off by default.

### RAG Chunks (~1,200 characters → ~300 tokens)

Before each LLM call, the agent runs a FAISS similarity search against the query and retrieves `k=4` document chunks. Each chunk is ~300 characters (~75 tokens). The 5 built-in scenario documents provide few-shot guidance on tool usage patterns.

### User Query (~25–100 tokens)

Typically short ("get all connected devices", "provision a service between R1 and R2").

### Agent Reasoning (~150–600 tokens)

Between each tool call the model generates a reasoning trace (chain-of-thought). For a 3-step query this adds ~150–600 tokens depending on verbosity.

### Tool Responses (~500–2,000 tokens)

MCP tool responses are compacted before being added to the context:
- Noisy fields (`uuid_string`, empty arrays, internal metadata) are stripped
- Nested `{"uuid": "..."}` leaves are flattened to plain strings

A typical `tfs_inventory` response for a small topology (3–4 devices, 2 services) compacts to ~2,000 characters (~500 tokens). Without compaction the same response can exceed 8,000 characters.

---

## 3. Token Budget Summary

### Typical Query (small topology, 3 tool calls)

| Item | Characters | Tokens |
|---|---|---|
| System prompt | 1,450 | 362 |
| Tool schemas (×9) | 2,700 | 675 |
| RAG chunks (×4) | 1,200 | 300 |
| User query | 100 | 25 |
| Tool responses (compacted) | 2,000 | 500 |
| Agent reasoning (3 steps) | 600 | 150 |
| **Total** | **8,050** | **~2,012** |
| **OLLAMA_NUM_CTX** | | **8,192** |
| **Headroom** | | **~6,180** |

### Worst Case (large topology, 5 tool calls, big JSON responses)

| Item | Characters | Tokens |
|---|---|---|
| System prompt | 1,450 | 362 |
| Tool schemas (×9) | 2,700 | 675 |
| RAG chunks (×4) | 1,200 | 300 |
| User query | 100 | 25 |
| Tool responses (compacted, large) | 8,000 | 2,000 |
| Agent reasoning (5 steps) | 1,000 | 250 |
| **Total** | **14,450** | **~3,612** |
| **OLLAMA_NUM_CTX** | | **8,192** |
| **Headroom** | | **~4,580** |

**Conclusion:** Even worst-case queries stay well within the 8,192-token budget. The architecture fits comfortably for both qwen2.5:7b-instruct and qwen2.5:1.5b-instruct.

---

## 4. Model Comparison: 7b vs 1.5b

Both models run on an **NVIDIA A30 GPU** via Ollama. The A30 has 24 GB HBM2 VRAM, which is more than sufficient for either model at Q4 quantisation with an 8,192-token context.

| Property | qwen2.5:7b-instruct | qwen2.5:1.5b-instruct |
|---|---|---|
| Native context length | 128K tokens | 128K tokens |
| Ollama default num_ctx | 2,048 | 2,048 |
| Configured num_ctx | 8,192 | 8,192 |
| Fits typical query? | Yes (~2,000 tokens used) | Yes (~2,000 tokens used) |
| Fits worst case? | Yes (~3,600 tokens used) | Yes (~3,600 tokens used) |
| GPU VRAM (Q4, 8K ctx) | ~6 GB | ~2 GB |
| A30 VRAM headroom | ~18 GB free | ~22 GB free |
| Tool use reliability | Good | Moderate |
| Multi-step reasoning | Good | Reduced |
| Final response generation | Reliable | Unreliable (see §5) |

Both models run comfortably within the 8,192-token window. The context window is **not** a limiting factor when switching from 7b to 1.5b. The A30 has ample VRAM for either choice.

---

## 5. Real Risk with 1.5b: Reasoning Quality, Not Context

The context window fits. The actual risks of running 1.5b are:

**Tool selection errors** — The model may pick the wrong tool or call tools in the wrong order. For example, it might skip `tfs_inventory` and attempt to call `tfs_provision_service` without first discovering device names.

**Argument hallucination** — It may fabricate device names, UUIDs, or endpoint IDs that do not exist in the topology, causing tool calls to fail.

**Destructive-operation policy compliance** — The runtime policy executes destructive operations when explicitly requested by the user. Smaller models may hesitate, ask unnecessary confirmations, or deviate from this policy.

**Early loop exit / silent completion** — On tool failure the prompt instructs the model to retry once and then call `ask_user`. The 1.5b model may instead loop endlessly or, more commonly, complete all tool calls successfully but produce no final text response. This was observed in practice: the model called `tfs_inventory` and `tfs_provision_service` correctly, but then exited without generating a summary message, leaving the user with no indication of the outcome. This is a fundamental capability gap — the model lacks the instruction-following capacity to reliably close the loop after multi-step tool chains.

### Mitigations already in place

| Risk | Mitigation |
|---|---|
| Too many tools confuse small models | `AGENT_EXPOSE_RAW_MCP_TOOLS=false` — model sees 9 tools, not 52 |
| Complex JSON arguments | High-level workflow tools accept plain names, not raw proto JSON |
| No examples in context | 53 RAG chunks provide few-shot guidance on tool call patterns |
| Runaway loops | `AGENT_MAX_ITERATIONS=15` caps the maximum tool call chain |
| Large response overflow | `MCP_HARD_RESPONSE_CAP=20000` chars + JSON compaction |

### Recommended tuning for 1.5b

If reliability issues appear, consider these adjustments in `.env`:

```bash
AGENT_MAX_ITERATIONS=10     # fail faster instead of looping
LLM_TEMPERATURE=0.1         # more deterministic outputs
OLLAMA_NUM_CTX=4096         # smaller context = faster inference, still fits budget
```

---

## 6. Decision: Use qwen2.5:7b-instruct

After testing both models in production against the TFS controller, **qwen2.5:7b-instruct is the recommended and current choice**. The 1.5b model was trialled but rejected for the following reasons observed in live operation:

- It asked the user for device names instead of calling `tfs_inventory` — even after the system prompt and tool descriptions were updated with explicit instructions not to do so.
- After fixes were applied, it successfully called `tfs_inventory` and `tfs_provision_service` in the correct order, but then exited without generating any final response, leaving the user with no confirmation of the outcome.
- Any attempt to synthesise a response from raw tool outputs in Python was rejected as unsafe — it could show a success message even when nothing was actually created on the TFS controller.

The 7b model handles all three steps reliably: selects the correct tools, calls them in the right order, and generates a coherent final response. On the A30 GPU it uses ~6 GB of the available 24 GB VRAM, leaving headroom for concurrent workloads.

To change the model, edit `.env`:

```bash
LLM_MODEL=qwen2.5:7b-instruct     # current — recommended
# LLM_MODEL=qwen2.5:1.5b-instruct # not recommended for multi-step tool use
```

Then redeploy:

```bash
./scripts/deploy-k8s.sh
```

No code changes are required. The token budget is unaffected by the model choice.
