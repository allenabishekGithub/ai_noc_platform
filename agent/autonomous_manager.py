# File: autonomous_manager.py
"""
AutonomousManager orchestrates all sub-components of the Agentic Module:

  * LangChain agent (LLM + tools + RAG retriever)
  * MCP client      - exposes remote MCP tools to the agent
  * A2A interface   - FastAPI server for Agent-to-Agent messaging and
                      OpenWebUI-compatible chat endpoints
  * FAISS vector DB - local RAG store

Mirrors the structure of SDT-Manager/sdt_manager_core.py.
"""
import logging
import json
import os
import threading

from rag.vector_store import FaissVectorStore
from rag.rag_manager import RAGManager
from mcp_integration.mcp_client import MCPClient
from agent.langchain_agent import LangChainAgent
from a2a.a2a_interface import A2AInterface
from network.spectrum_state import SpectrumState
from a2a.a2a_client import A2AClient
from network.topology_store import TopologyStore
from network.controller_cache import ControllerCache
from memory.session_memory_manager import SessionMemoryManager
from utils.event_timing import log_event, timed_event

LOGGER = logging.getLogger(__name__)


class AutonomousManager:
    def __init__(self):
        LOGGER.info("Initializing Autonomous Manager...")

        # --- Configuration from environment ------------------------------
        api_host = os.environ.get("AGENT_API_HOST", "0.0.0.0")
        api_port = int(os.environ.get("AGENT_API_PORT", "8800"))

        faiss_index_path = os.environ.get("FAISS_INDEX_PATH", "./faiss_index")
        embedding_model = os.environ.get(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )

        llm_provider = os.environ.get("LLM_PROVIDER", "openai")
        llm_model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
        controller_cache_ttl_seconds = int(
            os.environ.get("CONTROLLER_CACHE_TTL_SECONDS", "120")
        )

        mcp_servers_env = os.environ.get("MCP_SERVERS", "")  # comma-separated URLs

        self.config = {
            "api_host": api_host,
            "api_port": api_port,
            "faiss_index_path": faiss_index_path,
            "embedding_model": embedding_model,
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "controller_cache_ttl_seconds": controller_cache_ttl_seconds,
        }

        # --- FAISS vector store + RAG ------------------------------------
        with timed_event(LOGGER, "init_vector_store", index_path=faiss_index_path):
            self.vector_store = FaissVectorStore(
                index_path=faiss_index_path,
                embedding_model=embedding_model,
            )
        LOGGER.info("FAISS Vector Store has been instantiated")

        with timed_event(LOGGER, "init_rag_manager"):
            self.rag = RAGManager(vector_store=self.vector_store)
        LOGGER.info("RAG Manager has been instantiated")

        # --- MCP client --------------------------------------------------
        mcp_servers = [s.strip() for s in mcp_servers_env.split(",") if s.strip()]
        with timed_event(LOGGER, "init_spectrum_state"):
            self.spectrum_state = SpectrumState()
        LOGGER.info("Spectrum State store has been instantiated")

        with timed_event(LOGGER, "init_topology_store"):
            self.topology_store = TopologyStore()
        LOGGER.info("Topology Store has been instantiated")

        with timed_event(
            LOGGER,
            "init_controller_cache",
            ttl_seconds=controller_cache_ttl_seconds,
        ):
            self.controller_cache = ControllerCache(
                default_ttl_seconds=controller_cache_ttl_seconds
            )
        LOGGER.info("Controller Cache has been instantiated")

        with timed_event(LOGGER, "init_session_memory_manager"):
            self.session_memory = SessionMemoryManager()
        LOGGER.info("Session Memory Manager has been instantiated")

        with timed_event(LOGGER, "init_mcp_client", servers=len(mcp_servers)):
            self.mcp_client = MCPClient(
                server_urls=mcp_servers,
                spectrum_state=self.spectrum_state,
            )
        LOGGER.info(
            f"MCP Client has been instantiated with {len(mcp_servers)} server(s)"
        )

        # --- LangChain agent (combines LLM, RAG retriever, MCP tools) ----
        with timed_event(
            LOGGER,
            "init_langchain_agent",
            provider=llm_provider,
            model=llm_model,
        ):
            self.agent = LangChainAgent(
                llm_provider=llm_provider,
                llm_model=llm_model,
                rag_manager=self.rag,
                mcp_client=self.mcp_client,
                topology_store=self.topology_store,
                controller_cache=self.controller_cache,
                session_memory=self.session_memory,
            )
        LOGGER.info(
            f"LangChain Agent has been instantiated (provider={llm_provider}, "
            f"model={llm_model})"
        )

        # --- A2A / OpenWebUI HTTP interface ------------------------------
        with timed_event(LOGGER, "init_a2a_interface", host=api_host, port=api_port):
            self.a2a_client = A2AClient(sender_id=f"agentic-module@{api_host}:{api_port}")
            self.a2a = A2AInterface(
                self,
                spectrum_state=self.spectrum_state,
                a2a_client=self.a2a_client,
                topology_store=self.topology_store,
            )
        LOGGER.info(
            f"A2A Interface has been instantiated and will listen on "
            f"{api_host}:{api_port}"
        )

        # --- Seed built-in TFS knowledge into RAG ------------------------
        self._ingest_builtin_knowledge()

        self.terminate_event = threading.Event()
        self.api_thread: threading.Thread | None = None
        self._stop_lock = threading.Lock()
        self._stopped = False

        log_event(LOGGER, "autonomous_manager_initialized")

    # ------------------------------------------------------------------ #
    # Built-in knowledge seeding                                        #
    # ------------------------------------------------------------------ #
    def _ingest_builtin_knowledge(self) -> None:
        """Ingest TFS domain knowledge into the RAG store at startup.

        Ingests each built-in document version once, keyed by source tag and
        content hash, so edited docs are added after deployment.
        """
        import hashlib
        import os as _os
        docs_dir = _os.path.join(_os.path.dirname(__file__), "..", "docs")
        knowledge_files = [
            ("tfs_agent_knowledge.md",                  "tfs-agent-knowledge-builtin"),
            ("rag_tfs_network_model.md",                "tfs-network-model"),
            ("rag_diverse_topologies.md",               "tfs-diverse-topologies"),
            ("rag_scenarios_service_provisioning.md",   "tfs-scenarios-provisioning"),
            ("rag_scenarios_a2a_packet_services.md", "tfs-scenarios-a2a-packet-services"),
            ("rag_scenarios_service_management.md",     "tfs-scenarios-management"),
            ("rag_scenarios_advanced_operations.md",    "tfs-scenarios-advanced"),
            ("rag_scenarios_error_recovery.md",         "tfs-scenarios-error-recovery"),
            ("rag_scenarios_mutation_guardrails.md",    "tfs-scenarios-mutation-guardrails"),
            ("rag_scenarios_tool_error_recovery.md",    "tfs-scenarios-tool-error-recovery"),
        ]
        for filename, source_tag in knowledge_files:
            path = _os.path.join(docs_dir, filename)
            if not _os.path.isfile(path):
                LOGGER.warning("Built-in knowledge file not found: %s", path)
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if self.rag.has_source_hash(source_tag, content_hash):
                    LOGGER.info("Built-in knowledge already present: %s", filename)
                    continue
                self.rag.add_documents([{
                    "text": text,
                    "metadata": {
                        "source": source_tag,
                        "source_file": filename,
                        "content_sha256": content_hash,
                    },
                }])
                LOGGER.info(
                    "Ingested built-in knowledge: %s (%d chars, sha256=%s)",
                    filename,
                    len(text),
                    content_hash[:12],
                )
            except Exception as exc:
                LOGGER.warning("Failed to ingest built-in knowledge %s: %s", filename, exc)

    # ------------------------------------------------------------------ #
    # Lifecycle                                                          #
    # ------------------------------------------------------------------ #
    def start(self):
        """Start the HTTP server (A2A + OpenWebUI) in a background thread."""
        LOGGER.info("Starting Agentic Module HTTP server...")

        host = self.config["api_host"]
        port = self.config["api_port"]

        with timed_event(LOGGER, "start_http_server_thread", host=host, port=port):
            self.api_thread = threading.Thread(
                target=self.a2a.run,
                kwargs={"host": host, "port": port},
                daemon=True,
                name="a2a-http-server",
            )
            self.api_thread.start()
        LOGGER.info(f"Agentic Module API thread started on {host}:{port}")

    def stop(self):
        """Stop background workers."""
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True

        with timed_event(LOGGER, "stop_agentic_module"):
            LOGGER.info("Stopping Agentic Module...")
            self.terminate_event.set()
            try:
                with timed_event(LOGGER, "close_mcp_client"):
                    self.mcp_client.close()
            except Exception as e:
                LOGGER.warning(f"Error closing MCP client: {e}")
            try:
                with timed_event(LOGGER, "persist_vector_store"):
                    self.vector_store.persist()
            except Exception as e:
                LOGGER.warning(f"Error persisting vector store: {e}")
            LOGGER.info("Agentic Module stopped")

    # ------------------------------------------------------------------ #
    # High-level helpers used by CLI, A2A and OpenWebUI endpoints        #
    # ------------------------------------------------------------------ #
    def ask(self, query: str, session_id: str | None = None) -> str:
        """Run a single query through the LangChain agent and return text."""
        with timed_event(
            LOGGER,
            "agent_ask",
            session_id=session_id or "none",
            query_len=len(query or ""),
        ):
            return self.agent.run(query, session_id=session_id)

    def ask_detailed(self, query: str, session_id: str | None = None) -> dict:
        with timed_event(
            LOGGER,
            "agent_ask_detailed",
            session_id=session_id or "none",
            query_len=len(query or ""),
        ):
            return self.agent.run_detailed(query, session_id=session_id)

    def get_last_trace(self, session_id: str | None = None) -> dict:
        return self.agent.get_last_trace(session_id=session_id)

    def ingest_documents(self, docs: list[dict]) -> int:
        """
        Ingest documents into the FAISS RAG store.

        Each doc: {"text": "...", "metadata": {...}}.
        Returns the number of chunks added.
        """
        with timed_event(LOGGER, "ingest_documents", documents=len(docs)):
            return self.rag.add_documents(docs)

    # ------------------------------------------------------------------ #
    # Skill dispatch                                                     #
    # ------------------------------------------------------------------ #
    # Maps A2A skill ids to MCP tool-name fragments. Picks the first
    # discovered MCP tool whose name contains the fragment.
    _SKILL_TO_TOOL_HINT = {
        "compute-local-spectrum-candidates": "compute_local_spectrum_candidates",
        "negotiate-interdomain-spectrum": "negotiate_interdomain_spectrum",
        "reserve-or-commit-spectrum": "reserve_or_commit_spectrum",
    }

    _PACKET_SERVICE_SKILLS = {
        "create-l2vpn-service": "l2vpn",
        "create-l3vpn-service": "l3vpn",
        "provision-packet-service": None,
    }

    def _dispatch_packet_service_skill(
        self,
        skill_id: str,
        payload: dict,
        sender: str | None = None,
    ) -> dict:
        """Provision L2VPN/L3VPN services from a structured A2A task."""
        service_type = self._PACKET_SERVICE_SKILLS[skill_id] or payload.get("service_type")
        required = ["src_device", "src_endpoint", "dst_device", "dst_endpoint"]
        missing = [field for field in required if not payload.get(field)]
        if not service_type:
            missing.append("service_type")
        if missing:
            return {
                "skill_id": skill_id,
                "sender": sender,
                "ok": False,
                "error": "missing_required_arguments",
                "missing": missing,
                "required": required + ([] if self._PACKET_SERVICE_SKILLS[skill_id] else ["service_type"]),
            }

        normalized_type = str(service_type).lower().replace("-", "_")
        if normalized_type not in {"l2vpn", "l2nm", "l2", "l3vpn", "l3nm", "l3"}:
            return {
                "skill_id": skill_id,
                "sender": sender,
                "ok": False,
                "error": "unsupported_service_type",
                "service_type": service_type,
                "supported_service_types": ["l2vpn", "l3vpn"],
            }

        result_text = self.agent.workflow.provision_service(
            service_type=normalized_type,
            src_device=str(payload["src_device"]),
            src_endpoint=str(payload["src_endpoint"]),
            dst_device=str(payload["dst_device"]),
            dst_endpoint=str(payload["dst_endpoint"]),
            name=payload.get("name"),
            capacity_gbps=payload.get("capacity_gbps"),
            mtu=int(payload.get("mtu", 1512)),
            context_uuid=str(payload.get("context_uuid", "admin")),
        )
        try:
            structured = json.loads(result_text)
        except Exception:
            structured = None

        return {
            "skill_id": skill_id,
            "tool": "tfs_provision_service",
            "sender": sender,
            "service_type": normalized_type,
            "structured": structured,
            "text": result_text,
            "state_version": self.spectrum_state.version,
        }

    def dispatch_skill(
        self,
        skill_id: str,
        payload: dict,
        sender: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Route a structured A2A task to the appropriate MCP tool.

        Returns the structured artifact (dict). Falls back to invoking the
        LangChain agent for unknown skills.
        """
        with timed_event(
            LOGGER,
            "dispatch_skill",
            skill_id=skill_id,
            sender=sender or "unknown",
        ):
            if skill_id in self._PACKET_SERVICE_SKILLS:
                return self._dispatch_packet_service_skill(
                    skill_id=skill_id,
                    payload=payload,
                    sender=sender,
                )

            hint = self._SKILL_TO_TOOL_HINT.get(skill_id)
            if hint is None:
                # Unknown skill - let the LLM handle it as free text.
                answer = self.ask(
                    f"[skill={skill_id}] {payload}", session_id=session_id
                )
                return {"text": answer, "skill_id": skill_id}

            # Find a matching discovered MCP tool.
            caps = self.spectrum_state.capabilities()
            match = next((n for n in caps if hint in n), None)
            if match is None:
                raise KeyError(
                    f"No MCP tool matching '{hint}' for skill '{skill_id}'"
                )

            result = self.mcp_client.call_tool(match, payload)
            artifact = {
                "skill_id": skill_id,
                "tool": match,
                "sender": sender,
                "structured": result.get("structured"),
                "text": result.get("text"),
                "state_version": self.spectrum_state.version,
            }
            return artifact
