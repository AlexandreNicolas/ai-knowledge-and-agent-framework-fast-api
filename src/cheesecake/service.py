from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic
from sentence_transformers import SentenceTransformer

from src.memory.store import MemoryStore
from src.memory.trimming import trim_messages
from src.knowledge.retriever import retrieve
from src.knowledge.vector_store import VectorStore
from .schemas import AskRequest, UserPreferences

MODEL = "claude-haiku-4-5"
MAX_MESSAGES = 20
CLIENT_ID = "cheesecake-labs"

SYSTEM_PROMPT = (
    "You are a helpful assistant for Cheesecake Labs, a technology company that designs "
    "and builds high-quality digital products. Answer questions based on the provided "
    "context. If the context does not contain the answer, say so clearly. "
    "Be concise, friendly, and professional."
)


def _build_user_message(message: str, prefs: UserPreferences | None) -> str:
    if not prefs:
        return message

    parts: list[str] = []
    if prefs.user_name:
        parts.append(f"name={prefs.user_name}")
    if prefs.industry:
        parts.append(f"industry={prefs.industry}")
    if prefs.project_type:
        parts.append(f"project_type={prefs.project_type}")
    if prefs.company_size:
        parts.append(f"company_size={prefs.company_size}")
    if prefs.interests:
        parts.append(f"interests={', '.join(prefs.interests)}")

    if not parts:
        return message

    return f"[User context: {'; '.join(parts)}]\n\n{message}"


async def ask_stream(
    body: AskRequest,
    thread_id: str,
    memory: MemoryStore,
    store: VectorStore,
    embed_model: SentenceTransformer,
    anthropic_client: AsyncAnthropic,
) -> AsyncGenerator[str, None]:
    context = retrieve(
        query=body.message,
        store=store,
        embed_model=embed_model,
        client_id=CLIENT_ID,
    )

    system = SYSTEM_PROMPT
    if context:
        system = f"{SYSTEM_PROMPT}\n\n## Knowledge Base\n{context}"

    messages = await memory.get(thread_id)
    messages = trim_messages(messages, max_messages=MAX_MESSAGES)
    messages.append({"role": "user", "content": _build_user_message(body.message, body.user_preferences)})

    full_response: list[str] = []

    async with anthropic_client.messages.stream(
        model=MODEL,
        max_tokens=1024,
        system=system,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            full_response.append(text)
            yield f"data: {text}\n\n"

    messages.append({"role": "assistant", "content": "".join(full_response)})
    await memory.save(thread_id, messages)
