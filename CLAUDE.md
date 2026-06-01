# CLAUDE.md — AI Knowledge and Agent Framework (FastAPI + Claude SDK)

## Project overview

A reference implementation for building production-ready AI agents with RAG, short-term memory, user context injection, and SSE token streaming. Stack: **FastAPI** + **Anthropic Python SDK** + **ChromaDB** + **sentence-transformers**.

The source lives in `src/`. Each domain (e.g. `cheesecake`) is a self-contained FastAPI router following the same pattern.

---

## Commands

```bash
# Install dependencies (uv preferred)
uv sync

# Run dev server with hot reload
uv run uvicorn src.main:app --reload --port 8000

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=src

# Docker
make up       # build + run
make stop     # stop container
make logs     # tail container logs
```

---

## Project structure

```
src/
├── main.py                  # FastAPI app, lifespan, router registration
├── config.py                # pydantic-settings: ANTHROPIC_API_KEY, PORT, etc.
├── cheesecake/              # Reference domain (copy to add your own)
│   ├── router.py            # FastAPI router, SSE endpoint
│   ├── service.py           # RAG + memory + Claude calls
│   └── schemas.py           # Pydantic request/response models
└── knowledge/               # Shared RAG utilities
    ├── indexer.py           # load → chunk → embed → store
    └── retriever.py         # embed query → similarity search → top-k chunks
```

---

## Tech stack and key libraries

| Purpose | Library |
|---|---|
| Web framework | `fastapi` |
| LLM | `anthropic` (Claude SDK) |
| Embeddings | `sentence-transformers` or `voyageai` |
| Vector store | `chromadb` |
| Web scraping | `httpx` + `beautifulsoup4` |
| Data validation | `pydantic` v2 |
| Settings | `pydantic-settings` |
| ASGI server | `uvicorn` |
| Testing | `pytest` + `pytest-asyncio` + `httpx` (async test client) |

---

## Key patterns

### FastAPI router (domain module)

Each domain is a `APIRouter` with a prefix. Register it in `main.py`:

```python
# src/cheesecake/router.py
from fastapi import APIRouter
router = APIRouter(prefix="/cheesecake", tags=["cheesecake"])

# src/main.py
from cheesecake.router import router as cheesecake_router
app.include_router(cheesecake_router)
```

### Pydantic models (request/response)

Use Pydantic v2 with `model_config = ConfigDict(...)` instead of the v1 `class Config`. Keep request and response models in `schemas.py`.

```python
from pydantic import BaseModel, Field
from typing import Optional

class AskRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    user_preferences: Optional[UserPreferences] = None

class UserPreferences(BaseModel):
    user_name: Optional[str] = None
    industry: Optional[str] = None
    project_type: Optional[str] = None
```

### Anthropic SDK — streaming SSE

Use `AsyncAnthropic` for async FastAPI routes. Return a `StreamingResponse` with `media_type="text/event-stream"`.

```python
from anthropic import AsyncAnthropic
from fastapi.responses import StreamingResponse

client = AsyncAnthropic()

@router.post("/ask/stream")
async def ask_stream(body: AskRequest):
    async def event_generator():
        async with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield f"data: {text}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

### Anthropic SDK — non-streaming

```python
response = await client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system=SYSTEM_PROMPT,
    messages=messages,
)
answer = response.content[0].text
```

### Short-term memory (conversation history)

The Anthropic API is stateless — you pass the full `messages` list on every call. Maintain it in a dict keyed by `thread_id`:

```python
_conversations: dict[str, list[dict]] = {}

def get_messages(thread_id: str) -> list[dict]:
    return _conversations.setdefault(thread_id, [])

# Build thread_id from auth + request body
thread_id = f"{client_id}:{user_id}:{conversation_id}"

messages = get_messages(thread_id)
messages.append({"role": "user", "content": user_message})

# After Claude responds:
messages.append({"role": "assistant", "content": answer})
```

For production, replace the dict with Redis (using `redis-py` async client).

### Message trimming

Keep the last N messages to stay within Claude's context window:

```python
def trim_messages(messages: list[dict], max_messages: int = 20) -> list[dict]:
    if len(messages) > max_messages:
        return messages[-max_messages:]
    return messages
```

### RAG pipeline

**Index (once, or when content changes):**
```python
import httpx
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer
import chromadb

model = SentenceTransformer("all-MiniLM-L6-v2")
chroma = chromadb.Client()
collection = chroma.get_or_create_collection("cheesecake")

# Scrape
resp = await httpx.AsyncClient().get(SITE_URL)
soup = BeautifulSoup(resp.text, "html.parser")
text = soup.get_text(separator=" ", strip=True)

# Chunk
chunks = [text[i:i+1000] for i in range(0, len(text), 800)]  # 200-char overlap

# Embed + store
embeddings = model.encode(chunks).tolist()
collection.add(embeddings=embeddings, documents=chunks,
               ids=[f"chunk_{i}" for i in range(len(chunks))])
```

**Retrieve (per query):**
```python
query_embedding = model.encode([query]).tolist()
results = collection.query(query_embeddings=query_embedding, n_results=4)
context = "\n\n".join(results["documents"][0])
```

### User context injection

Inject user profile into the user message (not the system prompt) to keep the system prompt static and cache-friendly:

```python
def build_user_message(message: str, prefs: UserPreferences | None) -> str:
    if not prefs:
        return message
    lines = [f"[User context: name={prefs.user_name}, industry={prefs.industry}]"]
    return "\n".join(lines) + "\n\n" + message
```

### Settings

```python
# src/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    anthropic_api_key: str
    port: int = 8000
    client_url: str = "http://localhost:3000"

    model_config = {"env_file": ".env"}

settings = Settings()
```

---

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key (`sk-ant-...`) |
| `PORT` | No | HTTP port (default: 8000) |
| `CLIENT_URL` | No | CORS origin for the frontend |

---

## Claude model to use

Default to **`claude-sonnet-4-6`** for the best balance of speed and capability. Use `claude-haiku-4-5-20251001` for lower-cost or higher-throughput use cases. Use `claude-opus-4-8` for maximum reasoning.

---

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — full system design and multi-tenancy model
- [docs/rag-knowledge-flow.md](docs/rag-knowledge-flow.md) — RAG pipeline (index + retrieve + generate)
- [docs/short-term-memory-implementation.md](docs/short-term-memory-implementation.md) — conversation memory with `thread_id`
- [docs/DOCKER-AWS-EC2-REQUIREMENTS.md](docs/DOCKER-AWS-EC2-REQUIREMENTS.md) — Docker + EC2 deployment
