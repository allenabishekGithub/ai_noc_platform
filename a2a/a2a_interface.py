# File: a2a_interface.py
"""
HTTP interface for the Agentic Module.

Exposes:
  * /a2a/*           - Agent-to-Agent JSON messaging endpoints
  * /v1/chat/completions - OpenAI-compatible endpoint usable by OpenWebUI
  * /v1/models       - model listing for OpenWebUI discovery
  * /ingest          - push documents into the FAISS RAG store
  * /healthz         - health probe

Mirrors the FastAPI layout of SDT-Manager/sdt_manager_interface.py.
"""
import asyncio
import logging
import time
import uuid
import io
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from utils.event_timing import log_event, timed_event
from utils.observability import instrument_fastapi, metrics_response, observe_http_request

LOGGER = logging.getLogger(__name__)


# ---------- Pydantic request/response models -------------------------- #
class A2AMessage(BaseModel):
    """Inbound A2A message from a peer agent."""
    sender: str
    message: str
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class A2AResponse(BaseModel):
    success: bool
    response: str
    agent_id: str
    session_id: Optional[str] = None


class IngestDocument(BaseModel):
    text: str
    metadata: Optional[Dict[str, Any]] = None


class IngestRequest(BaseModel):
    documents: List[IngestDocument]


class A2ATaskRequest(BaseModel):
    """Structured, skill-typed A2A task request."""
    sender: str
    skill_id: str
    input: Dict[str, Any] = {}
    session_id: Optional[str] = None


class A2ATaskResponse(BaseModel):
    success: bool
    skill_id: str
    artifact: Dict[str, Any]
    state_version: int
    agent_id: str
    error: Optional[str] = None


class PeerSendRequest(BaseModel):
    """Tell this agent to send a message/task to a peer agent."""
    peer_url: str
    message: Optional[str] = None             # free-form path
    skill_id: Optional[str] = None            # structured-task path
    input: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    """OpenAI-style chat completion request (subset used by OpenWebUI)."""
    model: Optional[str] = None
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    user: Optional[str] = None


# ---------- File text-extraction helper ------------------------------- #
def _extract_text(filename: str, raw: bytes) -> tuple[str, dict, any]:
    """
    Extract plain text from an uploaded file based on its extension.
    Supports: pdf, docx, yaml/yml, json, and plain text formats (txt/md/csv/code).

    Returns ``(text, extra_metadata, parsed_object)``. ``extra_metadata`` carries
    structured information (e.g. parsed YAML/JSON hints). ``parsed_object`` is
    the deserialized dict/list for YAML/JSON, or None for other formats.
    """
    ext = Path(filename).suffix.lower()
    extra: dict = {}
    parsed: any = None

    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise RuntimeError(
                "pypdf not installed - run: pip install pypdf"
            ) from e
        reader = PdfReader(io.BytesIO(raw))
        text = "\n\n".join(
            (page.extract_text() or "") for page in reader.pages
        )
        return text, extra, parsed

    if ext == ".docx":
        try:
            import docx  # python-docx
        except ImportError as e:
            raise RuntimeError(
                "python-docx not installed - run: pip install python-docx"
            ) from e
        doc = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in doc.paragraphs), extra, parsed

    # YAML: parse for structure, keep text form for embedding.
    if ext in (".yaml", ".yml"):
        text = raw.decode("utf-8", errors="replace")
        try:
            import yaml
            parsed = yaml.safe_load(text)
            extra["format"] = "yaml"
            if isinstance(parsed, dict):
                hits = [
                    k for k in (
                        "topology", "topologies", "nodes", "links",
                        "devices", "contexts", "border_links",
                        "domain", "spectrum",
                    ) if k in parsed
                ]
                if hits:
                    extra["topology_keys"] = hits
        except Exception as e:
            LOGGER.warning(f"YAML parse failed for {filename}: {e}")
        return text, extra, parsed

    # JSON: parse for structure, keep text form for embedding.
    if ext == ".json":
        text = raw.decode("utf-8", errors="replace")
        try:
            import json
            parsed = json.loads(text)
            extra["format"] = "json"
            if isinstance(parsed, dict):
                # Recognise the canonical shapes plus the TFS
                # (TeraFlowSDN) descriptor shape (`devices`, `contexts`,
                # `topologies`).
                hits = [
                    k for k in (
                        "topology", "topologies", "nodes", "links",
                        "devices", "contexts", "border_links",
                        "domain", "spectrum",
                    ) if k in parsed
                ]
                if hits:
                    extra["topology_keys"] = hits
        except Exception as e:
            LOGGER.warning(f"JSON parse failed for {filename}: {e}")
        return text, extra, parsed

    # Plain-text fallback (txt, md, csv, code, etc.)
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc), extra, parsed
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Could not decode {filename} as text")


