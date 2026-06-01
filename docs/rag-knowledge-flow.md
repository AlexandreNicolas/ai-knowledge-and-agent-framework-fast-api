# Simple 2-Step RAG Flow with FastAPI + Claude SDK

This document describes how to build a **Retrieval-Augmented Generation (RAG)** flow for a knowledge base: index your business documents once, then answer questions over them with Claude. The architecture keeps two clear steps: **retrieve** (find relevant chunks) then **generate** (answer grounded in that context).

---

## Overview

**RAG** reduces hallucinations and keeps answers grounded in your data by:

1. **Index time (run once, or when docs change):** Scrape/load documents → split into chunks → embed → store in ChromaDB.
2. **Query time:** Take the user question → embed it → retrieve top-k relevant chunks → pass context + question to Claude → return the answer.

The **2-step flow** is: **retrieve → generate**. No reranking or multi-hop in this minimal version.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  INDEX (once or on update)                                       │
│  Scrape → Split → Embed → Store                                  │
│  httpx/BeautifulSoup → chunk() → SentenceTransformer → ChromaDB │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  QUERY TIME                                                      │
│  Question → Embed → Retrieve → Context + Question → Claude      │
│  SentenceTransformer → ChromaDB.query() → messages → stream     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Python abstractions

| Step | Library / Tool | Role |
|------|---------------|------|
| Scrape | `httpx` + `beautifulsoup4` | Load text from URLs |
| Split | Plain Python (`textwrap` or custom) | Chunk text with overlap |
| Embed | `sentence-transformers` or Voyage AI | Turn text into vectors |
| Store | `chromadb` | Persist and search vectors |
| Retrieve | `chromadb` `.query()` | Top-k similar chunks for a query |
| Generate | `anthropic` SDK | Claude call with context in system prompt |
| Stream | `StreamingResponse` (FastAPI) | SSE token stream to client |

For **prototyping** use `chromadb.Client()` (in-memory); for **persistence** use `chromadb.PersistentClient(path="./chroma_db")`.

---

## Dependencies

```bash
# Core
pip install anthropic fastapi uvicorn pydantic-settings

# RAG
pip install chromadb sentence-transformers httpx beautifulsoup4

# Optional: Voyage AI embeddings (higher quality, requires VOYAGE_API_KEY)
pip install voyageai
```

Or with `uv`:

```bash
uv add anthropic fastapi uvicorn pydantic-settings chromadb sentence-transformers httpx beautifulsoup4
```

Set `ANTHROPIC_API_KEY` in your `.env` file.

---

## Minimal 2-Step RAG (standalone)

### 1. Index: scrape → split → embed → store

```python
import httpx
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
import chromadb

SITE_URL = "https://cheesecakelabs.com"
COLLECTION_NAME = "cheesecake"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

embed_model = SentenceTransformer("all-MiniLM-L6-v2")
chroma = chromadb.Client()  # in-memory; use PersistentClient for disk
collection = chroma.get_or_create_collection(COLLECTION_NAME)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start += size - overlap
    return chunks


async def build_index() -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(SITE_URL, follow_redirects=True)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove script/style noise
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)

    chunks = chunk_text(text)
    embeddings = embed_model.encode(chunks).tolist()

    # Delete existing documents for this client before re-indexing
    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    collection.add(
        embeddings=embeddings,
        documents=chunks,
        ids=[f"chunk_{i}" for i in range(len(chunks))],
        metadatas=[{"source": SITE_URL, "chunk_index": i} for i in range(len(chunks))],
    )
    print(f"Indexed {len(chunks)} chunks from {SITE_URL}")
```

### 2. Query: retrieve → generate (non-streaming)

```python
from anthropic import AsyncAnthropic

anthropic_client = AsyncAnthropic()
TOP_K = 4

SYSTEM_PROMPT = """You are a helpful assistant for Cheesecake Labs.
Answer questions based strictly on the provided context.
If the context does not contain the answer, say so clearly."""


def retrieve(query: str, top_k: int = TOP_K) -> str:
    query_embedding = embed_model.encode([query]).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=top_k)
    return "\n\n".join(results["documents"][0])


async def ask(question: str, messages: list[dict]) -> str:
    context = retrieve(question)
    system_with_context = f"{SYSTEM_PROMPT}\n\n## Context\n{context}"

    messages_with_question = messages + [{"role": "user", "content": question}]

    response = await anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system_with_context,
        messages=messages_with_question,
    )
    return response.content[0].text
```

### 3. Query: retrieve → generate (SSE streaming with FastAPI)

