"""Embedding helper for incident memory (RAG).

Falls back to a deterministic hash-based pseudo-embedding when no API key is
configured, so the pipeline works offline. Real similarity quality requires
the API key.
"""
import hashlib
from typing import List

from app.config import settings
from app.ai.metering import allow_provider, record

_DIM = 1536


class Vector(list):
    def __init__(self, values, version):
        super().__init__(values)
        self.version = version


def _hash_embed(text: str) -> List[float]:
    """Cheap stable embedding for offline mode."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    out: List[float] = []
    while len(out) < _DIM:
        digest = hashlib.sha256(digest).digest()
        out.extend(b / 255.0 - 0.5 for b in digest)
    return out[:_DIM]


def embed(text: str) -> List[float]:
    if settings.llm_provider == "disabled" or not settings.openai_api_key or not allow_provider.get():
        return Vector(_hash_embed(text), "offline-hash-v1")
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key, timeout=15.0, max_retries=1)
        resp = client.embeddings.create(model=settings.embed_model, input=text[:24000], dimensions=_DIM)
        values = list(resp.data[0].embedding)
        if len(values) != _DIM:
            raise ValueError("unsupported embedding dimensions")
        record(embedding_tokens=getattr(getattr(resp, "usage", None), "total_tokens", 0), reasoning=False)
        return Vector(values, "openai:"+settings.embed_model+":1536")
    except Exception:
        return Vector(_hash_embed(text), "offline-hash-v1")
