# ERP AI Assistant — TAD v1.1
## Corrections & Implementation Refinements

**Version:** 1.1 (companion to TAD v1.0)
**Date:** 2026-06-23
**Status:** Authoritative — nơi nào v1.1 mâu thuẫn với v1.0 thì **v1.1 thắng**
**Scope:** Sửa các lỗi kỹ thuật + đồng bộ thực tế hạ tầng (RTX 5060 Ti 8GB, langgraph 1.x)

> Đọc cùng `ERP_AI_Assistant_TAD_v1.0.md`. Document này **không thay thế** TAD v1.0;
> nó sửa những chỗ sai/thiếu và chốt lại chi tiết implementation trước khi viết code Phase 1.

---

## 0. Version reality — môi trường thực tế đã cài

Kiểm tra `.venv` thực tế (không phải giả định trong TAD):

| Package | TAD giả định | Thực tế đã cài | Ghi chú |
|---|---|---|---|
| langgraph | "0.2+" | **1.1.10** | Major version 1.x — API `interrupt()`/`Command` |
| langchain / core | — | 1.2.18 / 1.4.0 | LangChain 1.x |
| langgraph-checkpoint | — | 4.1.0 | OK |
| **langgraph-checkpoint-postgres** | dùng `PostgresSaver` | **CHƯA CÀI** | Phải `uv add langgraph-checkpoint-postgres` |
| torch / transformers | — | đã có | OK cho BGE-M3 |
| fastapi | có | đã có | OK |
| psycopg2 | — | đã có | OK |
| pyodbc | — | đã có | OK cho SQL Server MCP |
| llama-index | có | **CHƯA** | `uv add llama-index ...` |
| litellm | có | **CHƯA** | container, không cần trong venv |
| FlagEmbedding | có | **CHƯA** | BGE-M3 + reranker |
| sentence-transformers | có | **CHƯA** | |
| underthesea | có | **CHƯA** | Vietnamese tokenizer |
| odoorpc | có | **CHƯA** | |
| mcp | có | **CHƯA** | |
| sqlparse | — | **CHƯA** | cần cho SQL guard (mục 3.1) |
| rank-bm25 / bm25s | — | **CHƯA** | cần cho BM25 thật (mục 2.2) |

**Hệ quả:** mọi snippet trong TAD v1.0 viết theo LangGraph 0.2. Với langgraph 1.x phải dùng `interrupt()` + `Command(resume=...)` (mục 2.1). Code confirmation flow trong TAD §10.3 **không chạy** trên bản đã cài.

---

## 1. Issue tracker — tổng hợp từ review

| # | Mức | Vấn đề | Mục fix | Chặn phase nào |
|---|---|---|---|---|
| 1 | ✅ | LangGraph confirmation gate dùng API không tồn tại (`__confirmation_resume__`) — **đã sửa: `interrupt()` trong write_planner + `chat()` resume (commit 1db2427)** | 2.1 | ~~Phase 3~~ DONE |
| 2 | 🔴 | `ts_rank` bị gọi nhầm là BM25 → retrieval kém | 2.2 | Phase 2 |
| 3 | 🔴 | VRAM 8GB không đủ cho LLM + embedding cùng GPU | 2.3 | Setup |
| 4 | 🟡 | SQL guard `startswith("SELECT")` quá yếu | 3.1 | Phase 1 go-live |
| 5 | 🟡 | TAD (Qwen2.5:14b) ≠ Summary (Qwen3:8b); thiếu lưu ý thinking-mode | 3.2 | Trước khi team đọc |
| 6 | 🟡 | `VietnamTokenizer()` được gọi nhưng không định nghĩa | 3.3 | Phase 2 |
| 7 | 🟡 | Resource contention ~12–15 container trên 1 workstation | 3.4 | Phase 2 |
| 8 | 🟡 | Không có per-request timeout / rate limit trên FastAPI | 3.5 | Phase 1 |
| 9 | 🟡 | LiteLLM image dùng tag trôi `main-stable` | 3.6 | Setup |
| 10 | 🟢 | Thiếu document re-indexing/versioning workflow | 4.1 | Phase 2 |

