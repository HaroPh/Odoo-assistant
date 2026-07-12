import os

# Chunking
CHUNK_SIZE_TOKENS = 400
CHUNK_OVERLAP_TOKENS = 60
MIN_CHUNK_TOKENS = 80
TIKTOKEN_ENCODING = "cl100k_base"

# Embedding (external, via Ollama)
EMBED_MODEL = "bge-m3"
EMBED_DIM = 1024
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Store
RAG_DB_DSN = os.environ.get("DATABASE_URL",
                            "postgresql://admin:changeme@localhost:5433/ai_assistant")
RAG_SCHEMA = os.environ.get("RAG_SCHEMA", "public")

# Retrieval
TOP_N = 20      # candidates per retriever before fusion
TOP_K = 6       # final chunks returned
RRF_K = 60      # RRF constant

# Rerank (cross-encoder, CPU-only — spec 2026-07-12-rag-reranker)
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
RERANK_MAX_LENGTH = 512