# ---------------------------------------------------------------------- #
class A2AInterface:
    """FastAPI-based A2A + OpenWebUI HTTP interface."""

    def __init__(self, agent_core, spectrum_state=None, a2a_client=None,
                 topology_store=None):
        self.agent_core = agent_core
        self.spectrum_state = spectrum_state
        self.a2a_client = a2a_client
        self.topology_store = topology_store
        self.agent_id = f"agentic-module-{uuid.uuid4().hex[:8]}"
        # Thread pool for running blocking agent calls without blocking the
        # uvicorn event loop (which would starve liveness/readiness probes).
        self._executor = ThreadPoolExecutor(
            max_workers=int(os.environ.get("AGENT_WORKER_THREADS", "4")),
            thread_name_prefix="agent-worker",
        )
        # Public base URL announced in the Agent Card. By default we derive
        # it from AGENT_API_HOST/AGENT_API_PORT. Override AGENT_PUBLIC_URL
        # only when the agent sits behind a reverse proxy / NAT and the
        # externally reachable URL differs from the bind address.
        host = agent_core.config.get("api_host", "0.0.0.0")
        port = agent_core.config.get("api_port", 8800)
        # 0.0.0.0 is a bind wildcard, not a routable client address.
        if host in ("0.0.0.0", "::", ""):
            host = "localhost"
        self.public_url = os.environ.get(
            "AGENT_PUBLIC_URL", f"http://{host}:{port}"
        ).rstrip("/")
        self.app = FastAPI(
            title="Agentic Module API",
            description=(
                "LangChain-based agent exposing A2A messaging, "
                "OpenWebUI-compatible chat, and RAG ingestion."
            ),
            version="1.0.0",
        )
        instrument_fastapi(self.app, "tfs-agentic-agent")
        self._setup_routes()
        LOGGER.info(f"A2A Interface initialized (agent_id={self.agent_id})")

    # ------------------------------------------------------------------ #
    def _build_agent_card(self) -> Dict[str, Any]:
        """
        Construct the A2A Agent Card as defined by the A2A protocol spec.

        Served at `/.well-known/agent-card.json` (current spec) and the
        legacy `/.well-known/agent.json` path so both old and new A2A
        clients can discover this agent.
        """
        cfg = self.agent_core.config
        model = cfg.get("llm_model", "unknown")
        provider = cfg.get("llm_provider", "unknown")

        return {
            "name": "Optical Spectrum Negotiation Agent",
            "description": (
                "Inter-domain flex-grid optical spectrum negotiation agent "
                "with FAISS RAG context, MCP tool access, and an "
                "OpenAI-compatible chat endpoint."
            ),
            "url": f"{self.public_url}/a2a/message",
            "version": "1.0.0",
            "protocolVersion": "0.3.0",
            "preferredTransport": "JSONRPC",
            "provider": {
                "organization": "Agentic Module",
                "url": self.public_url,
            },
            "documentationUrl": f"{self.public_url}/docs",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": False,
            },
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "skills": [
                {
                    "id": "compute-local-spectrum-candidates",
                    "name": "Compute local spectrum candidates",
                    "description": (
                        "Computes feasible local flex-grid slot ranges for a "
                        "requested service over local path/link constraints."
                    ),
                    "tags": ["spectrum", "flex-grid", "rsa"],
                    "examples": [
                        "Compute local candidate ranges for required_slots=4 and guard_band_slots=1.",
                    ],
                },
                {
                    "id": "negotiate-interdomain-spectrum",
                    "name": "Negotiate inter-domain spectrum",
                    "description": (
                        "Exchanges proposals with peer agents and selects or "
                        "counter-proposes contiguous slot ranges that satisfy "
                        "continuity and contiguity constraints."
                    ),
                    "tags": ["a2a", "negotiation", "continuity", "contiguity"],
                    "examples": [
                        "Negotiate a common slot block for border_link A-B-1.",
                    ],
                },
                {
                    "id": "reserve-or-commit-spectrum",
                    "name": "Reserve or commit spectrum",
                    "description": (
                        "Uses deterministic controller tools to reserve, "
                        "commit, and rollback spectrum allocations."
                    ),
                    "tags": ["mcp", "reservation", "commit", "rollback"],
                },
                {
                    "id": "create-l2vpn-service",
                    "name": "Create L2VPN service",
                    "description": (
                        "Creates a packet L2VPN service between two TFS packet "
                        "endpoints using controller-backed UUID discovery and "
                        "service provisioning."
                    ),
                    "tags": ["packet", "ip", "l2vpn", "service"],
                    "examples": [
                        "Create an L2VPN from DC1 eth0 to DC2 eth0.",
                    ],
                },
                {
                    "id": "create-l3vpn-service",
                    "name": "Create L3VPN service",
                    "description": (
                        "Creates a packet L3VPN service between two TFS packet "
                        "endpoints using controller-backed UUID discovery, "
                        "service provisioning, and generated L3 endpoint config."
                    ),
                    "tags": ["packet", "ip", "l3vpn", "service"],
                    "examples": [
                        "Create an L3VPN from DC1 eth0 to DC3 eth0.",
                    ],
                },
                {
                    "id": "provision-packet-service",
                    "name": "Provision packet service",
                    "description": (
                        "Creates a packet service. The task input selects "
                        "service_type=l2vpn or service_type=l3vpn and provides "
                        "source and destination endpoint names."
                    ),
                    "tags": ["packet", "ip", "l2vpn", "l3vpn", "service"],
                },
            ],
            "additionalInterfaces": [
                {
                    "url": f"{self.public_url}/v1/chat/completions",
                    "transport": "OpenAI",
                },
                {
                    "url": f"{self.public_url}/a2a/task",
                    "transport": "JSONRPC",
                },
                {
                    "url": f"{self.public_url}/a2a/state",
                    "transport": "HTTP",
                },
            ],
            "metadata": {
                "agent_id": self.agent_id,
                "model": model,
                "provider": provider,
                # Live spectrum state - re-evaluated on every card fetch so
                # peers always see the current view.
                "spectrum_state": (
                    self.spectrum_state.snapshot()
                    if self.spectrum_state is not None
                    else None
                ),
                # Dynamically discovered MCP capabilities (server side of
                # truth). Lets peers know which deterministic tools this
                # agent can actually invoke right now.
                "mcp_capabilities": (
                    list(self.spectrum_state.capabilities().keys())
                    if self.spectrum_state is not None
                    else []
                ),
            },
        }

    # ------------------------------------------------------------------ #
    def _setup_routes(self):
        app = self.app

        @app.middleware("http")
        async def prometheus_metrics_middleware(request: Request, call_next):
            started_at = time.perf_counter()
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code
                return response
            finally:
                observe_http_request(request, status_code, started_at)

        @app.middleware("http")
        async def timing_middleware(request: Request, call_next):
            with timed_event(
                LOGGER,
                "http_request",
                method=request.method,
                path=request.url.path,
            ):
                response = await call_next(request)
            log_event(
                LOGGER,
                "http_response",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
            )
            return response

        @app.get("/metrics", include_in_schema=False)
        async def metrics():
            return metrics_response()

        @app.get("/healthz")
        async def healthz():
            return {"status": "ok", "agent_id": self.agent_id}

        # ---- A2A Agent Card (well-known discovery) ------------------ #
        @app.get("/.well-known/agent-card.json")
        async def agent_card():
            return JSONResponse(self._build_agent_card())

        # Legacy path kept for older A2A clients.
        @app.get("/.well-known/agent.json")
        async def agent_card_legacy():
            return JSONResponse(self._build_agent_card())

        # ---- Agent metadata (A2A discovery) -------------------------- #
        @app.get("/a2a/info")
        async def a2a_info():
            return {
                "agent_id": self.agent_id,
                "capabilities": ["chat", "rag", "mcp"],
                "model": self.agent_core.config.get("llm_model"),
                "provider": self.agent_core.config.get("llm_provider"),
            }

        @app.post("/a2a/message", response_model=A2AResponse)
        async def a2a_message(msg: A2AMessage):
            with timed_event(LOGGER, "a2a_message", sender=msg.sender):
                LOGGER.info(f"A2A message from {msg.sender}: {msg.message[:120]}")
                sid = msg.session_id or msg.sender
                try:
                    loop = asyncio.get_event_loop()
                    answer = await loop.run_in_executor(
                        self._executor,
                        lambda: self.agent_core.ask(msg.message, session_id=sid),
                    )
                except Exception as e:
                    LOGGER.error(f"A2A handling failed: {e}")
                    raise HTTPException(status_code=500, detail=str(e))

                return A2AResponse(
                    success=True,
                    response=answer,
                    agent_id=self.agent_id,
                    session_id=sid,
                )

        # ---- Live spectrum-state snapshot --------------------------- #
        @app.get("/a2a/state")
        async def a2a_state():
            if self.spectrum_state is None:
                return {"state_version": 0, "candidates": {}, "reservations": {}}
            return self.spectrum_state.snapshot()

        @app.get("/a2a/cache")
        async def a2a_cache():
            cache = getattr(self.agent_core, "controller_cache", None)
            if cache is None:
                return {"default_ttl_seconds": 0, "resources": {}}
            return cache.snapshot()

        @app.get("/a2a/execution/{session_id}")
        async def a2a_execution(session_id: str):
            trace = self.agent_core.get_last_trace(session_id=session_id)
            if not trace:
                return {"session_id": session_id, "trace": None}
            return {"session_id": session_id, "trace": trace}

        # ---- Topology graph ----------------------------------------- #
        @app.get("/a2a/topology")
        async def a2a_topology(source: Optional[str] = None):
            if self.topology_store is None:
                raise HTTPException(status_code=503, detail="No topology store")
            data = self.topology_store.get(source)
            if not data:
                return {"sources": [], "topology": None}
            return {
                "sources": self.topology_store.sources(),
                "topology": data,
            }

        @app.get("/a2a/topology/path")
        async def a2a_topology_path(
            src: str, dst: str, source: Optional[str] = None
        ):
            if self.topology_store is None:
                raise HTTPException(status_code=503, detail="No topology store")
            path = self.topology_store.shortest_path(src, dst, source)
            return {"src": src, "dst": dst, "path": path}

        # ---- Structured, skill-typed A2A task ----------------------- #
        @app.post("/a2a/task", response_model=A2ATaskResponse)
        async def a2a_task(req: A2ATaskRequest):
            with timed_event(LOGGER, "a2a_task", sender=req.sender, skill_id=req.skill_id):
                LOGGER.info(
                    f"A2A task from {req.sender}: skill={req.skill_id} "
                    f"input_keys={list((req.input or {}).keys())}"
                )
                try:
                    loop = asyncio.get_event_loop()
                    artifact = await loop.run_in_executor(
                        self._executor,
                        lambda: self.agent_core.dispatch_skill(
                            skill_id=req.skill_id,
                            payload=req.input or {},
                            sender=req.sender,
                            session_id=req.session_id,
                        ),
                    )
                    return A2ATaskResponse(
                        success=True,
                        skill_id=req.skill_id,
                        artifact=artifact,
                        state_version=(
                            self.spectrum_state.version
                            if self.spectrum_state is not None else 0
                        ),
                        agent_id=self.agent_id,
                    )
                except KeyError as e:
                    raise HTTPException(status_code=404, detail=f"Unknown skill: {e}")
                except Exception as e:
                    LOGGER.error(f"a2a_task failed: {e}")
                    return A2ATaskResponse(
                        success=False,
                        skill_id=req.skill_id,
                        artifact={},
                        state_version=(
                            self.spectrum_state.version
                            if self.spectrum_state is not None else 0
                        ),
                        agent_id=self.agent_id,
                        error=str(e),
                    )

        # ---- Outbound: contact a peer agent ------------------------- #
        @app.post("/a2a/peer/discover")
        async def peer_discover(body: Dict[str, Any]):
            if self.a2a_client is None:
                raise HTTPException(status_code=503, detail="No outbound A2A client")
            peer_url = body.get("peer_url")
            if not peer_url:
                raise HTTPException(status_code=400, detail="peer_url required")
            with timed_event(LOGGER, "peer_discover", peer_url=peer_url):
                try:
                    card = self.a2a_client.discover(peer_url)
                    state = None
                    try:
                        state = self.a2a_client.fetch_state(peer_url)
                    except Exception:
                        pass  # peer may not implement /a2a/state
                    return {"agent_card": card, "state": state}
                except Exception as e:
                    raise HTTPException(status_code=502, detail=str(e))

        @app.post("/a2a/peer/send")
        async def peer_send(body: PeerSendRequest):
            if self.a2a_client is None:
                raise HTTPException(status_code=503, detail="No outbound A2A client")
            with timed_event(LOGGER, "peer_send", peer_url=body.peer_url):
                try:
                    if body.skill_id:
                        return self.a2a_client.send_task(
                            peer_base_url=body.peer_url,
                            skill_id=body.skill_id,
                            input_payload=body.input or {},
                            session_id=body.session_id,
                        )
                    if body.message is None:
                        raise HTTPException(
                            status_code=400,
                            detail="Either 'skill_id'+'input' or 'message' required",
                        )
                    return self.a2a_client.send_message(
                        peer_base_url=body.peer_url,
                        message=body.message,
                        session_id=body.session_id,
                        metadata=body.metadata,
                    )
                except HTTPException:
                    raise
                except Exception as e:
                    raise HTTPException(status_code=502, detail=str(e))

        # ---- RAG ingestion ------------------------------------------ #
        @app.get("/rag/stats")
        async def rag_stats():
            stats = self.agent_core.rag.vector_store.get_stats()
            return stats

        @app.post("/ingest")
        async def ingest(req: IngestRequest):
            with timed_event(LOGGER, "http_ingest", documents=len(req.documents)):
                docs = [d.model_dump() for d in req.documents]
                added = self.agent_core.ingest_documents(docs)
                return {"success": True, "chunks_added": added}

        # ---- File upload (multipart) for the web UI ------------------ #
        @app.post("/upload")
        async def upload_files(files: List[UploadFile] = File(...)):
            """
            Accept one or more files (pdf, txt, md, docx, json, csv, code) and
            ingest their extracted text into the FAISS RAG store.
            """
            with timed_event(LOGGER, "http_upload", files=len(files)):
                results = []
                docs_to_ingest: List[Dict[str, Any]] = []
                for f in files:
                    try:
                        with timed_event(LOGGER, "upload_file", filename=f.filename or "uploaded"):
                            raw = await f.read()
                            text, extra_meta, parsed = _extract_text(
                                f.filename or "uploaded", raw
                            )
                            if not text.strip():
                                results.append({
                                    "filename": f.filename,
                                    "status": "skipped",
                                    "reason": "no extractable text",
                                })
                                continue
                            md = {
                                "source": f.filename,
                                "content_type": f.content_type,
                                "size_bytes": len(raw),
                            }
                            md.update(extra_meta or {})
                            docs_to_ingest.append({
                                "text": text,
                                "metadata": md,
                            })
                            # Also feed the parsed structure into the topology graph.
                            topo_summary = None
                            if (
                                self.topology_store is not None
                                and parsed is not None
                                and md.get("topology_keys")
                            ):
                                topo_summary = self.topology_store.load(
                                    f.filename or "uploaded", parsed
                                )
                            results.append({
                                "filename": f.filename,
                                "status": "queued",
                                "chars": len(text),
                                "topology": topo_summary,
                            })
                    except Exception as e:
                        LOGGER.error(f"Failed to read upload {f.filename}: {e}")
                        results.append({
                            "filename": f.filename,
                            "status": "error",
                            "reason": str(e),
                        })

                chunks_added = 0
                if docs_to_ingest:
                    chunks_added = self.agent_core.ingest_documents(docs_to_ingest)

                return {
                    "success": True,
                    "files": results,
                    "chunks_added": chunks_added,
                }

        # ---- Static web UI ------------------------------------------ #
        static_dir = Path(__file__).parent.parent / "static"
        if static_dir.is_dir():
            app.mount(
                "/static", StaticFiles(directory=str(static_dir)), name="static"
            )

            @app.get("/", include_in_schema=False)
            async def root():
                index = static_dir / "index.html"
                if index.is_file():
                    return FileResponse(str(index))
                raise HTTPException(status_code=404, detail="UI not found")

        # ---- OpenAI-compatible endpoints (for OpenWebUI) ------------ #
        @app.get("/v1/models")
        async def list_models():
            model_id = self.agent_core.config.get("llm_model", "agentic-module")
            return {
                "object": "list",
                "data": [
                    {
                        "id": model_id,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "agentic-module",
                    }
                ],
            }

        @app.post("/v1/chat/completions")
        async def chat_completions(req: ChatCompletionRequest):
            # Concatenate non-system messages into a single prompt;
            # the LangChain agent maintains its own session history.
            user_messages = [m.content for m in req.messages if m.role == "user"]
            if not user_messages:
                raise HTTPException(status_code=400, detail="No user message")
            prompt = user_messages[-1]
            session_id = req.user or "openwebui"

            try:
                with timed_event(
                    LOGGER,
                    "chat_completion",
                    session_id=session_id,
                    prompt_len=len(prompt),
                ):
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        self._executor,
                        lambda: self.agent_core.ask_detailed(prompt, session_id=session_id),
                    )
            except Exception as e:
                LOGGER.error(f"chat_completions failed: {e}")
                raise HTTPException(status_code=500, detail=str(e))

            now = int(time.time())
            model_id = req.model or self.agent_core.config.get(
                "llm_model", "agentic-module"
            )
            answer = result.get("answer", "")
            trace = result.get("trace", {})
            return JSONResponse({
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": now,
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
                "agent_trace": trace,
            })

    # ------------------------------------------------------------------ #
    def run(self, host: str = "0.0.0.0", port: int = 8800):
        log_event(LOGGER, "uvicorn_start", host=host, port=port)
        uvicorn.run(self.app, host=host, port=port, log_level="info")
