CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.erp_entity_index (
    model       text NOT NULL,
    odoo_id     integer NOT NULL,
    name        text NOT NULL,
    search_text text NOT NULL,
    embedding   vector(1024),
    synced_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model, odoo_id)
);

CREATE INDEX IF NOT EXISTS erp_entity_index_trgm
    ON {schema}.erp_entity_index USING gin (search_text gin_trgm_ops);
