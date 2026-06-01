# AI Agent Framework with RAG — FastAPI + Claude SDK

A reference implementation for building production-ready AI agents with **Retrieval-Augmented Generation (RAG)**, **short-term memory**, **user context injection**, and **SSE streaming** — built on FastAPI and the Anthropic Python SDK.

Use this project as a starting point when you need to ship an AI assistant grounded in a specific website or knowledge base.

---

## What this project demonstrates

| Concept | Where to look |
|---|---|
| RAG with web scraping | `cheesecake/service.py` → `build_index()` + `ask()` |
| Short-term conversation memory | In-memory dict keyed by `thread_id` per conversation |
| User context injection | `build_user_message()` enriches each prompt with user profile |
| SSE token streaming | `POST /cheesecake/ask/stream` via `StreamingResponse` |
| Modular, pluggable design | Each domain is a self-contained FastAPI router |

---

## Architecture

```
src/
├── main.py                  # FastAPI app setup and router registration
├── config.py                # Settings via pydantic-settings
├── cheesecake/              # AI agent for cheesecakelabs.com (reference module)
│   ├── service.py           # RAG agent: scrape → embed → retrieve → generate
│   ├── router.py            # POST /cheesecake/ask/stream (SSE)
│   └── schemas.py           # Pydantic request/response models
└── knowledge/               # Shared RAG utilities
    ├── indexer.py           # Document loading, chunking, embedding, storing
    └── retriever.py         # Similarity search against the vector store
```

### Key building blocks

**RAG pipeline** — `httpx` + `BeautifulSoup4` scrapes the target website, a simple sentence splitter chunks the content, `sentence-transformers` (or Voyage AI) vectorizes it, and `chromadb` stores it for similarity search.

**Short-term memory** — A plain Python `dict` keyed by `thread_id = f"{client_id}:{user_id}:{conversation_id}"` holds the Anthropic `messages` list for each conversation. Pass the same `conversationId` across requests to continue a conversation.

**Message trimming** — A `trim_messages()` helper keeps only the last N turns, preventing context overflow without losing earlier context.

**User context injection** — Before each Claude call, `build_user_message()` prepends structured user preferences (name, industry, project type, etc.) as plaintext inside the user message — no extra tokens in the system prompt.

**SSE streaming** — The router returns a `StreamingResponse` with `media_type="text/event-stream"`, consuming `client.messages.stream()` from the Anthropic SDK and forwarding each text delta as an SSE `data:` event.

---

## Adding your own agent

1. Create a new directory under `src/`:
   ```bash
   mkdir src/my_domain
   touch src/my_domain/{__init__,router,service,schemas}.py
   ```

2. Copy the pattern from `src/cheesecake/` and update:
   - `SITE_URL` — point to your knowledge source
   - `SYSTEM_PROMPT` — describe your agent's persona and constraints
   - `UserPreferences` schema — add the context fields relevant to your domain
   - `build_user_message()` — map your preferences to the prompt string

3. Register the router in `src/main.py`:
   ```python
   from my_domain.router import router as my_domain_router
   app.include_router(my_domain_router)
   ```

That's it — the RAG pipeline, memory, and streaming are inherited automatically.

---

## Tech stack

| Layer | Technology |
|---|---|
| **Web framework** | [FastAPI](https://fastapi.tiangolo.com/) |
| **LLM** | [Claude](https://www.anthropic.com/) via `anthropic` Python SDK |
| **Embeddings** | `sentence-transformers` (`all-MiniLM-L6-v2`) or Voyage AI |
| **Vector store** | [ChromaDB](https://www.trychroma.com/) (in-memory or persistent) |
| **Web scraping** | `httpx` + `BeautifulSoup4` |
| **Data validation** | [Pydantic v2](https://docs.pydantic.dev/) |
| **Streaming** | `StreamingResponse` + `text/event-stream` |
| **Short-term memory** | Python `dict` keyed by `thread_id` (Redis for production) |
| **ASGI server** | [Uvicorn](https://www.uvicorn.org/) |
| **Containerisation** | Docker (multi-stage) + Makefile |

---

## Documentation

The [`docs/`](docs/) directory contains detailed guides for each major concept:

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Full system design: multi-tenancy model, RAG design, memory isolation, persistence layer, API design, and evolution roadmap |
| [short-term-memory-implementation.md](docs/short-term-memory-implementation.md) | Step-by-step guide to adding conversation memory: `thread_id` design, receiving `conversationId` from the frontend, and trimming message history |
| [rag-knowledge-flow.md](docs/rag-knowledge-flow.md) | How the 2-step RAG pipeline works: index documents once, retrieve relevant chunks at query time, and ground Claude's responses in that context |
| [DOCKER-AWS-EC2-REQUIREMENTS.md](docs/DOCKER-AWS-EC2-REQUIREMENTS.md) | Containerisation with Docker, Makefile automation, and deployment to AWS EC2 |

Start with [ARCHITECTURE.md](docs/ARCHITECTURE.md) for the big picture, then the concept guides for implementation details.

---

## Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`
- Anthropic API key

### Install and run

```bash
# Using uv (recommended)
uv sync
uv run uvicorn src.main:app --reload

# Using pip
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn src.main:app --reload
```

### Environment variables

Create a `.env` file at the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...
# Optional
PORT=8000
CLIENT_URL=http://localhost:3000
```

### Docker

```bash
# Copy the example and fill in your values, then:
make up
```

---

## API reference

### `POST /cheesecake/ask/stream`

Stream a response about Cheesecake Labs as Server-Sent Events.

**Request body:**
```json
{
  "message": "What services does Cheesecake Labs offer?",
  "conversationId": "optional-uuid-to-continue-a-conversation",
  "userPreferences": {
    "userName": "Alice",
    "industry": "fintech",
    "projectType": "mobile app",
    "companySize": "startup",
    "interests": ["React Native", "AI"]
  }
}
```

**Response:** SSE stream where each event's `data` field contains a text delta token.

**Example consumption (JavaScript):**
```js
const es = new EventSource('/cheesecake/ask/stream', { /* POST via fetch + ReadableStream */ });
```

---

## Tests

```bash
# Unit tests
uv run pytest

# With coverage
uv run pytest --cov=src
```
