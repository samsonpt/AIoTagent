from pathlib import Path

import pytest

from cloud.rag import HashingEmbedder, KnowledgeBase

ROOT = Path(__file__).resolve().parents[2]


def test_hashing_embedder_deterministic():
    embedder = HashingEmbedder()
    a = embedder.embed_query("蚀刻喷嘴堵塞")
    b = embedder.embed_query("蚀刻喷嘴堵塞")
    assert a == b
    assert len(a) == 256


def test_rag_returns_sources_for_nozzle_query(tmp_path):
    kb = KnowledgeBase(ROOT / "cloud" / "kb", tmp_path / "chroma", enabled=True)
    hits = kb.search("蚀刻喷嘴堵塞 残留", k=3)
    assert hits and all("source" in h and "text" in h for h in hits)


def test_rag_disabled_returns_empty(tmp_path):
    kb = KnowledgeBase(ROOT / "cloud" / "kb", tmp_path / "chroma", enabled=False)
    assert kb.search("蚀刻喷嘴堵塞", k=3) == []


def test_rag_empty_kb_returns_empty(tmp_path):
    empty_dir = tmp_path / "empty_kb"
    empty_dir.mkdir()
    kb = KnowledgeBase(empty_dir, tmp_path / "chroma", enabled=True)
    assert kb.search("任意查询", k=3) == []