---

## 2. Critical fixes

### 2.1 — LangGraph confirmation flow (langgraph 1.x, `interrupt()` + `Command`)

**Sai trong TAD §10.3:** dùng `add_edge("confirmation_gate", END)` rồi `add_conditional_edges("__confirmation_resume__", ...)`. Node ma `__confirmation_resume__` không tồn tại; không có cơ chế "resume sau khi human confirm".

**Đúng (langgraph 1.x):** dùng `interrupt()` *bên trong* node để pause graph và surface preview ra client; client gọi lại bằng `Command(resume=<decision>)`. Checkpointer **bắt buộc** (state phải persist giữa 2 lần invoke).

```python
# backend/src/agents/erp_agent.py
from typing import Literal
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

async def write_action_planner(state: ERPAgentState) -> dict:
    """Trích xuất + validate tham số write action (chưa execute)."""
    action_plan = await extract_write_action(state["messages"][-1]["content"])
    validated = await validate_action_params(action_plan)   # check partner/product tồn tại
    return {"pending_action": validated, "requires_confirmation": True}

async def confirmation_gate(state: ERPAgentState) -> dict:
    """
    PAUSE graph tại đây. `interrupt(payload)` ném payload ra ngoài cho client
    (hiện preview). Khi client gọi graph.invoke(Command(resume=decision), ...)
    thì hàm chạy LẠI từ đầu node và interrupt() trả về `decision`.
    """
    action = state["pending_action"]
    decision = interrupt({
        "type": "confirmation_request",
        "preview": format_action_preview(action),   # bảng xác nhận cho user
        "action_id": action["id"],
    })
    # decision = giá trị client truyền vào Command(resume=...)
    return {"confirmation_received": bool(decision and decision.get("confirmed"))}

async def write_executor(state: ERPAgentState) -> dict:
    assert state["confirmation_received"], "Confirmation required"
    result = await mcp_client.execute_write(state["pending_action"])
    await audit_logger.log(user_id=state["user_id"],
                           action=state["pending_action"], result=result)
    return {"tool_results": [result], "pending_action": None,
            "requires_confirmation": False}

def after_confirm(state: ERPAgentState) -> Literal["execute", "cancel"]:
    return "execute" if state["confirmation_received"] else "cancel"

# --- graph ---
builder = StateGraph(ERPAgentState)
builder.add_node("write_planner", write_action_planner)
builder.add_node("confirmation_gate", confirmation_gate)
builder.add_node("write_executor", write_executor)
builder.add_node("response_generator", response_generator)

builder.add_edge("write_planner", "confirmation_gate")
builder.add_conditional_edges("confirmation_gate", after_confirm,
                              {"execute": "write_executor", "cancel": "response_generator"})
builder.add_edge("write_executor", "response_generator")
builder.add_edge("response_generator", END)
```

**Checkpointer** — `PostgresSaver` chưa cài (mục 0). Cài và setup 1 lần:

```bash
uv add langgraph-checkpoint-postgres psycopg[binary,pool]
```

```python
# Async, dùng connection pool cho app long-lived (KHÔNG dùng from_conn_string context-manager
# kiểu một-lần như ví dụ docs — nó đóng connection khi thoát with-block).
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

pool = AsyncConnectionPool(conninfo=DATABASE_URL, max_size=20, open=False,
                           kwargs={"autocommit": True, "prepare_threshold": 0})
await pool.open()
checkpointer = AsyncPostgresSaver(pool)
await checkpointer.setup()          # chạy 1 lần: tạo bảng checkpoint
graph = builder.compile(checkpointer=checkpointer)
```

**Vòng đời gọi từ backend (WebSocket handler):**

```python
config = {"configurable": {"thread_id": session_id}}

# Lần 1: chạy tới interrupt → trả payload xác nhận ra frontend
result = await graph.ainvoke({"messages": [user_msg]}, config=config)
if "__interrupt__" in result:
    payload = result["__interrupt__"][0].value      # gửi preview cho user, chờ click
    ...

# Lần 2 (sau khi user bấm Xác nhận / Huỷ):
result = await graph.ainvoke(
    Command(resume={"confirmed": True}),             # hoặc {"confirmed": False}
    config=config,                                   # cùng thread_id → resume đúng chỗ
)
```

