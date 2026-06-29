import httpx

from .config import EMBED_MODEL, OLLAMA_URL


class EmbeddingError(RuntimeError):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch via Ollama's /api/embed (bge-m3, 1024-dim). No weights loaded here."""
    if not texts:
        return []
    try:
        resp = httpx.post(f"{OLLAMA_URL}/api/embed",
                          json={"model": EMBED_MODEL, "input": texts},
                          timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as e:
        raise EmbeddingError(f"embedding request failed: {e}") from e
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EmbeddingError(f"unexpected embedding response: {data!r}")
    return vectors


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