```python
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/cheesecake", tags=["cheesecake"])


class AskRequest(BaseModel):
    message: str
    conversation_id: str | None = None


@router.post("/ask/stream")
async def ask_stream(body: AskRequest):
    context = retrieve(body.message)
    system_with_context = f"{SYSTEM_PROMPT}\n\n## Context\n{context}"

    # In a real app: load messages from memory store by thread_id
    messages = [{"role": "user", "content": body.message}]

    async def event_generator():
        async with anthropic_client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_with_context,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield f"data: {text}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

---

## FastAPI: Knowledge router

The same flow is exposed as a **FastAPI router** named **knowledge** (domain-oriented naming).

### Endpoints

- **POST /knowledge/index** — Index business data (triggers scrape → chunk → embed → store).
- **POST /knowledge/query** — Ask a question; backend retrieves chunks, calls Claude, returns the answer.

### Design choices

- **Thin router:** Only validates input via Pydantic and delegates to the knowledge service.
- **Service:** Owns the RAG pipeline (ChromaDB retriever + Anthropic SDK). ChromaDB collection and embed model are instantiated once at startup via FastAPI `lifespan`.
- **Pydantic schemas:** Request/response use Pydantic models; internal objects are never exposed.
- **One LLM call per query:** Retrieve then generate, no extra reranking step.

### File layout

```
src/knowledge/
├── __init__.py
├── router.py            # FastAPI routes
├── service.py           # RAG pipeline (indexer + retriever + Claude call)
├── indexer.py           # load → chunk → embed → store
├── retriever.py         # embed query → chromadb.query() → top-k chunks
└── schemas.py           # IndexRequest, QueryRequest, QueryResponse
```

### Usage example

```bash
# Index documents
curl -X POST http://localhost:8000/knowledge/index \
  -H "Content-Type: application/json" \
  -d '{"url": "https://cheesecakelabs.com"}'

# Query
curl -X POST http://localhost:8000/knowledge/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What services does Cheesecake Labs offer?"}'
```

---

## Embeddings options

### Option A: sentence-transformers (local, no extra API key)

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")

def embed(texts: list[str]) -> list[list[float]]:
    return model.encode(texts).tolist()
```

Fast, free, runs locally. Quality is good for most use cases.

### Option B: Voyage AI (Anthropic's recommended provider)

```python
import voyageai

vo = voyageai.Client()  # reads VOYAGE_API_KEY from env

def embed(texts: list[str]) -> list[list[float]]:
    result = vo.embed(texts, model="voyage-3")
    return result.embeddings
```

Higher quality retrieval; requires a `VOYAGE_API_KEY`. Recommended for production.

Both options produce a `list[list[float]]` that ChromaDB accepts directly.

---

## Persistence (beyond prototyping)

- **In-memory ChromaDB** (`chromadb.Client()`): lost on restart; good for local/dev.
- **Persistent ChromaDB** (`chromadb.PersistentClient(path="./chroma_db")`): survives restarts; good for single-server prod.
- **pgvector**: store embeddings in Postgres alongside your relational data; use `pgvector` + `asyncpg`.
- **Pinecone / Weaviate / Qdrant**: cloud-native vector DBs; same retrieve-then-generate flow, just swap the client.

Keep the **retrieve → generate** flow unchanged; only the **vector store** implementation changes. Abstract behind a `Retriever` protocol:

```python
from typing import Protocol

class Retriever(Protocol):
    def retrieve(self, query: str, client_id: str, top_k: int = 4) -> str:
        ...
```

---

## Multi-tenant RAG

To isolate documents between clients, use either:

**Option A: Separate collection per client (simpler)**
```python
collection_name = f"knowledge_{client_id}"
collection = chroma.get_or_create_collection(collection_name)
```

**Option B: Shared collection with metadata filter (scales to many clients)**
```python
# At index time
collection.add(
    embeddings=embeddings,
    documents=chunks,
    ids=[f"{client_id}_{i}" for i in range(len(chunks))],
    metadatas=[{"client_id": client_id, "source": url} for _ in chunks],
)

# At query time
results = collection.query(
    query_embeddings=query_embedding,
    n_results=top_k,
    where={"client_id": client_id},  # metadata filter
)
```

---

## Summary

| Phase | Steps | Tools |
|-------|-------|-------|
| **Index** | Scrape → Split → Embed → Store | `httpx`, `BeautifulSoup4`, `SentenceTransformer`, `chromadb` |
| **Query** | Retrieve → Build prompt → Generate | `chromadb`, `AsyncAnthropic`, `StreamingResponse` |

Start with this 2-step RAG (retrieve then generate), then add persistence (persistent ChromaDB or pgvector), Voyage AI embeddings, or multi-hop retrieval as needed.
