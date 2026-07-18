from __future__ import annotations

from langchain_ollama import ChatOllama

from .config import SETTINGS


def build_ollama_chat(model: str, temperature: float = 0.2) -> ChatOllama:
    return ChatOllama(
        model=model,
        base_url=SETTINGS.ollama_base_url,
        temperature=temperature,
    )