> Timeout 5 phút (TAD §8.4 quy tắc 3): xử lý ở **application layer**, không ở graph — lưu `confirmation_expires_at` khi tạo interrupt; nếu user resume sau hạn thì `write_executor` reject. Đừng dựa vào graph tự hết hạn.

#### ✅ Đã implement (Phase 3, commit 1db2427)

Cơ chế interrupt/resume đã chạy end-to-end, **nhưng khác plan ở tầng client** vì MVP dùng **Open WebUI (chat)** chứ không phải Next.js có nút bấm:

| Khía cạnh | Plan gốc (§2.1, frontend buttons) | Đã implement (MVP, chat text) |
|---|---|---|
| Vị trí `interrupt()` | node `confirmation_gate` riêng | gọi *trong* `erp_write_planner` ([nodes.py](../backend/src/agents/nodes.py)) |
| Client gửi quyết định | nút "Xác nhận/Huỷ" → `Command(resume={"confirmed": bool})` | user gõ "có/không" → classifier → `Command(resume=<bool>)` |
| Phân loại câu trả lời | không cần (button = nhị phân) | [confirmation.py](../backend/src/agents/confirmation.py): keyword fast-path + LLM fallback → `CONFIRM/CANCEL/UNCLEAR` |
| Câu trả lời mơ hồ | n/a | `UNCLEAR` → hỏi lại, **không** đoán (fail-safe deny) |
| State fields | `requires_confirmation` / `confirmation_received` | `pending_action` / `confirmed` (đã có sẵn từ Task 5) |

Wiring nằm trong `ERPAgent.chat()` ([erp_agent.py](../backend/src/agents/erp_agent.py)): `aget_state()` để phát hiện thread đang parked → resume; nếu không thì chạy mới và surface `__interrupt__`. **Yêu cầu `thread_id` ổn định** (client phải gửi `session_id`).

**Còn nợ (chưa làm trong Phase 3):**
- Timeout `confirmation_expires_at` (đoạn trên) — `write_executor` chưa reject theo hạn.
- `WRITE_ACTIONS_ENABLED` vẫn `false`: `erp_write_executor` còn là STUB, chưa có MCP write tool thật vào Odoo.
- Message accumulation trên stable thread (N-1): non-resume turn vẫn truyền full history → checkpointer append trùng. Chưa ảnh hưởng vòng confirm (resume không truyền messages).

---

### 2.2 — Hybrid retrieval: BM25 thật, bỏ `ts_rank` "giả BM25"

**Sai trong TAD §7.3:** `ts_rank(to_tsvector('simple', ...), plainto_tsquery(...))` **không phải BM25** — đó là thuật toán ranking riêng của Postgres FTS (tf bão hoà kiểu khác, không có k1/b của Okapi BM25). Gọi nó là BM25 gây hiểu nhầm về chất lượng + tham số tuning.

**Chốt 2 đường rõ ràng theo phase:**

**MVP (pgvector):** dense = pgvector; lexical = BM25 in-memory bằng LlamaIndex `BM25Retriever` (backend `bm25s`/`rank_bm25`) với tokenizer tiếng Việt (mục 3.3). KHÔNG dùng `ts_rank` làm "BM25".

```bash
uv add llama-index llama-index-retrievers-bm25 bm25s rank-bm25 \
       llama-index-vector-stores-postgres
```

```python
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.retrievers import QueryFusionRetriever

bm25 = BM25Retriever.from_defaults(
    nodes=nodes, similarity_top_k=20, tokenizer=vi_tokenizer,  # mục 3.3
)
hybrid = QueryFusionRetriever(
    retrievers=[index.as_retriever(similarity_top_k=20), bm25],
    similarity_top_k=20, mode="reciprocal_rerank", num_queries=1, use_async=True,
)
# sau đó: BGE reranker v2-m3 → top-5 (chạy CPU, mục 2.3)
```

