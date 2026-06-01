import httpx
from bs4 import BeautifulSoup
from sentence_transformers import SentenceTransformer

from .vector_store import VectorStore

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunk = text[start : start + size]
        if chunk.strip():
            chunks.append(chunk)
        if start + size >= len(text):
            break
        start += size - overlap
    return chunks


async def index_url(
    url: str,
    store: VectorStore,
    embed_model: SentenceTransformer,
    client_id: str = "default",
) -> int:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)

    chunks = _chunk_text(text)
    if not chunks:
        return 0

    # Remove existing documents for this client before re-indexing
    if store.count() > 0:
        existing = store.get(where={"client_id": client_id})
        if existing["ids"]:
            store.delete(ids=existing["ids"])

    embeddings = embed_model.encode(chunks, show_progress_bar=False).tolist()
    ids = [f"{client_id}_chunk_{i}" for i in range(len(chunks))]
    metadatas = [{"client_id": client_id, "source": url, "chunk_index": i} for i in range(len(chunks))]

    store.add(embeddings=embeddings, documents=chunks, ids=ids, metadatas=metadatas)
    return len(chunks)
