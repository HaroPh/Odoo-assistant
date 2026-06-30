CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.rag_documents (
    doc_id        text PRIMARY KEY,
    source_file   text NOT NULL,
    content_hash  text NOT NULL,
    lang          text NOT NULL DEFAULT 'vi',
    ingested_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.rag_chunks (
    id            bigserial PRIMARY KEY,
    doc_id        text NOT NULL REFERENCES {schema}.rag_documents(doc_id) ON DELETE CASCADE,
    source_file   text NOT NULL,
    doc_title     text NOT NULL DEFAULT '',
    section_path  text,
    page          int,
    sheet         text,
    row_range     text,
    columns       text[],
    chunk_index   int NOT NULL DEFAULT 0,
    token_count   int NOT NULL DEFAULT 0,
    visibility    text NOT NULL DEFAULT 'all',
    chunk_text    text NOT NULL,
    embedding     vector({dim}),
    ts_vector     tsvector
);

CREATE INDEX IF NOT EXISTS rag_chunks_embedding_hnsw
    ON {schema}.rag_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS rag_chunks_ts_gin
    ON {schema}.rag_chunks USING gin (ts_vector);
CREATE INDEX IF NOT EXISTS rag_chunks_doc_id
    ON {schema}.rag_chunks (doc_id);
