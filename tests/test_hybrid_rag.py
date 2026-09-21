from app.backend.routers import rag


def test_hybrid_search_combines_vector_and_keyword_ranks(monkeypatch):
    monkeypatch.setattr(
        rag,
        "_vector_search",
        lambda *_args: [
            {"source_doc": "04.md", "section": "ETF", "chunk_index": 1, "text": "vector first", "score": 0.9},
            {"source_doc": "03.md", "section": "주식", "chunk_index": 2, "text": "vector second", "score": 0.8},
        ],
    )
    monkeypatch.setattr(
        rag,
        "_keyword_search",
        lambda *_args: [
            {"id": "03", "doc_id": "03", "title": "주식", "content": "keyword first"},
            {"id": "04", "doc_id": "04", "title": "ETF", "content": "keyword second"},
        ],
    )

    results, mode = rag._hybrid_search("ETF 주식", top_k=2, score_threshold=0)

    assert mode == "hybrid"
    assert [item["source_doc"] for item in results] == ["04.md", "03.md"]
    assert all(item["retrieval"] == "hybrid" for item in results)


def test_hybrid_search_falls_back_when_keyword_engine_is_down(monkeypatch):
    expected = [{"source_doc": "04.md", "text": "only vector", "score": 0.7}]
    monkeypatch.setattr(rag, "_vector_search", lambda *_args: expected)
    monkeypatch.setattr(rag, "_keyword_search", lambda *_args: [])

    results, mode = rag._hybrid_search("ETF", top_k=5, score_threshold=0)

    assert results == expected
    assert mode == "vector"
