from __future__ import annotations

from pathlib import Path

import chromadb
from sklearn.feature_extraction.text import HashingVectorizer


def _char_bigram_analyzer(text: str) -> list[str]:
    return [text[i : i + 2] for i in range(max(len(text) - 1, 0))]


class HashingEmbedder:
    def __init__(self) -> None:
        self._vectorizer = HashingVectorizer(
            n_features=256,
            alternate_sign=False,
            norm="l2",
            analyzer=_char_bigram_analyzer,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        matrix = self._vectorizer.transform(texts)
        return matrix.toarray().tolist()

    def embed_query(self, query: str) -> list[float]:
        return self.embed_documents([query])[0]


class KnowledgeBase:
    _COLLECTION = "pcb_kb"

    def __init__(self, kb_dir: Path, persist_dir: Path, *, enabled: bool = True) -> None:
        self._kb_dir = kb_dir
        self._persist_dir = persist_dir
        self._enabled = enabled
        self._embedder = HashingEmbedder()
        self._built = False
        self._client: chromadb.PersistentClient | None = None
        self._collection = None

    def build(self) -> None:
        if not self._enabled or self._built:
            return
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self._persist_dir))
        self._collection = self._client.get_or_create_collection(name=self._COLLECTION)
        md_files = sorted(self._kb_dir.glob("*.md"))
        if not md_files:
            self._built = True
            return
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, str]] = []
        for path in md_files:
            text = path.read_text(encoding="utf-8")
            ids.append(path.stem)
            documents.append(text)
            metadatas.append({"source": path.name})
        embeddings = self._embedder.embed_documents(documents)
        self._collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings,
        )
        self._built = True

    def search(self, query: str, k: int = 4) -> list[dict]:
        if not self._enabled:
            return []
        if not self._built:
            self.build()
        if self._collection is None or self._collection.count() == 0:
            return []
        results = self._collection.query(
            query_embeddings=[self._embedder.embed_query(query)],
            n_results=min(k, self._collection.count()),
        )
        hits: list[dict] = []
        for text, meta in zip(results["documents"][0], results["metadatas"][0]):
            hits.append({"text": text, "source": meta["source"]})
        return hits
