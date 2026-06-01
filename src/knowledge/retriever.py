from sentence_transformers import SentenceTransformer

from .vector_store import VectorStore


def retrieve(
    query: str,
    store: VectorStore,
    embed_model: SentenceTransformer,
    client_id: str = "default",
    top_k: int = 4,
) -> str:
    if store.count() == 0:
        return ""

    query_embedding = embed_model.encode([query], show_progress_bar=False).tolist()
    results = store.query(
        query_embeddings=query_embedding,
        n_results=min(top_k, store.count()),
        where={"client_id": client_id},
    )

    if not results["documents"] or not results["documents"][0]:
        return ""

    return "\n\n".join(results["documents"][0])
