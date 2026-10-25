"""Find the chunks that best answer a question: keyword search, vector search, or both fused together."""
import argparse
import re
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text

from sec_copilot.config import DATABASE_URL

CANDIDATES = 30     # how many results each method contributes before they are merged
RRF_K = 60          # standard constant for reciprocal rank fusion; it softens the gap between ranks

FILTERS = """
    AND (CAST(:ticker AS text) IS NULL OR c.ticker = :ticker)
    AND (CAST(:fiscal_year AS integer) IS NULL OR d.fiscal_year = :fiscal_year)
    AND (CAST(:section AS text) IS NULL OR d.section = :section)
"""

KEYWORD_SQL = text(f"""
    SELECT d.id, c.ticker, d.section, d.fiscal_year, d.chunk_index, d.content,
           ts_rank_cd(d.content_tsv, q) AS score
    FROM doc_chunks d
    JOIN companies c ON c.cik = d.cik,
         to_tsquery('english', :query) AS q
    WHERE d.content_tsv @@ q {FILTERS}
    ORDER BY score DESC
    LIMIT :k
""")

VECTOR_SQL = text(f"""
    SELECT d.id, c.ticker, d.section, d.fiscal_year, d.chunk_index, d.content,
           1 - (d.embedding <=> CAST(:vector AS vector)) AS score
    FROM doc_chunks d
    JOIN companies c ON c.cik = d.cik
    WHERE d.embedding IS NOT NULL {FILTERS}
    ORDER BY d.embedding <=> CAST(:vector AS vector)
    LIMIT :k
""")


@dataclass
class Hit:
    id: int
    ticker: str
    section: str
    fiscal_year: int
    chunk_index: int
    content: str
    score: float
    found_by: dict[str, int] = field(default_factory=dict)   # which search found it, and at what rank


def build_tsquery(question: str) -> str:
    """'What risks does Apple face?' -> 'what | risks | does | apple | face'. Postgres drops filler words itself.

    We join with OR, because requiring every word of a question to appear would match almost nothing.
    """
    words = dict.fromkeys(re.findall(r"[a-z0-9]+", question.lower()))   # unique, in order
    return " | ".join(words)


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Merge several ranked lists of ids. An item near the top of ANY list scores well; near the top of several, best."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def _filters(ticker, fiscal_year, section) -> dict:
    return {"ticker": ticker, "fiscal_year": fiscal_year, "section": section}


def _to_hits(rows, source: str) -> list[Hit]:
    return [
        Hit(r.id, r.ticker, r.section, r.fiscal_year, r.chunk_index, r.content, float(r.score), {source: rank})
        for rank, r in enumerate(rows, start=1)
    ]


def keyword_search(engine, question, k=10, ticker=None, fiscal_year=None, section=None) -> list[Hit]:
    query = build_tsquery(question)
    if not query:
        return []
    with engine.connect() as conn:
        rows = conn.execute(KEYWORD_SQL, {"query": query, "k": k, **_filters(ticker, fiscal_year, section)}).all()
    return _to_hits(rows, "keyword")


def vector_search(engine, embedder, question, k=10, ticker=None, fiscal_year=None, section=None) -> list[Hit]:
    from sec_copilot.rag.embeddings import to_pgvector

    vector = to_pgvector(embedder.embed_query(question))
    with engine.begin() as conn:
        # Look at more graph nodes than the default, so filtering to one company still returns enough rows.
        conn.execute(text("SET LOCAL hnsw.ef_search = 400"))
        rows = conn.execute(VECTOR_SQL, {"vector": vector, "k": k, **_filters(ticker, fiscal_year, section)}).all()
    return _to_hits(rows, "vector")


def hybrid_search(engine, embedder, question, k=8, ticker=None, fiscal_year=None, section=None) -> list[Hit]:
    """Run both searches and merge them, so exact terms AND similar meaning both count."""
    filters = {"ticker": ticker, "fiscal_year": fiscal_year, "section": section}
    keyword = keyword_search(engine, question, CANDIDATES, **filters)
    vector = vector_search(engine, embedder, question, CANDIDATES, **filters)

    by_id: dict[int, Hit] = {}
    for hit in keyword + vector:
        if hit.id in by_id:
            by_id[hit.id].found_by.update(hit.found_by)
        else:
            by_id[hit.id] = hit

    fused = reciprocal_rank_fusion([[h.id for h in keyword], [h.id for h in vector]])
    results = []
    for chunk_id, score in fused[:k]:
        hit = by_id[chunk_id]
        hit.score = score
        results.append(hit)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Search the filing chunks.")
    parser.add_argument("question")
    parser.add_argument("--mode", choices=["keyword", "vector", "hybrid"], default="hybrid")
    parser.add_argument("--ticker")
    parser.add_argument("--year", type=int, help="fiscal year")
    parser.add_argument("--section", choices=["Item 1A", "Item 7"])
    parser.add_argument("-k", type=int, default=5)
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)
    filters = {"ticker": args.ticker, "fiscal_year": args.year, "section": args.section}
    if args.mode == "keyword":
        hits = keyword_search(engine, args.question, args.k, **filters)
    else:
        from sec_copilot.rag.embeddings import GeminiEmbedder

        embedder = GeminiEmbedder()
        search = vector_search if args.mode == "vector" else hybrid_search
        hits = search(engine, embedder, args.question, args.k, **filters)

    for number, hit in enumerate(hits, start=1):
        found = ", ".join(f"{name} #{rank}" for name, rank in hit.found_by.items())
        print(f"\n[{number}] {hit.ticker} FY{hit.fiscal_year} {hit.section} chunk {hit.chunk_index}"
              f"  score={hit.score:.4f}  ({found})")
        print("    " + hit.content[:350].replace("\n", " "))
    if not hits:
        print("No results.")


if __name__ == "__main__":
    main()