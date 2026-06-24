-- ERP AI Assistant — Postgres init (chạy 1 lần khi volume trống).
-- Context mặc định: database ai_assistant (POSTGRES_DB), user superuser.

-- ─── Databases phụ cho service khác ─────────────────────────────────────────
CREATE DATABASE litellm;     -- log request/response của LiteLLM
CREATE DATABASE langfuse;    -- trace agent (Phase 2)

-- ─── pgvector trên ai_assistant ─────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;

-- ─── Users (tối thiểu cho JWT + FK audit) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    username    VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role        VARCHAR(20) NOT NULL DEFAULT 'viewer',  -- viewer|operator|manager|admin
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ─── Audit log (BẬT TỪ DAY 1 — kể cả read actions; TAD §8.5) ─────────────────
CREATE TABLE IF NOT EXISTS erp_action_audit (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      VARCHAR(100) NOT NULL,
    user_id         INTEGER REFERENCES users(id),
    username        VARCHAR(100),
    action_type     VARCHAR(50) NOT NULL,    -- read|write|approval
    erp_model       VARCHAR(100),
    erp_operation   VARCHAR(50),             -- search_read|create|write|unlink
    erp_record_id   INTEGER,
    input_params    JSONB,
    user_prompt     TEXT,
    ai_generated_params JSONB,
    confirmation_required BOOLEAN DEFAULT FALSE,
    confirmation_received BOOLEAN DEFAULT FALSE,
    executed_at     TIMESTAMPTZ,
    execution_result JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_user_id    ON erp_action_audit(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON erp_action_audit(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action     ON erp_action_audit(action_type);

-- ─── Document registry (re-indexing/versioning — TAD v1.1 §4.1) ─────────────
CREATE TABLE IF NOT EXISTS document_registry (
    doc_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_file  TEXT UNIQUE NOT NULL,
    content_hash CHAR(64) NOT NULL,          -- sha256, để skip nếu không đổi
    version      VARCHAR(50),
    indexed_at   TIMESTAMPTZ DEFAULT NOW()
);

-- ─── LLM request log (phương án nhẹ song song LiteLLM UI — TAD §4.6) ─────────
CREATE TABLE IF NOT EXISTS llm_request_log (
    id          BIGSERIAL PRIMARY KEY,
    model       VARCHAR(100),
    caller      VARCHAR(100),                -- agent|mcp-odoo|report|...
    prompt      JSONB,
    response    TEXT,
    latency_ms  INTEGER,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_llmlog_created ON llm_request_log(created_at);

-- ─── MCP call log (schema port từ mcp_server addon → mcp.log) ────────────────
-- Mọi tool call qua MCP server ghi vào đây: access, permission_denied, rate_limit, error
CREATE TABLE IF NOT EXISTS mcp_call_log (
    id            BIGSERIAL PRIMARY KEY,
    event_type    VARCHAR(30) NOT NULL,   -- model_access|permission_denied|rate_limit|error|auth_failure
    caller        VARCHAR(100),           -- mcp-odoo|mcp-sqlserver|...
    user_id       INTEGER REFERENCES users(id),
    ip_address    VARCHAR(45),            -- IPv6-safe (size=45, lấy từ addon)
    api_key_used  BOOLEAN DEFAULT FALSE,
    tool_name     VARCHAR(100),           -- get_late_orders|search_orders|...
    model_name    VARCHAR(100),           -- sale.order|stock.quant|...
    operation     VARCHAR(20),            -- read|create|write|unlink
    record_ids    TEXT,                   -- "1,2,3"
    duration_ms   INTEGER,
    error_code    VARCHAR(10),
    error_message TEXT,                    -- truncate 10000 chars ở app layer
    request_data  TEXT,
    response_data TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mcp_log_event   ON mcp_call_log(event_type);
CREATE INDEX IF NOT EXISTS idx_mcp_log_caller  ON mcp_call_log(caller);
CREATE INDEX IF NOT EXISTS idx_mcp_log_created ON mcp_call_log(created_at);

-- Cleanup logs cũ (concept lấy từ addon cleanup_old_logs) — gọi qua cron Phase 2
CREATE OR REPLACE FUNCTION cleanup_mcp_logs(retain_days INT DEFAULT 30)
RETURNS INT AS $$
DECLARE deleted INT;
BEGIN
  DELETE FROM mcp_call_log WHERE created_at < NOW() - (retain_days || ' days')::INTERVAL;
  GET DIAGNOSTICS deleted = ROW_COUNT;
  RETURN deleted;
END;
$$ LANGUAGE plpgsql;
