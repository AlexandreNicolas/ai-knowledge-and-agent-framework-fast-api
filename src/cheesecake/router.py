import uuid

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sentence_transformers import SentenceTransformer

from src.memory.store import MemoryStore
from src.knowledge.vector_store import VectorStore
from src.deps import get_anthropic_client, get_vector_store, get_embed_model, get_memory_store
from .schemas import AskRequest
from .service import ask_stream

router = APIRouter(prefix="/cheesecake", tags=["cheesecake"])

_CLIENT_ID = "cheesecake-labs"
_USER_ID = "anonymous"


@router.post("/ask/stream")
async def ask_stream_endpoint(
    body: AskRequest,
    memory: MemoryStore = Depends(get_memory_store),
    store: VectorStore = Depends(get_vector_store),
    embed_model: SentenceTransformer = Depends(get_embed_model),
    anthropic_client: AsyncAnthropic = Depends(get_anthropic_client),
) -> StreamingResponse:
    conversation_id = body.conversation_id or str(uuid.uuid4())
    thread_id = f"{_CLIENT_ID}:{_USER_ID}:{conversation_id}"

    return StreamingResponse(
        ask_stream(body, thread_id, memory, store, embed_model, anthropic_client),
        media_type="text/event-stream",
        headers={
            "X-Conversation-Id": conversation_id,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
