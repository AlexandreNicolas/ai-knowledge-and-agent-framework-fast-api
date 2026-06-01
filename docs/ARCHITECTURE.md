# AI Knowledge and Agent Framework — System Architecture (v1 → v2)

**AI Knowledge and Agent Framework** is a reusable conversational AI engine that provides:

- **RAG** — client-level knowledge retrieval
- **Short-term memory** — per-conversation context
- **Per-user context** — profile and preferences in prompt, not in vectors
- **Client data integration** — e.g. Cheesecake Labs website

It is **not** the end product. It is the **intelligence layer** that multiple products can plug into.

---

## Table of Contents

1. [Vision & Principles](#vision--principles)
2. [Phase 1 — One Client, Multiple Users (NOW)](#phase-1--one-client-multiple-users-now)
3. [Core Identity Model](#core-identity-model)
4. [Short-Term Memory Design](#short-term-memory-design)
5. [RAG Design](#rag-design)
6. [Full Request Flow](#full-request-flow)
7. [Persistence Layer](#persistence-layer)
8. [API Design](#api-design)
9. [Phase 2 — Multiple Clients (Future)](#phase-2--multiple-clients-future)
10. [Security & Multi-Tenancy](#security--multi-tenancy)
11. [Technology Stack & Replaceability](#technology-stack--replaceability)
12. [Observability & Operations](#observability--operations)
13. [Decisions Summary](#decisions-summary)
14. [Current Implementation vs. Target](#current-implementation-vs-target)

---

## Vision & Principles

| Principle | Description |
|-----------|-------------|
| **Multi-tenant from day one** | All data and compute are scoped by `client_id`; adding clients does not change core logic. |
| **RAG = client knowledge** | Embeddings and retrieval are per client; users do not have personal RAGs. |
| **Memory = conversation-scoped** | One short-term memory per conversation; never mixed with RAG. |
| **User profile in prompt** | Preferences and profile affect filters and prompt context, not the vector store. |
| **Replaceable infrastructure** | LLM, embeddings, vector store, and memory store are swappable behind simple interfaces. |

---

## Phase 1 — One Client, Multiple Users (NOW)

### Actors

| Actor | Example | Role |
|-------|---------|------|
| **Client** | Cheesecake Website | Tenant; owns RAG corpus and product branding. |
| **Users** | End users | Consume the client's product; have conversations and optional profile. |

Even with one client, the system is designed as **multi-tenant** from the start.

### High-Level Architecture

```
                    Frontend (Client App)
                              |
                              v
                    API Gateway / BFF
                              |
                              v
                    AI Knowledge and Agent Framework (FastAPI)
                     ├─ Auth / Tenant Resolver
                     ├─ Conversation Service
                     │   ├─ Short-term Memory (dict / Redis)
                     │   └─ Conversation State
                     ├─ RAG Service
                     │   ├─ Retriever (ChromaDB)
                     │   ├─ Vector Store
                     │   └─ Metadata Filters
                     ├─ LLM Orchestrator (Anthropic SDK)
                     └─ Persistence Layer
```

**Responsibilities**

- **Auth / Tenant Resolver** — Resolves `user_id` and `client_id` from token or headers; enforces tenant isolation.
- **Conversation Service** — Manages conversation lifecycle; loads/saves short-term memory by `thread_id`.
- **RAG Service** — Indexing (per client) and retrieval with metadata filters; no user-specific embeddings.
- **LLM Orchestrator** — Builds final prompt (system + user profile + memory + RAG context), calls Claude, streams response.
- **Persistence Layer** — Relational DB for tenants/conversations; vector store for RAG; memory store (Redis/Postgres) for threads.

---

## Core Identity Model

This model avoids most future scaling and isolation issues.

```
Client {
  id: "cheesecake-labs"
}

User {
  id: "user-123",
  client_id: "cheesecake-labs"
}

Conversation {
  id: "chat-456",
  user_id: "user-123",
  client_id: "cheesecake-labs"
}
```

- Every **User** belongs to exactly one **Client**.
- Every **Conversation** belongs to one **User** and one **Client**.
- All retrieval, memory, and LLM context are keyed by these identities.

---

## Short-Term Memory Design

### Key Rule

**One memory per conversation.** Memory is isolated by a single composite key: `thread_id`.

```python
thread_id = f"{client_id}:{user_id}:{conversation_id}"
# Example: "cheesecake-labs:user-123:chat-456"
```

The Anthropic SDK is **stateless** — it does not retain conversation history between calls. The application is responsible for maintaining the `messages` list and passing it on every request.

### Properties

| Property | Description |
|----------|-------------|
| **Scope** | One thread per conversation; no cross-conversation leakage. |
| **Storage** | Python `dict` (dev/single-instance) or Redis (preferred for production). |
| **TTL** | Optional (e.g. 24h or 7 days); configurable per client when using Redis. |
| **Content** | `list[dict]` of `{"role": "user"|"assistant", "content": "..."}` messages. |

### Critical Constraint

⚠️ **Short-term memory never goes into RAG.**
Memory is used only for conversation continuity and prompt context. It is not embedded or indexed for retrieval.

### Implementation Notes

- Use a **memory store interface** (`get_messages(thread_id)`, `save_messages(thread_id, messages)`) so you can swap between a plain dict and Redis without changing the service layer.
- Trim the message list before each call to prevent context overflow. See [short-term-memory-implementation.md](./short-term-memory-implementation.md).

---

## RAG Design

### What RAG Is in This System

- **Client-level knowledge** — Shared across all users of that client.
- **Static or semi-static** — Re-indexed on content updates, not per message.
- **Example (Cheesecake Labs):** Company info, services, case studies.

### Important Boundaries

| Do | Don't |
|----|--------|
| One RAG corpus per client (or per client + optional namespace). | Per-user RAGs. |
| User attributes as **metadata filters** (e.g. language, segment). | Put user profile text into the vector store. |
| User profile in **prompt context** (e.g. "User is in fintech"). | Embed user preferences. |

### RAG Pipeline (High Level)

1. **Index (offline or on demand)**
   Scrape/load documents → split into chunks → embed (sentence-transformers or Voyage AI) → store in ChromaDB with `client_id` metadata.

2. **Retrieve (at query time)**
   Embed query → similarity search filtered by `client_id` → return top-k chunks.

3. **Generate**
   Inject retrieved context + user context + short-term memory into Claude messages → stream response.

See [rag-knowledge-flow.md](./rag-knowledge-flow.md) for a complete Python implementation.

### Vector Store Isolation

- All documents are stored with a `client_id` metadata field.
- Every query filters by `client_id`, ensuring tenants cannot see each other's data.
- **Optional:** Use a separate ChromaDB collection per client (`collection_name = f"knowledge_{client_id}"`).

---

## Full Request Flow

```
1. User sends a message
2. FastAPI resolves client_id + user_id (from auth middleware, not body)
3. Load short-term memory (messages list) using thread_id
4. Run RAG retrieval (query + client_id filter)
5. Build final messages list:
   - system prompt (persona + RAG context)
   - user profile context (injected into user message)
   - short-term memory (previous turns)
   - current user message
6. Call Claude via Anthropic SDK (streaming or non-streaming)
7. Persist updated messages list (append new turn)
8. Return response (SSE stream or JSON)
```

### Flow Diagram

```
[Client App] --> POST /chat (message, conversation_id)
       |
       v
[Auth Middleware] --> client_id, user_id
       |
       v
[Conversation Service] --> thread_id = client_id:user_id:conversation_id
       |                       |
       |                       v
       |                 [Memory Store] --> load messages list
       |
       v
[RAG Service] --> retrieve(client_id, query, top_k=4)
       |                       |
       |                       v
       |                 [ChromaDB] --> top-k chunks
       |
       v
[LLM Orchestrator] --> build messages(system, profile, memory, chunks, message)
       |                       |
       |                       v
       |                 [Claude API] --> stream response
       |
       v
[Memory Store] --> save updated messages list
       |
       v
[StreamingResponse] --> client (text/event-stream)
```

---

## Persistence Layer

### Minimum Production Setup

| Store | Purpose | Notes |
|-------|---------|------|
| **Relational DB (Postgres)** | `clients`, `users`, `conversations`, optional `messages` | Source of truth for identity and conversation metadata. |
| **Vector store (ChromaDB / pgvector)** | Embeddings per client; filtered by `client_id` | ChromaDB for dev/small scale; pgvector or Pinecone for production. |
| **Memory store** | Short-term memory per `thread_id` | Redis (preferred, async with `redis.asyncio`) or Postgres; optional TTL. |

### Optional Tables

- **messages** — Long-term history for replay, analytics, or training.
- **memory_summaries** — Compressed conversation summaries for very long threads.

---

## API Design

### Primary Endpoint: Chat

**POST /chat/stream**

**Request:**

```json
{
  "conversation_id": "chat-456",
  "message": "Which service fits my company?",
  "user_preferences": {
    "user_name": "Alice",
    "industry": "fintech"
  }
}
```

**Best practice:** Resolve `client_id` and `user_id` from **authentication** (JWT, API key), not from the body. Accept only `conversation_id`, `message`, and `user_preferences` in the body.

**Response:** SSE stream (`text/event-stream`) where each `data:` event contains a text delta.

### Additional Endpoints (Suggested)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/chat` | Single message, sync JSON response. |
| POST | `/chat/stream` | Single message, SSE stream. |
| GET  | `/conversations/{id}` | Get conversation metadata and recent messages. |
| POST | `/conversations` | Create conversation, returns `conversation_id`. |
| GET  | `/health` | Liveness check. |
| GET  | `/ready` | Readiness check (DB, Redis, vector store). |

### API Principles

- **Pydantic models** for all inputs and outputs; validation is automatic via FastAPI.
- **Structured errors** (code, message, request_id) via a global exception handler.
- **Dependency injection** via FastAPI `Depends()` for auth, settings, and services.

---

## Phase 2 — Multiple Clients (Future)

When you onboard more clients, **core logic does not change**.

```
AI Knowledge and Agent Framework
 ├─ Client A RAG (collection: knowledge_client_a)
 ├─ Client B RAG (collection: knowledge_client_b)
 ├─ Client C RAG (collection: knowledge_client_c)
```

Isolation is guaranteed by:

- `client_id` in auth middleware and in all DB/vector queries.
- `thread_id` for memory (`client_id:user_id:conversation_id`).
- Separate ChromaDB collections or metadata filters per client.

New clients require configuration (and possibly separate RAG indexing pipelines), not new application code paths.

---

## Security & Multi-Tenancy

| Concern | Approach |
|--------|----------|
| **Tenant resolution** | Derive `client_id` and `user_id` from verified auth (JWT, API key); never trust client-supplied IDs for authorization. |
| **Data isolation** | Every DB and vector query includes `client_id`; consider Row Level Security (RLS) in Postgres for extra safety. |
| **Secrets** | `ANTHROPIC_API_KEY` and DB credentials in env or AWS Secrets Manager; per-client keys only if you need strict cost/access isolation. |
| **Input validation** | Pydantic validates all request bodies automatically; limit `message` length explicitly (e.g. `max_length=4000`). |
| **Rate limiting** | Use `slowapi` or an API gateway for per-user/client rate limits. |
| **Audit** | Log `client_id`, `user_id`, `conversation_id` for all LLM calls; never log full message content in production. |

---

## Technology Stack & Replaceability

| Component | Default | How to replace |
|-----------|---------|----------------|
| **LLM** | Claude (`claude-sonnet-4-6`) | Change `model` param; swap `AsyncAnthropic` for OpenAI via `openai` SDK if needed. |
| **Embeddings** | `sentence-transformers` (`all-MiniLM-L6-v2`) | Drop-in replace with Voyage AI (`voyageai`) or OpenAI embeddings; same interface. |
| **Vector store** | ChromaDB (in-memory) | Swap to `chromadb.PersistentClient(path)` for disk, or `pgvector` / Pinecone for cloud. |
| **Memory store** | Python `dict` | Replace `get_messages`/`save_messages` impl with `redis.asyncio` keyed by `thread_id`. |
| **Web framework** | FastAPI | Domain routers depend on Pydantic schemas and service classes, not FastAPI internals. |
| **Scraping** | `httpx` + `BeautifulSoup4` | Swap for `playwright` (JS-rendered pages) or direct file/PDF loaders. |

---

## Observability & Operations

| Area | Recommendations |
|------|------------------|
| **Logging** | Structured JSON logs; include `client_id`, `user_id`, `conversation_id`, `request_id`. Use `structlog` or Python's `logging` with a JSON formatter. |
| **Metrics** | Latency (p50, p99) per endpoint; token usage per request/client (from `response.usage`); RAG retrieval latency and chunk count. |
| **Tracing** | Trace ID across auth → memory load → RAG → Claude → memory save. Use `opentelemetry-sdk` with FastAPI middleware. |
| **Errors** | FastAPI `@app.exception_handler` for structured error responses; map to stable error codes. |
| **Health** | `GET /health` (liveness); `GET /ready` (DB + Redis + ChromaDB reachability). |

---

## Decisions Summary

| Decision | Rationale |
|----------|------------|
| **FastAPI over Django/Flask** | Async-native, Pydantic built-in, automatic OpenAPI docs, SSE streaming with `StreamingResponse`. |
| **Anthropic SDK (not LangChain)** | Direct control over prompt structure, simpler dependency graph, easier to reason about token usage and caching. |
| **Plain dict for memory (dev)** | The Anthropic API takes `messages` as a plain list — no ORM or framework needed. Replace with Redis at scale. |
| **ChromaDB for RAG** | Zero-config Python-native vector store; swap to persistent backend without changing retrieval logic. |
| **Short-term memory per conversation** | Clear semantics; `thread_id` gives a simple, scalable key. |
| **RAG decoupled from users** | One corpus per client; user only affects filters and prompt, not embeddings. |
| **Multi-tenant from day one** | Avoids costly refactors later; same code path for all clients. |
| **User profile in prompt, not in RAG** | Keeps retrieval stable and fair; personalisation via context, not vector pollution. |
| **Auth resolves tenant** | Security and consistency; body carries only conversation and message. |

---

## Current Implementation vs. Target

| Aspect | Current | Target |
|--------|---------|--------|
| **API** | `POST /cheesecake/ask/stream` with `message` in body | Unified `POST /chat/stream` with auth-derived `client_id`/`user_id`, body: `conversation_id`, `message` |
| **Auth / Tenant** | Not implemented | FastAPI `Depends()` middleware + tenant resolver; `client_id`/`user_id` from JWT |
| **Identity** | No Client/User/Conversation entities | Postgres (SQLAlchemy async) for clients, users, conversations |
| **Short-term memory** | In-memory `dict`, not keyed by tenant | Redis keyed by `thread_id` with TTL |
| **RAG** | In-memory ChromaDB, single client | Persistent ChromaDB or pgvector with `client_id` filter |
| **Streaming** | `StreamingResponse` per router | Keep SSE; optional centralised streaming in a shared chat pipeline |
| **Persistence** | No DB | Postgres (identity); persistent vector store; Redis (memory) |

**Suggested evolution**

1. Add **auth middleware** (e.g. JWT with `python-jose` or `authlib`); inject `client_id`/`user_id` via `Depends()`.
2. Add **SQLAlchemy async** models for `Client`, `User`, `Conversation`; use `asyncpg` driver.
3. Replace in-memory dict with **Redis** (`redis.asyncio`) keyed by `thread_id` with configurable TTL.
4. Replace in-memory ChromaDB with **persistent ChromaDB** (`chromadb.PersistentClient`) or **pgvector**.
5. Unify entrypoint to **`POST /chat`** and **`POST /chat/stream`** with auth-derived tenant.
