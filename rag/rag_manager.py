# File: rag_manager.py
"""
RAG manager - handles document chunking, ingestion into the FAISS vector
store, and retrieval helpers used by the LangChain agent.
"""
import logging
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.vector_store import FaissVectorStore
from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)


class RAGManager:
    def __init__(
        self,
        vector_store: FaissVectorStore,
        chunk_size: int = 800,
        chunk_overlap: int = 100,
    ):
        self.vector_store = vector_store
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def add_documents(self, docs: List[dict]) -> int:
        """
        Add raw documents to the store. Each doc is a dict with keys:
          - text: str (required)
          - metadata: dict (optional)
        Returns number of chunks added.
        """
        with timed_event(LOGGER, "rag_add_documents", documents=len(docs)):
            chunks: List[Document] = []
            for d in docs:
                text = d.get("text", "")
                metadata = d.get("metadata", {}) or {}
                if not text:
                    continue
                for chunk in self.splitter.split_text(text):
                    chunks.append(Document(page_content=chunk, metadata=metadata))

            added = self.vector_store.add_documents(chunks)
            self.vector_store.persist()
            LOGGER.info(f"Ingested {added} chunks into FAISS store")
            return added

    def retrieve(self, query: str, k: int = 4) -> List[Document]:
        with timed_event(LOGGER, "rag_retrieve", k=k, query_len=len(query or "")):
            return self.vector_store.similarity_search(query, k=k)

    def has_source(self, source: str) -> bool:
        """Return True if a document with the given metadata source is present."""
        return self.vector_store.has_metadata_value("source", source)

    def has_source_hash(self, source: str, content_sha256: str) -> bool:
        """Return True if this exact source version is already present."""
        return self.vector_store.has_metadata_values({
            "source": source,
            "content_sha256": content_sha256,
        })

    def format_context(self, docs: List[Document]) -> str:
        if not docs:
            return ""
        parts = []
        for i, d in enumerate(docs, 1):
            src = d.metadata.get("source", "unknown")
            parts.append(f"[{i}] (source: {src})\n{d.page_content}")
        return "\n\n".join(parts)
