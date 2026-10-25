"""Embed every chunk that has no vector yet. Safe to stop and re-run: finished work is already saved."""
import math
import time

from sqlalchemy import create_engine, text

from sec_copilot.config import DATABASE_URL
from sec_copilot.rag.embeddings import EMBEDDING_MODEL, GeminiEmbedder, QuotaExceeded, to_pgvector

BATCH_SIZE = 16            # chunks per API request
TOKENS_PER_MINUTE = 25_000  # the free tier allows 30,000, so we stay safely below it
CHARS_PER_TOKEN = 3.2       # deliberately pessimistic: real text is about 4 characters per token

COUNT_CHUNKS = text("SELECT count(*) FILTER (WHERE embedding IS NULL), count(*) FROM doc_chunks")
SELECT_PENDING = text("SELECT id, content FROM doc_chunks WHERE embedding IS NULL ORDER BY id LIMIT :limit")
SAVE_EMBEDDING = text("""
    UPDATE doc_chunks SET embedding = CAST(:embedding AS vector), embedding_model = :model WHERE id = :id
""")


def estimate_tokens(texts: list[str]) -> int:
    return math.ceil(sum(len(t) for t in texts) / CHARS_PER_TOKEN)


def embed_pending(
    engine, embedder, batch_size=BATCH_SIZE, tokens_per_minute=TOKENS_PER_MINUTE,
    sleep=time.sleep, clock=time.monotonic, log=print,
) -> int:
    """Embed all chunks without a vector, one batch at a time. Returns how many were embedded."""
    with engine.connect() as conn:
        pending, total = conn.execute(COUNT_CHUNKS).one()
    log(f"{pending} of {total} chunks need embeddings")

    done = 0
    while True:
        with engine.connect() as conn:
            rows = conn.execute(SELECT_PENDING, {"limit": batch_size}).all()
        if not rows:
            break

        started = clock()
        texts = [row.content for row in rows]
        try:
            vectors = embedder.embed_documents(texts)
        except QuotaExceeded as error:
            log(f"Stopped early: {error}. Everything embedded so far is saved. Run this again later.")
            break
        if len(vectors) != len(rows):
            raise RuntimeError("Got a different number of vectors than chunks sent.")

        with engine.begin() as conn:   # save each batch right away, so a stop loses nothing
            conn.execute(SAVE_EMBEDDING, [
                {"id": row.id, "embedding": to_pgvector(vector), "model": EMBEDDING_MODEL}
                for row, vector in zip(rows, vectors)
            ])
        done += len(rows)
        log(f"  embedded {done} of {pending} ({100 * done // max(pending, 1)}%)")

        # Pace ourselves so we never exceed the tokens-per-minute limit.
        minimum_seconds = estimate_tokens(texts) / tokens_per_minute * 60
        wait = minimum_seconds - (clock() - started)
        if wait > 0:
            sleep(wait)
    return done


def main() -> None:
    engine = create_engine(DATABASE_URL)
    done = embed_pending(engine, GeminiEmbedder())
    with engine.connect() as conn:
        missing = conn.execute(text("SELECT count(*) FROM doc_chunks WHERE embedding IS NULL")).scalar()
    print(f"\nDone: {done} chunks embedded this run, {missing} still waiting.")


if __name__ == "__main__":
    main()