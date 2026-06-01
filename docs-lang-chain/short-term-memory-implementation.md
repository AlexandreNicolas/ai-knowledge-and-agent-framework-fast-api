# Short-Term Memory Implementation Guide

This document explains **step-by-step** how to add short-term (conversation-level) memory to the Ai Knowledge and Agent Framework agent so that user conversations are persisted per thread and can be resumed. It covers:

1. **Checkpointer and thread ID** — Persisting and loading state per conversation
2. **Frontend → Backend thread ID** — Sending `conversationId` from the client and building `thread_id` on the server
3. **Optional custom state** — Extending state with `userId`, `clientId`, preferences
4. **Trimming messages** — Keeping context within limits (count-based and token-based)

The design aligns with [ARCHITECTURE.md](./ARCHITECTURE.md): one memory per conversation, keyed by `thread_id = clientId:userId:conversationId`.

---

## Table of Contents

1. [Concepts](#1-concepts)
2. [Step 1: Add a checkpointer to the agent](#2-step-1-add-a-checkpointer-to-the-agent)
3. [Step 2: Use thread_id when invoking the agent](#3-step-2-use-thread_id-when-invoking-the-agent)
4. [Step 3: Receive conversationId from the frontend](#4-step-3-receive-conversationid-from-the-frontend)
5. [Step 4 (optional): Custom state schema](#5-step-4-optional-custom-state-schema)
6. [Step 5: Trim message history (count-based)](#6-step-5-trim-message-history-count-based)
7. [Step 6: Trim by token limit (maxTokens)](#7-step-6-trim-by-token-limit-maxtokens)
8. [Putting it together](#8-putting-it-together)
9. [Production notes](#9-production-notes)

---

## 1. Concepts

- **Short-term memory** is the agent’s state for a single conversation (messages, tool results, etc.).
- LangChain’s agent keeps this in the **graph state**. A **checkpointer** persists that state so it can be read and updated across requests.
- Each conversation is identified by a **thread ID**. The same `thread_id` must be used for every request in that conversation so the agent sees the full history.
- State is **read at the start** of each step and **written when** the agent is invoked or a step (e.g. tool call) completes.

So: **specify a checkpointer when creating the agent**, and **pass the same `thread_id` in the config** for every call in that conversation.

---

## 2. Step 1: Add a checkpointer to the agent

Create a checkpointer and pass it into `createAgent`. For development, `MemorySaver` (in-memory) is enough. For production, you would use a persistent store (e.g. Redis or Postgres) keyed by `thread_id` (see [Production notes](#9-production-notes)).

```ts
import { createAgent } from "langchain";
import { MemorySaver } from "@langchain/langgraph";

const checkpointer = new MemorySaver();

const agent = createAgent({
  model: "gpt-4o-mini", // or your ChatOpenAI instance
  tools: [/* ... */],
  checkpointer,
});
```

After this, the agent will read/write state per thread when you pass `configurable: { thread_id }` on invoke/stream.

---

## 3. Step 2: Use thread_id when invoking the agent

Every time you call the agent for a given conversation, pass the same `thread_id` in the **config** so the checkpointer loads and saves state for that thread.

**Non-streaming (`invoke`):**

```ts
const result = await agent.invoke(
  { messages: [{ role: "user", content: "Hi, I'm Bob" }] },
  { configurable: { thread_id: "cheesecake-labs:user-123:chat-456" } }
);
```

**Streaming (`stream`):**

```ts
const stream = await agent.stream(
  { messages: [{ role: "user", content: "What did I just say?" }] },
  {
    streamMode: "messages",
    configurable: { thread_id: "cheesecake-labs:user-123:chat-456" },
  }
);
```

If the frontend sends a **conversation ID** and the backend has **clientId** and **userId** (e.g. from auth), you build `thread_id` as:

```ts
const threadId = `${clientId}:${userId}:${conversationId}`;
```

Then use `threadId` in `configurable: { thread_id: threadId }`.

---

## 4. Step 3: Receive conversationId from the frontend

The **frontend** should send the conversation identifier so the backend can load the right thread. Per [ARCHITECTURE.md](./ARCHITECTURE.md), `clientId` and `userId` should come from **auth**, not the request body. Only **conversationId** is provided by the client.

### 4.1 Request body (DTO)

Extend your chat/ask DTO to include an optional `conversationId`. If omitted, the backend can create a new conversation and return its ID, or use a default.

```ts
// dto/ask-cheesecake.dto.ts (or chat.dto.ts)
export class AskCheesecakeDto {
  message: string;
  conversationId?: string;  // sent by frontend to continue a conversation
}
```

### 4.2 Backend: resolve thread_id

In your NestJS controller/service:

1. Get **clientId** and **userId** from your auth layer (e.g. JWT guard, custom decorators).
2. Get **conversationId** from the request body (or create one for new conversations).
3. Build **thread_id** and pass it into the agent config.

Example (conceptual):

```ts
// In your controller or conversation service
const clientId = req.user.clientId;   // from auth
const userId = req.user.userId;       // from auth
const conversationId = body.conversationId ?? generateNewConversationId();

const threadId = `${clientId}:${userId}:${conversationId}`;

const stream = await agent.stream(
  { messages: [{ role: "user", content: body.message }] },
  {
    streamMode: "messages",
    configurable: { thread_id: threadId },
  }
);
```

So: **frontend sends `conversationId`**; **backend builds `thread_id`** from auth + body and uses it in every agent call for that conversation.

---

## 5. Step 4 (optional): Custom state schema

You can extend the agent state with custom fields (e.g. `userId`, `clientId`, preferences) using **middleware** and a **state schema**. This keeps tenant and user context inside the graph state.

```ts
import { createAgent, createMiddleware } from "langchain";
import { StateSchema, MemorySaver } from "@langchain/langgraph";
import * as z from "zod";

const CustomState = new StateSchema({
  userId: z.string(),
  clientId: z.string().optional(),
  preferences: z.record(z.string(), z.any()).optional(),
});

const stateExtensionMiddleware = createMiddleware({
  name: "StateExtension",
  stateSchema: CustomState,
});

const checkpointer = new MemorySaver();
const agent = createAgent({
  model: "gpt-4o-mini",
  tools: [],
  middleware: [stateExtensionMiddleware],
  checkpointer,
});

// Pass custom state on invoke/stream
const result = await agent.invoke(
  {
    messages: [{ role: "user", content: "Hello" }],
    userId: "user_123",
    clientId: "cheesecake-labs",
    preferences: { theme: "dark" },
  },
  { configurable: { thread_id: threadId } }
);
```

Prefer **StateSchema** for state definitions; plain Zod objects are also supported. Custom state is then available in the graph (e.g. in tools or prompts).

---

## 6. Step 5: Trim message history (count-based)

LLMs have limited context. You can trim the message list in a **beforeModel** hook so only the first message plus the last N messages are kept.

Use **middleware** with `beforeModel` and **RemoveMessage** with `REMOVE_ALL_MESSAGES` to replace the whole message list:

```ts
import { RemoveMessage } from "@langchain/core/messages";
import { createAgent, createMiddleware } from "langchain";
import { MemorySaver, REMOVE_ALL_MESSAGES } from "@langchain/langgraph";

const trimMessages = createMiddleware({
  name: "TrimMessages",
  beforeModel: (state) => {
    const messages = state.messages;

    if (messages.length <= 3) {
      return; // No changes needed
    }

    const firstMsg = messages[0];
    const recentMessages =
      messages.length % 2 === 0 ? messages.slice(-3) : messages.slice(-4);
    const newMessages = [firstMsg, ...recentMessages];

    return {
      messages: [
        new RemoveMessage({ id: REMOVE_ALL_MESSAGES }),
        ...newMessages,
      ],
    };
  },
});

const checkpointer = new MemorySaver();
const agent = createAgent({
  model: "gpt-4o-mini",
  tools: [],
  middleware: [trimMessages],
  checkpointer,
});
```

This keeps the first message (e.g. system or context) and a sliding window of recent messages.

---

## 7. Step 6: Trim by token limit (maxTokens)

To respect a **token budget** instead of a fixed message count, use the **trimMessages** utility. You pass `maxTokens`, a **strategy** (e.g. `"last"` to keep the most recent tokens), and optional **startOn**/**endOn** so the trim aligns with human/tool boundaries.

Example with a token counter (replace with a real tokenizer in production, e.g. from `@langchain/core` or your model’s tokenizer):

```ts
import { trimMessages } from "@langchain/core/messages";
import { RemoveMessage } from "@langchain/core/messages";
import { createAgent, createMiddleware } from "langchain";
import { MemorySaver, REMOVE_ALL_MESSAGES } from "@langchain/langgraph";

const trimMessageHistory = createMiddleware({
  name: "TrimMessages",
  beforeModel: async (state) => {
    const trimmed = await trimMessages(state.messages, {
      maxTokens: 384,
      strategy: "last",
      startOn: "human",
      endOn: ["human", "tool"],
      tokenCounter: (msgs) => msgs.length, // replace with real token count
    });
    return {
      messages: [new RemoveMessage({ id: REMOVE_ALL_MESSAGES }), ...trimmed],
    };
  },
});

const checkpointer = new MemorySaver();
const agent = createAgent({
  model: "gpt-4o-mini",
  tools: [],
  middleware: [trimMessageHistory],
  checkpointer,
});
```

- **maxTokens** — Maximum tokens to keep in the list.
- **strategy: "last"** — Keep the most recent messages that fit in the budget.
- **startOn / endOn** — Trim so the result starts/ends on the right message types (e.g. human/tool) for cleaner context.
- **tokenCounter** — For production, use a proper tokenizer (e.g. `tiktoken` or your LLM’s tokenizer) so the count matches the model’s context window.

---

## 8. Putting it together

Minimal flow in your backend:

1. **Create the agent once** (or per client) with:
   - `checkpointer` (e.g. `new MemorySaver()` for now)
   - Optional `middleware`: custom state, trim by count, or trim by `maxTokens`
2. **On each request**:
   - Read `clientId`, `userId` from auth.
   - Read `conversationId` from the body (or create one).
   - Build `thread_id = `${clientId}:${userId}:${conversationId}`.
   - Call `agent.invoke(...)` or `agent.stream(...)` with:
     - Input: `messages` (and any custom state).
     - Config: `{ configurable: { thread_id: threadId } }`.

Example combining checkpointer + trim middleware:

```ts
const checkpointer = new MemorySaver();
const agent = createAgent({
  model: llm,
  tools: [retrieve],
  systemPrompt,
  checkpointer,
  middleware: [trimMessageHistory],
});

// In your service (e.g. CheesecakeService.askStream)
const threadId = `${clientId}:${userId}:${conversationId}`;
const stream = await agent.stream(
  { messages: [{ role: "user", content: message }] },
  {
    streamMode: "messages",
    configurable: { thread_id: threadId },
  }
);
```

---

## 9. Production notes

- **MemorySaver** is in-memory and process-local. For multiple instances or restarts, use a **persistent checkpointer** (e.g. Redis or Postgres) keyed by `thread_id`, as in [ARCHITECTURE.md](./ARCHITECTURE.md) (Memory store keyed by `thread_id`).
- **Auth**: Always derive `clientId` and `userId` from your auth layer; do not trust client-supplied tenant or user IDs for authorization.
- **Token counter**: For `trimMessages` in production, plug in a real tokenizer (e.g. `tiktoken` or the one for your model) so `maxTokens` matches the model’s context window.
- **thread_id format**: Use `${clientId}:${userId}:${conversationId}` consistently so memory is isolated per conversation and tenant.

Once this is in place, the frontend can send the same `conversationId` for a conversation and the backend will load and update short-term memory for that thread automatically.
