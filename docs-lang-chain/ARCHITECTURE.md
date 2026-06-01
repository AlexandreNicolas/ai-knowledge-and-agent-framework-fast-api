# Ai Knowledge and Agent Framework — System Architecture (v1 → v2)

**Ai Knowledge and Agent Framework** is a reusable conversational AI engine that provides:

- **RAG** — client-level knowledge retrieval
- **Short-term memory** — per-conversation context
- **Per-user context** — profile and preferences in prompt, not in vectors
- **Client data integration** — e.g. Cheesecake Website

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
| **Multi-tenant from day one** | All data and compute are scoped by `clientId`; adding clients does not change core logic. |
| **RAG = client knowledge** | Embeddings and retrieval are per client; users do not have personal RAGs. |
| **Memory = conversation-scoped** | One short-term memory per conversation; never mixed with RAG. |
| **User profile in prompt** | Preferences and profile affect filters and prompt context, not the vector store. |
| **Replaceable infrastructure** | LLM, embeddings, vector store, and memory store are swappable behind interfaces. |

---

## Phase 1 — One Client, Multiple Users (NOW)

### Actors

| Actor | Example | Role |
|-------|---------|------|
| **Client** | Cheesecake Website | Tenant; owns RAG corpus and product branding. |
| **Users** | End students | Consume the client’s product; have conversations and optional profile. |

Even with one client, the system is designed as **multi-tenant** from the start.

### High-Level Architecture

```
                    Frontend (Client App)
                              |
                              v
                    API Gateway / BFF
                              |
                              v
                    Ai Knowledge and Agent Framework (AI Backend)
                     ├─ Auth / Tenant Resolver
                     ├─ Conversation Service
                     │   ├─ Short-term Memory
                     │   └─ Conversation State
                     ├─ RAG Service
                     │   ├─ Retriever
                     │   ├─ Vector Store
                     │   └─ Metadata Filters
                     ├─ LLM Orchestrator
                     └─ Persistence Layer
```

**Responsibilities**

- **Auth / Tenant Resolver** — Resolves `userId` and `clientId` from token or headers; enforces tenant isolation.
- **Conversation Service** — Manages conversation lifecycle; loads/saves short-term memory by `thread_id`.
- **RAG Service** — Indexing (per client) and retrieval with metadata filters; no user-specific embeddings.
- **LLM Orchestrator** — Builds final prompt (system + user profile + memory + RAG context), calls LLM, optional streaming.
- **Persistence Layer** — Relational DB for tenants/conversations; vector store for RAG; memory store (e.g. Redis/Postgres) for threads.

---

## Core Identity Model

This model avoids most future scaling and isolation issues.

```
Client {
  id: "cheesecake-labs"
}

User {
  id: "user-123",
  clientId: "cheesecake-labs"
}

Conversation {
  id: "chat-456",
  userId: "user-123",
  clientId: "cheesecake-labs"
}
```

- Every **User** belongs to exactly one **Client**.
- Every **Conversation** belongs to one **User** and one **Client**.
- All retrieval, memory, and LLM context are keyed by these identities.

---

## Short-Term Memory Design

### Key Rule

**One memory per conversation.** Memory is isolated by a single composite key: `thread_id`.

```
thread_id = `${clientId}:${userId}:${conversationId}`
```

**Example:** `cheesecake-labs:user-123:chat-456`

### Properties

| Property | Description |
|----------|-------------|
| **Scope** | One thread per conversation; no cross-conversation leakage. |
| **Storage** | Redis (preferred for speed and TTL) or Postgres. |
| **TTL** | Optional (e.g. 24h or 7 days); configurable per client. |
| **Content** | Recent turns, summaries, or structured state (e.g. LangGraph checkpointer state). |

### Critical Constraint

⚠️ **Short-term memory never goes into RAG.**  
Memory is used only for conversation continuity and prompt context. It is not embedded or indexed for retrieval.

### Implementation Notes

- Use a **memory store interface** (e.g. `MemoryStore`) so you can switch between Redis and Postgres.
- When using LangGraph, key the checkpointer by `thread_id` so each conversation has its own graph state.

---

## RAG Design

### What RAG Is in This System

- **Client-level knowledge** — Shared across all users of that client.
- **Static or semi-static** — Re-indexed on content updates, not per message.
- **Example (Cheesecake Website):** Universities, courses, reviews.

### Important Boundaries

| Do | Don’t |
|----|--------|
| One RAG corpus per client (or per client + optional namespace). | Per-user RAGs. |
| User attributes as **metadata filters** (e.g. language, segment). | Put user profile text into the vector store. |
| User profile in **prompt context** (e.g. “Student prefers X”). | Embed user preferences. |

### RAG Pipeline (High Level)

