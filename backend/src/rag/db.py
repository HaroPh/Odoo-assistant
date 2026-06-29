import os

import psycopg
from pgvector.psycopg import register_vector

from .config import RAG_DB_DSN, RAG_SCHEMA, EMBED_DIM

_SCHEMA_SQL = os.path.join(os.path.dirname(__file__), "schema.sql")


def connect(schema: str | None = None) -> psycopg.Connection:
    schema = schema or RAG_SCHEMA
    conn = psycopg.connect(RAG_DB_DSN, autocommit=True)
    register_vector(conn)
    conn.execute(f"SET search_path TO {schema}, public")
    return conn


def ensure_schema(conn: psycopg.Connection, schema: str | None = None) -> None:
    schema = schema or RAG_SCHEMA
    with open(_SCHEMA_SQL, encoding="utf-8") as f:
        sql = f.read().format(schema=schema, dim=EMBED_DIM)
    conn.execute(sql)
    conn.execute(f"SET search_path TO {schema}, public")
