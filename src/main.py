import logging
from contextlib import asynccontextmanager

from anthropic import AsyncAnthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentence_transformers import SentenceTransformer

from src.cheesecake.router import router as cheesecake_router
from src.config import settings
from src.knowledge.indexer import index_url
from src.knowledge.vector_store import VectorStore
from src.memory.in_memory import InMemoryStore

log = logging.getLogger(__name__)

_CHEESECAKE_URL = "https://cheesecakelabs.com"
_CHEESECAKE_CLIENT_ID = "cheesecake-labs"


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Loading embedding model...")
    app.state.embed_model = SentenceTransformer("all-MiniLM-L6-v2")

    log.info("Initialising vector store...")
    app.state.vector_store = VectorStore()

    log.info("Indexing knowledge base from %s ...", _CHEESECAKE_URL)
    try:
        n = await index_url(
            url=_CHEESECAKE_URL,
            store=app.state.vector_store,
            embed_model=app.state.embed_model,
            client_id=_CHEESECAKE_CLIENT_ID,
        )
        log.info("Indexed %d chunks.", n)
    except Exception as exc:
        log.warning("Indexing failed (%s). RAG will return empty context.", exc)

    app.state.memory_store = InMemoryStore()
    app.state.anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    yield

    log.info("Shutting down.")


app = FastAPI(
    title="AI Knowledge and Agent Framework",
    description="RAG-powered conversational agent with short-term memory and SSE streaming.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.client_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cheesecake_router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def ready() -> dict:
    count = app.state.vector_store.count() if hasattr(app.state, "vector_store") else 0
    return {"status": "ok", "indexed_chunks": count}
