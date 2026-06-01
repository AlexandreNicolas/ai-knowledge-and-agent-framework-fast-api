from dataclasses import dataclass, field

import numpy as np


@dataclass
class _Doc:
    id: str
    embedding: list[float]
    document: str
    metadata: dict = field(default_factory=dict)


class VectorStore:
    """Lightweight in-memory vector store backed by numpy cosine similarity.

    Matches the subset of the chromadb Collection API used by the project so
    that swapping to a persistent backend later only touches this file.
    """

    def __init__(self) -> None:
        self._docs: list[_Doc] = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add(
        self,
        embeddings: list[list[float]],
        documents: list[str],
        ids: list[str],
        metadatas: list[dict] | None = None,
    ) -> None:
        for i, (emb, doc, id_) in enumerate(zip(embeddings, documents, ids)):
            meta = metadatas[i] if metadatas else {}
            self._docs.append(_Doc(id=id_, embedding=emb, document=doc, metadata=meta))

    def delete(self, ids: list[str]) -> None:
        id_set = set(ids)
        self._docs = [d for d in self._docs if d.id not in id_set]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def count(self) -> int:
        return len(self._docs)

    def get(self, where: dict | None = None) -> dict:
        docs = self._filter(where)
        return {
            "ids": [d.id for d in docs],
            "documents": [d.document for d in docs],
            "metadatas": [d.metadata for d in docs],
        }

    def query(
        self,
        query_embeddings: list[list[float]],
        n_results: int = 4,
        where: dict | None = None,
    ) -> dict:
        candidates = self._filter(where)
        if not candidates:
            return {"documents": [[]], "ids": [[]], "metadatas": [[]]}

        q = np.array(query_embeddings[0], dtype=np.float32)
        corpus = np.array([d.embedding for d in candidates], dtype=np.float32)

        q_norm = q / (np.linalg.norm(q) + 1e-10)
        c_norm = corpus / (np.linalg.norm(corpus, axis=1, keepdims=True) + 1e-10)
        scores = c_norm @ q_norm

        k = min(n_results, len(candidates))
        top_idx = np.argsort(scores)[::-1][:k]
        top = [candidates[int(i)] for i in top_idx]

        return {
            "documents": [[d.document for d in top]],
            "ids": [[d.id for d in top]],
            "metadatas": [[d.metadata for d in top]],
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _filter(self, where: dict | None) -> list[_Doc]:
        if not where:
            return list(self._docs)
        return [d for d in self._docs if all(d.metadata.get(k) == v for k, v in where.items())]