> `ts_rank` của Postgres vẫn dùng được làm **prefilter rẻ** (lọc theo keyword trước khi vector search trên corpus lớn), nhưng đừng coi điểm của nó là tín hiệu lexical chính.

**Production (Qdrant):** dùng **sparse vector thật của BGE-M3** (lexical weights) — đây mới là điều TAD §5.2 hứa ("Sparse Retrieval"). Qdrant hỗ trợ sparse native:

```python
out = bge_m3.encode(text, return_dense=True, return_sparse=True)
dense = out["dense_vecs"]
sparse = out["lexical_weights"]          # {token_id: weight} → Qdrant SparseVector
# query Qdrant với cả 2 named vectors ("dense" + "sparse") rồi RRF fusion server-side
```

→ Production không cần `BM25Retriever` in-memory nữa; sparse của BGE-M3 thay thế, đồng nhất tokenizer với dense.

**Quyết định:** ADR-004 giữ nguyên path pgvector→Qdrant, **bổ sung**: "lexical signal = bm25s+underthesea (MVP) → BGE-M3 sparse (Qdrant prod)". Gạch bỏ `ts_rank`-as-BM25 trong hàm `hybrid_search()` của TAD §7.3.

---

### 2.3 — VRAM budget cho RTX 5060 Ti 8GB (BGE-M3 + reranker = CPU)

**Vấn đề:** nếu LLM và embedding/reranker cùng nằm GPU thì tràn 8GB.

| Thành phần | Nếu GPU | Quyết định |
|---|---|---|
| Qwen3:8b Q4_K_M (KV cache) | ~5.5–6.5GB | **GPU** (primary) |
| BGE-M3 fp16 | ~1.1GB | **CPU** |
| BGE-reranker-v2-m3 | ~1.1GB | **CPU** |
| Buffer an toàn | — | giữ ≥1GB GPU trống |

→ **Chốt: embedding + reranker chạy CPU.** Query-time embed 1 câu trên CPU ~50–150ms, reranker top-20 ~100–300ms — chấp nhận được. Ingestion batch trên CPU chậm hơn nhưng là offline.

```python
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.postprocessor.flag_embedding_reranker import FlagEmbeddingReranker
from llama_index.core import Settings

Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-m3", device="cpu")
reranker = FlagEmbeddingReranker(top_n=5, model="BAAI/bge-reranker-v2-m3", use_fp16=False)
# FlagEmbedding reranker → ép CPU bằng cách không set CUDA / device="cpu" tuỳ API version
```

Ollama (1 model/lần, không CPU-fallback ngầm):

```yaml
ollama:
  environment:
    OLLAMA_MAX_LOADED_MODELS: "1"     # 8GB chỉ giữ 1 model
    OLLAMA_NUM_PARALLEL: "1"          # tuần tự, không batch (tránh KV cache phình)
    OLLAMA_KEEP_ALIVE: "30m"
```

> Nếu muốn embedding trên GPU để nhanh hơn: phải hạ primary LLM xuống **Qwen3:4b** (~3.5GB) để chừa chỗ. Với SME read-heavy, CPU-embedding + Qwen3:8b-GPU là cân bằng tốt hơn.

**✅ ĐO THỰC TẾ trên máy (2026-06-23, RTX 5060 Ti 8GB):**
- qwen3:8b load **100% GPU**, footprint **5.6GB**, ở context **4096** → còn **785 MiB VRAM trống**.
- ⟹ Embedding/reranker trên GPU **bất khả thi** — CPU là bắt buộc, không phải tùy chọn.
- ⟹ **Trần context thực tế ~8K**; 16K rủi ro OOM (chặt hơn ước tính ban đầu). Cấu hình `num_ctx` thận trọng, đo lại khi thêm tool-result + RAG context vào prompt.

---

## 3. Medium fixes

### 3.1 — SQL Server MCP: read-only login + parse AST (không chỉ `startswith`)

**Sai trong TAD §9.2:** `if not query.upper().startswith("SELECT")` — vượt qua được bằng `SELECT 1; DROP TABLE x`, CTE ghi, `; EXEC`, comment-trick, v.v.

**Phòng thủ 2 lớp — lớp 1 là chính:**