1. **Index (offline or on demand)**  
   Load documents → split → embed → store in vector store with `clientId` (and optional metadata).

2. **Retrieve (at query time)**  
   Embed query → similarity search with **metadata filters** (e.g. `clientId`, language) → return top-k chunks.

3. **Generate**  
   Inject retrieved context + user context + short-term memory into prompt → LLM → response.

See [Simple 2-Step RAG Flow](./rag-knowledge-flow.md) for a minimal retrieve-then-generate pattern and LangChain mapping.

### Vector Store Isolation

- **Namespace or metadata:** All vectors carry `clientId`; every query filters by `clientId`.
- **Optional:** Separate index or collection per client (e.g. Pinecone namespaces, pgvector schemas).

---

## Full Request Flow

This flow remains the same when you add more clients.

```
1. User sends a message
2. Ai Knowledge and Agent Framework resolves clientId + userId (from auth, not body)
3. Load short-term memory using thread_id
4. Apply user-based filters (e.g. for RAG metadata)
5. Run RAG retrieval (query + filters, client-scoped)
6. Build final prompt:
   - system prompt
   - user profile context
   - short-term memory
   - retrieved documents
7. Call LLM (streaming or non-streaming)
8. Persist updated memory (e.g. new turn or summary)
9. Return response
```

### Flow Diagram

```
[Client App] --> POST /chat (message, conversationId)
       |
       v
[Auth] --> clientId, userId
       |
       v
[Conversation Service] --> thread_id = clientId:userId:conversationId
       |                       |
       |                       v
       |                 [Memory Store] --> load state
       |
       v
[RAG Service] --> retrieve(clientId, query, filters)
       |                       |
       |                       v
       |                 [Vector Store] --> top-k chunks
       |
       v
[LLM Orchestrator] --> prompt(system, profile, memory, chunks, message)
       |                       |
       |                       v
       |                 [LLM] --> response (stream or full)
       |
       v
[Memory Store] --> save state (updated memory)
       |
       v
[Response] --> client
```

---

## Persistence Layer

### Minimum Production Setup

| Store | Purpose | Notes |
|-------|---------|------|
| **Relational DB (Postgres)** | `clients`, `users`, `conversations`, optional `messages`, optional `memory_summaries` | Source of truth for identity and conversation metadata. |
| **Vector store** | Embeddings per client; strong metadata (e.g. `clientId`, doc type) | pgvector, Pinecone, Weaviate, etc. |
| **Memory store** | Short-term memory per `thread_id` | Redis (preferred) or Postgres; optional TTL. |

### Optional Tables

- **messages** — Long-term history for replay, analytics, or training (if needed).
- **memory_summaries** — Compressed conversation summaries for very long threads (e.g. summarise every N turns).

---

## API Design

### Primary Endpoint: Chat

**POST /chat**

**Request (conceptual):**

```json
{
  "clientId": "cheesecake-labs",
  "conversationId": "chat-456",
  "message": "Which course fits my profile?"
}
```

**Best practice:** Resolve `clientId` and `userId` from **authentication** (JWT, API key, or session), not from the body. Accept `conversationId` (and optionally `message`) in the body. This reduces spoofing and keeps tenant identity server-authoritative.

**Response:** Streaming (e.g. SSE) or non-streaming JSON, depending on endpoint variant.

### Additional Endpoints (Suggested)

| Method | Path | Purpose |
|--------|------|---------|
| POST | /chat | Single message, sync response (optional). |
| POST | /chat/stream | Single message, SSE (or similar) stream. |
| GET  | /conversations/:id | Get conversation metadata (and optionally recent messages). |
| POST | /conversations | Create conversation (returns `conversationId`). |

### API Principles

- **DTOs** for all inputs and outputs; validate with `class-validator`.
- **Structured errors** (e.g. code, message, requestId) via a global exception filter.
- **Idempotency** where relevant (e.g. conversation creation) via idempotency keys if needed.

---

## Phase 2 — Multiple Clients (Future)

When you onboard more clients, **core logic does not change**.

```
Ai Knowledge and Agent Framework
 ├─ Client A RAG (vector namespace / metadata clientId = A)
 ├─ Client B RAG (clientId = B)
 ├─ Client C RAG (clientId = C)
```

Isolation is guaranteed by:

- **clientId** in auth and in all queries.
- **thread_id** for memory (`clientId:userId:conversationId`).
- **Vector namespaces or metadata** so retrieval is always client-scoped.

New clients require: configuration (and possibly separate RAG indexing pipelines), not new application code paths.

---

## Security & Multi-Tenancy

