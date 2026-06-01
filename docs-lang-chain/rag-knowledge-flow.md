# Simple 2-Step RAG Flow with LangChain

This document describes how to build a **Retrieval-Augmented Generation (RAG)** flow for a knowledge base: index your business documents once, then answer questions over them with an LLM. The architecture keeps two clear steps: **retrieve** (find relevant chunks) then **generate** (answer grounded in that context).

---

## Overview

**RAG** reduces hallucinations and keeps answers grounded in your data by:

1. **Index time (run once, or when docs change):** Load documents → split into chunks → embed → store in a vector store.
2. **Query time:** Take the user question → retrieve top-k relevant chunks → pass context + question to the LLM → return the answer.

The **2-step flow** is: **retrieve** → **generate**. No reranking or multi-hop in this minimal version.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  INDEX (once or on update)                                       │
│  Load → Split → Embed → Store                                    │
│  DocumentLoader → TextSplitter → Embeddings → VectorStore        │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│  QUERY TIME                                                      │
│  Question → Retriever → Context + Question → LLM → Answer        │
│  Retriever.getRelevantDocuments() → ChatPromptTemplate → ChatModel│
└─────────────────────────────────────────────────────────────────┘
```

- **Index:** Business data is loaded (e.g. from files or URLs), split into chunks, embedded, and stored in a vector store.
- **Query:** For each question, the retriever fetches relevant chunks; the LLM answers using that context in a single call.

---

## LangChain JS Abstractions

LangChain JS maps cleanly onto this flow:

| Step        | LangChain component        | Role                                      |
|------------|----------------------------|-------------------------------------------|
| Load       | `DocumentLoader`           | Load from file, web, PDF, etc.            |
| Split      | `TextSplitter`             | Chunk text (e.g. `RecursiveCharacterTextSplitter`) |
| Embed      | `Embeddings`               | Turn text into vectors (e.g. OpenAI)     |
| Store      | `VectorStore`              | Persist and search vectors               |
| Retrieve   | `Retriever`                | Top-k similar chunks for a query         |
| Prompt     | `ChatPromptTemplate`       | Context + question → messages            |
| Generate   | `ChatModel`                | LLM call (e.g. `ChatOpenAI`)              |
| Parse      | `OutputParser`             | e.g. `StringOutputParser` for plain text  |

For **prototyping** use `InMemoryVectorStore`; for **persistence** use `@langchain/community` integrations such as **HNSWLib** or **FAISS** (e.g. `faiss-node`).

---

## Dependencies

```bash
npm install langchain @langchain/openai @langchain/community @langchain/core
# Optional, for persistent vector store:
npm install faiss-node
# Or use HNSWLib from @langchain/community
```

Set `OPENAI_API_KEY` in your environment.

---

## Minimal 2-Step RAG (standalone)

### 1. Index: load → split → embed → store

```typescript
import { TextLoader } from "@langchain/community/document_loaders/fs/text";
import { RecursiveCharacterTextSplitter } from "@langchain/textsplitters";
import { OpenAIEmbeddings } from "@langchain/openai";
import { InMemoryVectorStore } from "langchain/vectorstores/in_memory";

// Load documents (or WebBaseLoader, PDFLoader, etc.)
const loader = new TextLoader("business_info.txt");
const docs = await loader.load();

// Split into chunks
const splitter = new RecursiveCharacterTextSplitter({
  chunkSize: 1000,
  chunkOverlap: 200,
});
const splits = await splitter.splitDocuments(docs);

// Embed and store (use HNSWLib.fromDocuments(...) for persistence)
const embeddings = new OpenAIEmbeddings();
const vectorStore = await InMemoryVectorStore.fromDocuments(splits, embeddings);
const retriever = vectorStore.asRetriever(4);  // top-k = 4
```

### 2. Query: retrieve → generate

```typescript
import { ChatPromptTemplate } from "@langchain/core/prompts";
import { ChatOpenAI } from "@langchain/openai";
import { StringOutputParser } from "@langchain/core/output_parsers";

const llm = new ChatOpenAI({ model: "gpt-4o-mini" });
const prompt = ChatPromptTemplate.fromMessages([
  ["system", "Answer based only on this context:\n{context}\n\nIf the context does not contain the answer, say so."],
  ["human", "Question: {question}"],
]);
const chain = prompt.pipe(llm).pipe(new StringOutputParser());

// One LLM call per query
const relevantDocs = await retriever.getRelevantDocuments("What are our products?");
const context = relevantDocs.map((d) => d.pageContent).join("\n\n");
const response = await chain.invoke({ context, question: "What are our products?" });
console.log(response);
```

---

## NestJS: Knowledge resource

The same flow is exposed as a **NestJS resource** named **knowledge** (domain-oriented naming).

### Endpoints

- **POST /knowledge/index** — Index business data (e.g. from a file path or document content). Runs: load → split → embed → store.
- **POST /knowledge/query** — Ask a question; backend retrieves chunks, then runs the LLM with context and returns the answer.

### Design choices

- **Thin controller:** Only validates input and delegates to the knowledge service.
- **Service:** Owns the RAG pipeline (retriever + prompt + LLM). Vector store and LLM are injectable so you can swap InMemoryVectorStore for HNSWLib/FAISS or change the model.
- **DTOs:** Request/response use DTOs and `class-validator`; internal models are not exposed.
- **One LLM call per query:** Retrieve then generate, no extra reranking step.

### File layout

```
src/knowledge/
├── knowledge.module.ts
├── knowledge.controller.ts
├── knowledge.service.ts
├── dto/
│   ├── index-knowledge.dto.ts   # body for POST /knowledge/index
│   ├── query-knowledge.dto.ts   # body for POST /knowledge/query
│   └── query-response.dto.ts    # response for query
```

### Usage example

```bash
# Index a document (path or content depending on your DTO)
curl -X POST http://localhost:3000/knowledge/index \
  -H "Content-Type: application/json" \
  -d '{"filePath": "business_info.txt"}'

# Query
curl -X POST http://localhost:3000/knowledge/query \
  -H "Content-Type: application/json" \
  -d '{"question": "What are our products?"}'
```

---

## Persistence (beyond prototyping)

- **InMemoryVectorStore:** Lost on restart; good for local/dev.
- **HNSWLib:** `await vectorStore.save("path")` / `HNSWLib.load("path", embeddings)` for a single-node persistent index.
- **FAISS:** Same idea with `faiss-node`; save/load the index path.

Keep the **retrieve-then-generate** flow; only the **VectorStore** implementation changes. The NestJS knowledge service should depend on an abstract retriever or vector store so you can swap implementations without changing the controller or the RAG steps.

---

## Summary

| Phase    | Steps                    | LangChain components                          |
|----------|--------------------------|-----------------------------------------------|
| **Index**| Load → Split → Embed → Store | DocumentLoader, TextSplitter, Embeddings, VectorStore |
| **Query**| Retrieve → Generate      | Retriever, ChatPromptTemplate, ChatModel, OutputParser |

Start with this 2-step RAG (retrieve then generate), then add persistence (HNSWLib/FAISS), reranking, or multiple retrievers as needed.
