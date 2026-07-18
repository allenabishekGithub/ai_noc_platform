# File: vector_store.py
"""
FAISS-backed vector store wrapper for RAG.

Wraps LangChain's `FAISS` vector store + a HuggingFace sentence-transformer
embedding model. The index is persisted to disk so it survives restarts.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document
from utils.event_timing import timed_event

LOGGER = logging.getLogger(__name__)


class FaissVectorStore:
    def __init__(self, index_path: str, embedding_model: str):
        self.index_path = index_path
        self.embedding_model_name = embedding_model

        LOGGER.info(f"Loading embedding model: {embedding_model}")
        self.embeddings = HuggingFaceEmbeddings(model_name=embedding_model)

        self.store: Optional[FAISS] = None
        self._load_or_init()

    def _load_or_init(self):
        with timed_event(LOGGER, "faiss_load_or_init", index_path=self.index_path):
            if os.path.isdir(self.index_path) and os.listdir(self.index_path):
                try:
                    LOGGER.info(f"Loading FAISS index from {self.index_path}")
                    self.store = FAISS.load_local(
                        self.index_path,
                        self.embeddings,
                        allow_dangerous_deserialization=True,
                    )
                    return
                except Exception as e:
                    LOGGER.warning(
                        f"Failed to load FAISS index ({e}); creating an empty one"
                    )

            # Initialize empty store with a single placeholder doc, then delete it.
            LOGGER.info("Initializing new FAISS index")
            seed = Document(page_content="__init__", metadata={"seed": True})
            self.store = FAISS.from_documents([seed], self.embeddings)

    # ------------------------------------------------------------------ #
    def add_texts(self, texts: List[str], metadatas: Optional[List[dict]] = None):
        if not texts:
            return 0
        self.store.add_texts(texts=texts, metadatas=metadatas)
        return len(texts)

    def add_documents(self, docs: List[Document]):
        if not docs:
            return 0
        with timed_event(LOGGER, "faiss_add_documents", documents=len(docs)):
            self.store.add_documents(docs)
            return len(docs)

    def has_metadata_value(self, key: str, value: str) -> bool:
        """Return True if any stored document has metadata[key] == value."""
        return self.has_metadata_values({key: value})

    def has_metadata_values(self, expected: Dict[str, Any]) -> bool:
        """Return True if any stored document has all expected metadata values."""
        if self.store is None:
            return False
        docstore = getattr(self.store, "docstore", None)
        docs = getattr(docstore, "_dict", {}) if docstore is not None else {}
        for doc in docs.values():
            metadata = getattr(doc, "metadata", {}) or {}
            if all(metadata.get(key) == value for key, value in expected.items()):
                return True
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Return chunk count and unique source names from the docstore."""
        if self.store is None:
            return {"chunks": 0, "sources": []}
        docstore = getattr(self.store, "docstore", None)
        docs = getattr(docstore, "_dict", {}).values() if docstore else []
        sources: set = set()
        total = 0
        for doc in docs:
            meta = getattr(doc, "metadata", {}) or {}
            if meta.get("seed"):
                continue
            total += 1
            src = meta.get("source")
            if src:
                sources.add(src)
        return {"chunks": total, "sources": sorted(sources)}

    def similarity_search(self, query: str, k: int = 4) -> List[Document]:
        if self.store is None:
            return []
        with timed_event(LOGGER, "faiss_similarity_search", k=k, query_len=len(query or "")):
            return self.store.similarity_search(query, k=k)

    def as_retriever(self, **kwargs):
        return self.store.as_retriever(**kwargs)

    def persist(self):
        if self.store is None:
            return
        with timed_event(LOGGER, "faiss_persist", index_path=self.index_path):
            os.makedirs(self.index_path, exist_ok=True)
            self.store.save_local(self.index_path)
            LOGGER.info(f"FAISS index saved to {self.index_path}")