1. **Read-only DB principal (kiểm soát thật):** tạo SQL login chỉ `db_datareader`. Dù prompt injection có lọt, DB từ chối mọi ghi.
```sql
CREATE LOGIN ai_readonly WITH PASSWORD = '***';
CREATE USER ai_readonly FOR LOGIN ai_readonly;
ALTER ROLE db_datareader ADD MEMBER ai_readonly;   -- KHÔNG add db_datawriter/ddladmin
```
2. **Parse + validate bằng `sqlparse` (defense in depth):**
```python
import sqlparse
from sqlparse.tokens import DML, Keyword

FORBIDDEN = {"INSERT","UPDATE","DELETE","DROP","ALTER","CREATE","TRUNCATE",
             "GRANT","REVOKE","EXEC","EXECUTE","MERGE","INTO","SP_","XP_"}

def assert_safe_select(sql: str) -> None:
    stmts = sqlparse.parse(sql)
    if len(stmts) != 1:
        raise ValueError("Chỉ cho phép 1 câu lệnh")
    stmt = stmts[0]
    if stmt.get_type() != "SELECT":
        raise ValueError(f"Chỉ cho phép SELECT, nhận: {stmt.get_type()}")
    for tok in stmt.flatten():
        if tok.ttype in (DML, Keyword) and tok.value.upper() in FORBIDDEN:
            raise ValueError(f"Từ khoá bị cấm: {tok.value}")
```
3. Vẫn giữ `max_rows`, parameterized values, và timeout truy vấn (vd `cursor.execute` với `SET LOCK_TIMEOUT`).

```bash
uv add sqlparse
```

---

### 3.2 — Đồng bộ model: Qwen3, và lưu ý thinking-mode

TAD §4 vẫn viết Qwen2.5:14b khắp nơi; Summary đã chốt Qwen3 cho 8GB. **Chốt thống nhất:**

| Slot | Model | VRAM | Dùng cho |
|---|---|---|---|
| Primary | **qwen3:8b** (Q4_K_M) | ~5.5GB | 80% traffic: ERP query, RAG answer, tool calling |
| Fast | **qwen3:4b** | ~3.5GB | intent routing, câu ngắn, latency <3s |
| Fallback | **qwen2.5:7b** | ~5.0GB | khi Qwen3 lỗi tool-call format |