| Concern | Approach |
|--------|----------|
| **Tenant resolution** | Derive `clientId` and `userId` from verified auth; never trust client-supplied tenant IDs for authorization. |
| **Data isolation** | Every DB and vector query includes `clientId`; consider Row Level Security (RLS) in Postgres for extra safety. |
| **Secrets** | API keys (OpenAI, etc.) in env or secret manager; per-client keys only if you need strict cost/access isolation. |
| **Input validation** | Validate and sanitize all inputs; limit message length and rate per user/client. |
| **Audit** | Log tenant and user for sensitive operations (e.g. chat, RAG access) for debugging and compliance. |

---

## Technology Stack & Replaceability

Treat these as **replaceable infrastructure** behind interfaces or dependency injection:

| Component | Example | Replaceability |
|-----------|---------|----------------|
| **LLM** | OpenAI GPT-4o-mini | Swap to another provider by implementing a common `LLMAdapter` or using a port interface. |
| **Embeddings** | OpenAI text-embedding-3-small | Same idea; abstract behind `EmbeddingsService` or equivalent. |
| **Vector store** | In-memory → pgvector / Pinecone / Weaviate | Abstract `VectorStore` or `Retriever`; same RAG pipeline. |
| **Memory store** | Redis / Postgres | Abstract `MemoryStore`; keyed by `thread_id`. |
| **Framework** | NestJS | Domain modules (e.g. conversation, RAG, auth) should depend on interfaces, not framework specifics. |

This keeps the architecture **technology-agnostic** and allows you to change providers or scale components independently.

---

## Observability & Operations

| Area | Recommendations |
|------|------------------|
| **Logging** | Structured logs (JSON); include `clientId`, `userId`, `conversationId`, `requestId`. Avoid logging full message content in production; log lengths or hashes if needed. |
| **Metrics** | Latency (e.g. p50, p99) per endpoint; token usage per request/client; RAG retrieval latency and chunk count; memory load/save latency. |
| **Tracing** | Trace ID across auth → conversation → RAG → LLM → memory save; helps debug slow or failed requests. |
| **Errors** | Global exception filter; map to stable error codes and messages; log with context (tenant, request id). |
| **Health** | `/health` (liveness); `/ready` (DB, Redis, optional vector store reachability) for readiness. |

---

## Decisions Summary

| Decision | Rationale |
|----------|------------|
| **Separate AI backend** | Clear boundary; BFF/API gateway handles client-specific HTTP; AI backend handles RAG, memory, LLM. |
| **Short-term memory per conversation** | Clear semantics; `thread_id` gives a simple, scalable key. |
| **RAG decoupled from users** | One corpus per client; user only affects filters and prompt, not embeddings. |
| **Multi-tenant from day one** | Avoids costly refactors later; same code path for all clients. |
| **User profile in prompt, not in RAG** | Keeps retrieval stable and fair; personalisation via context, not vector pollution. |
| **Auth resolves tenant** | Security and consistency; body carries only conversation and message. |
| **Replaceable LLM/vector/memory** | Enables provider changes and testing with mocks. |

This is **production-grade** architecture, not a toy setup.

---

## Current Implementation vs. Target

The following table aligns the **current codebase** with the **target architecture** above.

| Aspect | Current | Target |
|--------|---------|--------|
| **API** | `POST /cheesecake/ask/stream` (and non-stream ask) with `message` in body | Unified `POST /chat` (or `/chat/stream`) with auth-derived `clientId`/`userId`, body: `conversationId`, `message` |
| **Auth / Tenant** | Not implemented | Auth middleware + tenant resolver; `clientId`/`userId` from token |
| **Identity** | No Client/User/Conversation entities in use | Postgres (or equivalent) for clients, users, conversations |
| **Short-term memory** | LangGraph `MemorySaver` in-memory, not keyed by tenant/conversation | Memory store (Redis/Postgres) keyed by `thread_id` |
| **RAG** | Manual module: in-memory vector store, single client (Cheesecake Website) | Vector store with `clientId` (and metadata); separate index/namespace per client |
| **Streaming** | SSE in Manual controller with per-token delay | Keep SSE; optional centralised streaming in a single chat pipeline |
| **Persistence** | No DB or vector persistence | Postgres (identity + optional messages); persistent vector store; Redis (or Postgres) for memory |

**Suggested evolution**

1. Introduce **Auth and Tenant Resolver** (e.g. JWT guard + custom decorators for `clientId`/`userId`).
2. Add **Conversation** and **User** (and **Client**) entities and use them in the chat flow.
3. Introduce **MemoryStore** abstraction and implement Redis (or Postgres) keyed by `thread_id`; integrate with LangGraph checkpointer.
4. Replace in-memory vector store with **persistent vector store** and **client-scoped metadata** (e.g. `clientId`).
5. Unify entrypoint to **POST /chat** and **POST /chat/stream** with auth-derived tenant and conversation-scoped memory.

This document and the [RAG Knowledge Flow](./rag-knowledge-flow.md) together describe the intended architecture and the path from the current implementation to it.
