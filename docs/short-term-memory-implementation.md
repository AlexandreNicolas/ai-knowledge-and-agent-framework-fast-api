# Short-Term Memory Implementation Guide

This document explains **step-by-step** how to add short-term (conversation-level) memory to the AI agent so that user conversations persist per thread and can be resumed. It covers:

1. **Why the Anthropic SDK needs explicit memory management**
2. **thread_id design** — isolating memory per conversation
3. **Frontend → Backend thread_id** — sending `conversationId` from the client
4. **In-memory store** — the simplest working implementation
5. **Redis store** — production-ready drop-in replacement
6. **Trimming messages** — keeping context within Claude's limits

The design aligns with [ARCHITECTURE.md](./ARCHITECTURE.md): one memory per conversation, keyed by `thread_id = f"{client_id}:{user_id}:{conversation_id}"`.

---

## Table of Contents

1. [Concepts](#1-concepts)
2. [Step 1: Understand the Anthropic SDK's stateless model](#2-step-1-understand-the-anthropic-sdks-stateless-model)
3. [Step 2: Define a memory store interface](#3-step-2-define-a-memory-store-interface)
4. [Step 3: Implement the in-memory store](#4-step-3-implement-the-in-memory-store)
5. [Step 4: Use thread_id when calling Claude](#5-step-4-use-thread_id-when-calling-claude)
6. [Step 5: Receive conversationId from the frontend](#6-step-5-receive-conversationid-from-the-frontend)
7. [Step 6: Trim message history (count-based)](#7-step-6-trim-message-history-count-based)
8. [Step 7: Trim by token estimate](#8-step-7-trim-by-token-estimate)
9. [Step 8: Redis store (production)](#9-step-8-redis-store-production)
10. [Putting it together](#10-putting-it-together)
11. [Production notes](#11-production-notes)

---

## 1. Concepts

- **Short-term memory** is the agent's context for a single conversation — the list of previous messages.
- The **Anthropic SDK is stateless**: it does not remember previous calls. You must pass the full `messages` list on every request.
- Each conversation is identified by a **thread ID**. The same `thread_id` must be used for every request in that conversation so Claude sees the full history.
- You load the messages list at the start of each request, append the new user message, call Claude, then append Claude's response and save.

So: **maintain a `messages` list per `thread_id`**, pass it on every call, and save it after Claude responds.

---

## 2. Step 1: Understand the Anthropic SDK's stateless model

The Anthropic `messages.create()` call takes a full `messages` list each time:

```python
from anthropic import AsyncAnthropic

client = AsyncAnthropic()

# First turn
response = await client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="You are a helpful assistant.",
    messages=[
        {"role": "user", "content": "Hi, I'm Alice"},
    ],
)
# response.content[0].text == "Hello Alice! How can I help you?"

# Second turn — must include the first exchange
response = await client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1024,
    system="You are a helpful assistant.",
    messages=[
        {"role": "user",      "content": "Hi, I'm Alice"},
        {"role": "assistant", "content": "Hello Alice! How can I help you?"},
        {"role": "user",      "content": "What's my name?"},
    ],
)
# response.content[0].text == "Your name is Alice."
```

Your application owns this list. Memory management means: load it, mutate it, save it.

---

## 3. Step 2: Define a memory store interface

Abstract the storage so you can swap between dict (dev) and Redis (prod) without touching the service layer:

```python
# src/memory/store.py
from abc import ABC, abstractmethod


class MemoryStore(ABC):
    @abstractmethod
    async def get(self, thread_id: str) -> list[dict]:
        """Return the messages list for this thread (empty list if new)."""
        ...

    @abstractmethod
    async def save(self, thread_id: str, messages: list[dict]) -> None:
        """Persist the updated messages list for this thread."""
        ...

    @abstractmethod
    async def delete(self, thread_id: str) -> None:
        """Remove all messages for this thread."""
        ...
```

---

## 4. Step 3: Implement the in-memory store

For development and single-instance deployments:

```python
# src/memory/in_memory.py
from .store import MemoryStore


class InMemoryStore(MemoryStore):
    def __init__(self) -> None:
        self._data: dict[str, list[dict]] = {}

    async def get(self, thread_id: str) -> list[dict]:
        return list(self._data.get(thread_id, []))

    async def save(self, thread_id: str, messages: list[dict]) -> None:
        self._data[thread_id] = list(messages)

    async def delete(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)
```

Instantiate once at app startup and inject via FastAPI `Depends()`:

```python
# src/main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager
from memory.in_memory import InMemoryStore

memory_store: InMemoryStore | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global memory_store
    memory_store = InMemoryStore()
    yield

app = FastAPI(lifespan=lifespan)

def get_memory_store() -> InMemoryStore:
    return memory_store
```

---

## 5. Step 4: Use thread_id when calling Claude

Every request in a conversation must use the **same `thread_id`** so the store returns the correct history.

```python
# src/cheesecake/service.py
from anthropic import AsyncAnthropic
from memory.store import MemoryStore

client = AsyncAnthropic()


async def ask(
    message: str,
    thread_id: str,
    system_prompt: str,
    memory: MemoryStore,
) -> str:
    messages = await memory.get(thread_id)
    messages.append({"role": "user", "content": message})

    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    )

    answer = response.content[0].text
    messages.append({"role": "assistant", "content": answer})

    await memory.save(thread_id, messages)
    return answer
```

Build `thread_id` from auth + request body:

```python
# client_id and user_id come from auth middleware
thread_id = f"{client_id}:{user_id}:{conversation_id}"
```

Example: `"cheesecake-labs:user-123:chat-456"`

---

## 6. Step 5: Receive conversationId from the frontend

The **frontend** sends the conversation identifier so the backend can load the right thread. Per [ARCHITECTURE.md](./ARCHITECTURE.md), `client_id` and `user_id` come from **auth**, not the request body. Only `conversation_id` is provided by the client.

### Request body (Pydantic schema)

```python
# src/cheesecake/schemas.py
from pydantic import BaseModel
from typing import Optional
import uuid


class AskRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None  # sent by frontend to continue a conversation
```

### Backend: resolve thread_id

```python
# src/cheesecake/router.py
from fastapi import APIRouter, Depends
import uuid

router = APIRouter(prefix="/cheesecake", tags=["cheesecake"])


@router.post("/ask/stream")
async def ask_stream(
    body: AskRequest,
    memory: MemoryStore = Depends(get_memory_store),
    # In a real app: current_user = Depends(get_current_user)
):
    # Derive from auth in production; hardcoded here for the reference impl
    client_id = "cheesecake-labs"
    user_id = "anonymous"

    conversation_id = body.conversation_id or str(uuid.uuid4())
    thread_id = f"{client_id}:{user_id}:{conversation_id}"

    # ... call service with thread_id
```

The **frontend gets back** the `conversation_id` on the first call and sends it on subsequent calls to continue the same thread.

---

## 7. Step 6: Trim message history (count-based)

Claude has a context window limit. Keep only the last N messages to stay within it. A simple sliding window is usually enough:

```python
# src/memory/trimming.py

def trim_messages(messages: list[dict], max_messages: int = 20) -> list[dict]:
    """Keep the last max_messages turns. Always keeps an even number (user+assistant pairs)."""
    if len(messages) <= max_messages:
        return messages
    # Keep even number to avoid orphaned user/assistant messages
    keep = max_messages if max_messages % 2 == 0 else max_messages - 1
    return messages[-keep:]
```

Apply it before calling Claude:

```python
async def ask(message: str, thread_id: str, system_prompt: str, memory: MemoryStore) -> str:
    messages = await memory.get(thread_id)
    messages = trim_messages(messages, max_messages=20)  # trim before appending
    messages.append({"role": "user", "content": message})
    # ...
```

---

## 8. Step 7: Trim by token estimate

For a tighter budget, estimate token count before calling Claude. Claude's tokenizer counts roughly 4 characters per token:

```python
# src/memory/trimming.py

def estimate_tokens(text: str) -> int:
    return len(text) // 4  # rough estimate; use tiktoken for precision


def trim_messages_by_tokens(
    messages: list[dict],
    max_tokens: int = 8000,
) -> list[dict]:
    total = 0
    result = []
    for msg in reversed(messages):
        tokens = estimate_tokens(str(msg.get("content", "")))
        if total + tokens > max_tokens:
            break
        result.insert(0, msg)
        total += tokens
    return result
```

For production accuracy, use Claude's token counting endpoint:

```python
# Count tokens before sending (costs a small API call)
token_count = await client.messages.count_tokens(
    model="claude-sonnet-4-6",
    system=system_prompt,
    messages=messages,
)
```

---

## 9. Step 8: Redis store (production)

When running multiple instances or needing conversation persistence across restarts, replace `InMemoryStore` with Redis:

```python
# src/memory/redis_store.py
import json
import redis.asyncio as redis
from .store import MemoryStore

THREAD_TTL_SECONDS = 60 * 60 * 24  # 24 hours


class RedisMemoryStore(MemoryStore):
    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)

    async def get(self, thread_id: str) -> list[dict]:
        raw = await self._redis.get(f"thread:{thread_id}")
        if raw is None:
            return []
        return json.loads(raw)

    async def save(self, thread_id: str, messages: list[dict]) -> None:
        await self._redis.set(
            f"thread:{thread_id}",
            json.dumps(messages),
            ex=THREAD_TTL_SECONDS,
        )

    async def delete(self, thread_id: str) -> None:
        await self._redis.delete(f"thread:{thread_id}")

    async def close(self) -> None:
        await self._redis.aclose()
```

Swap it in by changing one line in `lifespan`:

```python
from memory.redis_store import RedisMemoryStore

memory_store = RedisMemoryStore(redis_url=settings.redis_url)
```

The service layer does not change because it depends on the `MemoryStore` interface.

---

## 10. Putting it together

Minimal flow in your service:

```python
# src/cheesecake/service.py
from anthropic import AsyncAnthropic
from memory.store import MemoryStore
from memory.trimming import trim_messages

client = AsyncAnthropic()
MAX_MESSAGES = 20


async def ask_stream(
    message: str,
    thread_id: str,
    system_prompt: str,
    memory: MemoryStore,
):
    """Async generator yielding SSE data lines."""
    messages = await memory.get(thread_id)
    messages = trim_messages(messages, max_messages=MAX_MESSAGES)
    messages.append({"role": "user", "content": message})

    full_response = []

    async with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            full_response.append(text)
            yield f"data: {text}\n\n"

    messages.append({"role": "assistant", "content": "".join(full_response)})
    await memory.save(thread_id, messages)
```

Router:

```python
# src/cheesecake/router.py
@router.post("/ask/stream")
async def ask_stream_endpoint(
    body: AskRequest,
    memory: MemoryStore = Depends(get_memory_store),
):
    conversation_id = body.conversation_id or str(uuid.uuid4())
    thread_id = f"cheesecake-labs:anonymous:{conversation_id}"

    return StreamingResponse(
        ask_stream(body.message, thread_id, SYSTEM_PROMPT, memory),
        media_type="text/event-stream",
        headers={"X-Conversation-Id": conversation_id},
    )
```

The `X-Conversation-Id` response header lets the frontend capture the conversation ID on the first request and reuse it on subsequent ones.

---

## 11. Production notes

- **InMemoryStore** is process-local; conversations are lost on restart or when running multiple workers. Use **RedisMemoryStore** for any production deployment.
- **Auth**: Always derive `client_id` and `user_id` from your auth layer (JWT, API key); never trust client-supplied tenant or user IDs.
- **TTL**: Set a TTL on Redis keys so abandoned conversations don't accumulate indefinitely (24h or 7 days is typical).
- **Token counting**: For `trim_messages_by_tokens`, use the Claude token counting API (`client.messages.count_tokens()`) for accurate counts in production.
- **thread_id format**: Use `f"{client_id}:{user_id}:{conversation_id}"` consistently so memory is isolated per conversation and tenant.
- **Streaming and memory**: Accumulate the full streamed response (`"".join(full_response)`) before saving it to memory so the stored message is complete.
- **Concurrent requests**: For the same `thread_id`, use Redis `WATCH`/transactions or an async lock to avoid race conditions when two requests update the messages list simultaneously.