**Lưu ý kỹ thuật Qwen3 (khác Qwen2.5 — phải test):**
- Qwen3 có **hybrid thinking**. Cho ERP tool-calling nên **TẮT thinking** để giảm latency và tránh `<think>` lẫn vào output: dùng `/no_think` trong prompt, hoặc param `think: false` (Ollama bản mới). RAG reasoning phức tạp có thể bật.
- Tool-call template Qwen3 khác Qwen2.5 → **chạy lại bộ eval tool-calling** (TAD §12.3) khi đổi, đừng giả định parity.
- Context: Qwen3:8b native **32K** (không phải 128K như dòng Qwen2.5 trong bảng TAD §4.1) — YaRN mở rộng được nhưng tốn VRAM. Với 8GB giữ ≤16K context.
- Xác nhận tag thực tế: `ollama pull qwen3:8b` (đừng hard-code trước khi pull thành công). **✅ Đã pull OK (5.2GB), tool-calling tiếng Việt PASS qua LiteLLM.**
- **⚠️ Model KHÔNG biết ngày hiện tại** — smoke test cho thấy nó bịa `date_from: 2023-10-01` (thực tế 2026). **Bắt buộc inject `{datetime}` vào system prompt** (TAD §14.3) và **validate mọi tham số ngày** do LLM sinh trước khi gọi Odoo/SQL (TAD §16.3 sai lầm #8 — nay có bằng chứng cụ thể).

> Cập nhật bảng TAD §4.1/§4.5/§16.1 sang Qwen3, hoặc thêm dòng "v1.0 viết Qwen2.5 — thực thi theo bảng v1.1 này".

---

### 3.3 — Vietnamese tokenizer wrapper (định nghĩa `vi_tokenizer`)

TAD §6.4 gọi `VietnamTokenizer()` nhưng không định nghĩa. Đây là 1 hàm callable truyền vào `BM25Retriever`:

```bash
uv add underthesea
```
```python
# backend/src/rag/vi_tokenizer.py
from functools import lru_cache
from underthesea import word_tokenize

@lru_cache(maxsize=4096)
def _seg(text: str) -> tuple[str, ...]:
    # word_tokenize trả list token đã ghép từ ghép tiếng Việt ("máy_CNC")
    return tuple(word_tokenize(text.lower()))

def vi_tokenizer(text: str) -> list[str]:
    toks = [t.replace(" ", "_") for t in _seg(text)]
    return [t for t in toks if t.strip()]      # bỏ token rỗng/space
```
- Dùng chung `vi_tokenizer` cho cả index-time và query-time của BM25 (bắt buộc nhất quán).
- `lru_cache` giảm chi phí cho query lặp; ingestion batch không cần cache lớn.

---

### 3.4 — Resource sizing + tiered compose (đừng bật hết 1 lúc)

Full stack ~12–15 container. Trên 1 workstation 8GB-VRAM, RAM là nút thắt thật.

| Mức | Container | RAM ước tính |
|---|---|---|
| **core** (Phase 1) | postgres, ollama, litellm, backend, open-webui, mcp-odoo, mcp-sqlserver, mcp-filesystem | ~12–16GB |
| **+rag** (Phase 2) | + qdrant (prod) | +2–4GB |
| **+monitoring** | + langfuse, prometheus, grafana | +3–5GB |

→ **Yêu cầu tối thiểu xác nhận trước Phase 2: 32GB system RAM.** Dùng compose profiles để bật theo nhu cầu (TAD đã có `profiles: [erp|monitoring]`, mở rộng thêm):
```bash
docker compose up -d                       # core
docker compose --profile monitoring up -d  # khi cần xem trace
```
Đặt `mem_limit` cho langfuse/grafana để chúng không tranh RAM với Ollama khi cùng bật.

---

### 3.5 — Per-request timeout + rate limit trên FastAPI

Một LLM request dài có thể chiếm queue (Ollama tuần tự). Thêm từ Phase 1:

```bash
uv add slowapi
```
```python
# timeout mỗi LLM call (đừng để treo vô hạn)
import asyncio
async def call_llm_guarded(coro, timeout_s: int = 60):
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        raise HTTPException(504, "LLM timeout — thử lại với câu ngắn hơn")

# rate limit per-user (slowapi) — chống 1 user spam khoá queue
from slowapi import Limiter
from slowapi.util import get_remote_address
limiter = Limiter(key_func=get_remote_address)
# @limiter.limit("10/minute") trên endpoint /chat
```
LiteLLM cũng đặt được `timeout` và `max_parallel_requests` per virtual key — tận dụng ở gateway thay vì code tay nếu muốn.

---

### 3.6 — Pin version các image hay breaking

TAD dùng tag trôi → build không reproducible. Pin:

```yaml
litellm: ghcr.io/berriai/litellm:v1.61.3-stable   # thay main-stable (đổi sang tag thực tế khi pull)
open-webui: ghcr.io/open-webui/open-webui:v0.6.5  # thay :main
qdrant: qdrant/qdrant:v1.12.4                      # TAD ghi v1.9.0 (cũ) — bump + pin
langfuse: langfuse/langfuse:2                      # OK nếu giữ major 2, tốt hơn nếu pin patch
ollama: ollama/ollama:0.5.x                         # thay :latest
```
> Xác nhận tag tồn tại trước khi commit (`docker manifest inspect`). Đừng để `:latest`/`:main` trong file production.

---

## 4. Bổ sung còn thiếu

### 4.1 — Document re-indexing / versioning workflow (Phase 2)

TAD có field `version` trong metadata nhưng **không có logic** xử lý khi tài liệu đổi (SOP v2.1 → v2.2). Chốt cơ chế **content-hash + invalidation**:

```python
import hashlib

def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

async def ingest_document(path: str, doc_meta: dict):
    new_hash = file_hash(path)
    row = await db.fetchone(
        "SELECT content_hash FROM document_registry WHERE source_file=$1", path)

    if row and row["content_hash"] == new_hash:
        return "unchanged"                       # idempotent: skip

    doc_id = row["doc_id"] if row else uuid4()
    # 1) Xoá toàn bộ chunk cũ của doc_id (atomic trong transaction)
    await db.execute("DELETE FROM document_chunks WHERE doc_id=$1", doc_id)
    # 2) Re-chunk + re-embed + insert chunk mới
    await embed_and_store(path, doc_id, doc_meta)
    # 3) Upsert registry (bump version, lưu hash mới)
    await db.execute("""
        INSERT INTO document_registry(doc_id, source_file, content_hash, version, indexed_at)
        VALUES ($1,$2,$3,$4,NOW())
        ON CONFLICT (source_file) DO UPDATE
          SET content_hash=$3, version=$4, indexed_at=NOW()
    """, doc_id, path, new_hash, doc_meta["version"])
    return "reindexed"
```

- **Trigger:** cron quét `D:\Documents` so hash (đơn giản, đủ cho SME), hoặc watchdog filesystem event.
- **Atomicity:** delete + insert trong 1 transaction để không có khoảng trống "tài liệu biến mất".
- **Audit:** log mỗi reindex (ai/khi nào/version cũ→mới) — tái dùng bảng audit.
- Bảng `document_registry` mới: `(doc_id, source_file UNIQUE, content_hash, version, indexed_at)`.

---

## 5. Tech stack đã sửa (chốt)

```
LLM Runtime : Ollama (MAX_LOADED_MODELS=1, NUM_PARALLEL=1)  → vLLM khi >15 user
LLM Models  : qwen3:8b (primary) / qwen3:4b (fast) / qwen2.5:7b (fallback)   [thinking OFF cho tool-calling]
Embedding   : BAAI/bge-m3                 → device=CPU
Reranker    : BAAI/bge-reranker-v2-m3     → device=CPU
Gateway     : LiteLLM (pin v1.x.y, không main-stable)
Agent       : LangGraph 1.1.x  (interrupt()/Command — KHÔNG dùng pattern TAD §10.3)
Checkpoint  : langgraph-checkpoint-postgres (AsyncPostgresSaver + psycopg pool)  ← phải cài thêm
RAG         : LlamaIndex
Lexical     : bm25s + underthesea (MVP, vi_tokenizer)  →  BGE-M3 sparse trên Qdrant (prod)
Vector DB   : pgvector (MVP)  →  Qdrant (prod, sparse native)
ERP         : Odoo CE 17 (odoorpc, XML-RPC)
SQL Server  : pyodbc + read-only login + sqlparse guard
Backend     : FastAPI + slowapi (rate limit) + asyncio.wait_for (LLM timeout)
Auth        : JWT (PyJWT)
Monitoring  : LiteLLM UI (raw I/O) + Langfuse (agent trace) + Prometheus/Grafana
```

**Dependencies cần `uv add` ngay (chưa có trong venv):**
```bash
uv add llama-index llama-index-retrievers-bm25 llama-index-vector-stores-postgres \
       llama-index-embeddings-huggingface llama-index-postprocessor-flag-embedding-reranker \
       FlagEmbedding sentence-transformers bm25s rank-bm25 underthesea \
       odoorpc mcp sqlparse slowapi \
       langgraph-checkpoint-postgres "psycopg[binary,pool]"
```

---

## 6. Refined Phase 1 plan (fixes đã baked-in)

**Mục tiêu Phase 1 không đổi:** ERP read-only chatbot tiếng Việt trên Odoo + SQL Server.
**Khác biệt:** các lỗi 🔴/🟡 ở trên được xử lý ngay trong khi dựng, không để nợ.

| Ngày | Việc | Fix liên quan |
|---|---|---|
| 1–3 | Infra: docker-compose core (pin versions), bind-mount `D:/ai-data`, `.env` từ `.env.example` | 3.6 |
| 3 | `ollama pull qwen3:8b qwen3:4b qwen2.5:7b`; verify tag + tool-calling + `/no_think` | 3.2, 2.3 |
| 4 | Ollama env 8GB (MAX_LOADED_MODELS=1, NUM_PARALLEL=1); LiteLLM gateway + virtual keys | 2.3, 3.6 |
| 5–8 | Odoo MCP Server (read-only: orders/inventory/customers/suppliers) | — |
| 9–10 | SQL Server MCP: **read-only login + sqlparse guard** + parameterized | 3.1 |
| 11–14 | LangGraph ERP Agent (intent→read→response); AsyncPostgresSaver checkpointer | 0, 2.1* |
| 15–17 | FastAPI: JWT + **slowapi rate limit + LLM timeout**; audit log read actions từ Day 1 | 3.5 |
| 18 | Open WebUI tích hợp qua backend `/v1` | — |
| 19–20 | Test E2E tiếng Việt ("Đơn hàng nào đang trễ?"), verify audit log | — |

\* Confirmation gate (interrupt) **chỉ cần khi có write** → để Phase 3; nhưng dựng checkpointer + thread_id ngay Phase 1 để Phase 3 chỉ thêm node, không refactor.

**Definition of Done Phase 1:**
- [ ] Hỏi tiếng Việt → trả lời đúng từ Odoo & SQL Server, có streaming.
- [ ] SQL Server MCP: thử `SELECT 1; DROP TABLE x` → **bị từ chối** (cả guard lẫn login).
- [ ] Mọi query (read) ghi `erp_action_audit`.
- [ ] LiteLLM UI thấy request/response/tokens-per-sec theo từng caller.
- [ ] 1 user spam 20 req → bị rate-limit, queue không kẹt.

---

## 7. Quick-start checklist (cập nhật)

```
☐ WSL2 + Docker Desktop (WSL2 backend) + NVIDIA Container Toolkit
☐ Xác nhận: 32GB RAM (cho Phase 2), RTX 5060 Ti 8GB, CUDA OK
☐ mkdir D:/ai-data/{ollama,qdrant,postgres}
☐ .env.example → .env (đổi hết change_me; thêm LITELLM_*, *_KEY)
☐ PIN image versions trong compose (litellm/open-webui/qdrant/ollama) — không :latest/:main
☐ docker compose up -d postgres ollama litellm
☐ ollama pull qwen3:8b   (verify tag tồn tại trước)
☐ ollama pull qwen3:4b qwen2.5:7b
☐ uv add <deps mục 5>   (llama-index, FlagEmbedding, underthesea, sqlparse, slowapi,
                          langgraph-checkpoint-postgres, psycopg[pool] ...)
☐ python -c "from FlagEmbedding import BGEM3FlagModel; print('BGE-M3 OK')"   (chạy CPU)
☐ python -c "from langgraph.types import interrupt, Command; print('interrupt OK')"
☐ Tạo SQL read-only login (ai_readonly, chỉ db_datareader)
☐ docker compose up -d backend mcp-odoo mcp-sqlserver mcp-filesystem open-webui
☐ http://localhost:4000/ui  (LiteLLM — kiểm tra log per-caller)
☐ http://localhost:3000      (Open WebUI) → "Tồn kho hiện tại là bao nhiêu?"
☐ Verify erp_action_audit có bản ghi
☐ (Phase 2) docker compose --profile monitoring up -d
```

---

## 8. Việc cần quyết / xác nhận trước khi code

1. **RAM workstation = 32GB?** Nếu chỉ 16GB → không chạy nổi monitoring + qdrant cùng lúc; phải tách máy hoặc giảm scope Phase 2.
2. **Qwen3 vs Qwen2.5 cho tool-calling:** chạy eval ngắn (10 ERP queries) ngay sau khi pull, chốt primary dựa trên kết quả thật — không chốt trên giấy.
3. **ParadeDB (`pg_search`) cho BM25 trong Postgres?** Nếu muốn BM25 thật ngay trên pgvector (thay bm25s in-memory) thì đổi image `pgvector/pgvector:pg16` → `paradedb/paradedb` (bundle cả pgvector + pg_search). Trade-off: image nặng hơn. Mặc định plan này dùng bm25s in-memory cho MVP → đủ và nhẹ.

---

*Companion to TAD v1.0 — cập nhật sau khi chốt mục 8.*
