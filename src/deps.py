from anthropic import AsyncAnthropic
from fastapi import Request
from sentence_transformers import SentenceTransformer

from src.memory.store import MemoryStore
from src.knowledge.vector_store import VectorStore


def get_memory_store(request: Request) -> MemoryStore:
    return request.app.state.memory_store


def get_vector_store(request: Request) -> VectorStore:
    return request.app.state.vector_store


def get_embed_model(request: Request) -> SentenceTransformer:
    return request.app.state.embed_model


def get_anthropic_client(request: Request) -> AsyncAnthropic:
    return request.app.state.anthropic_client
