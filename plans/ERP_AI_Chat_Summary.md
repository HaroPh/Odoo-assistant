# Tóm tắt: Thiết kế & Triển khai ERP AI Assistant

**Thời gian:** 2026  
**Phạm vi:** Từ đầu cuộc trò chuyện đến quyết định model selection

---

## 1. Bối cảnh & Mục tiêu

Thiết kế một **ERP AI Assistant on-premise** cho doanh nghiệp vừa và nhỏ, tập trung hai use case:

- **ERP Chatbot** — hỏi đáp tự nhiên trên dữ liệu Odoo CE (đơn hàng, tồn kho, khách hàng, nhà cung cấp) và thực hiện một số thao tác có kiểm soát (tạo báo giá, PO, hóa đơn, work order)
- **Document Intelligence RAG** — hỏi đáp trên tài liệu nội bộ (SOP, ISO, Work Instruction, Maintenance Manual)

**Nguyên tắc:** Local-first → On-premise-first → Open-source-first → Cloud-optional.

**Hạ tầng hiện có:** Windows workstation, Python 3.11, VS Code, uv, SQL Server Developer Edition.

---

## 2. Technical Architecture Document (TAD)

Toàn bộ thiết kế được đóng gói thành một TAD 15 chương, bao gồm:

### Kiến trúc tổng thể

Hệ thống chia thành 8 layer rõ ràng: Frontend → Backend (FastAPI) → Agent Orchestration (LangGraph) → MCP Layer → LLM Layer → RAG Layer → Vector DB → Storage. Mỗi layer có interface riêng biệt, có thể thay thế độc lập.

### Các quyết định kiến trúc chính (ADR)

| ADR | Quyết định | Lý do |
|-----|-----------|-------|
| ADR-001 | Open WebUI (MVP) → Next.js (Production) | Zero-config MVP, full control Production |
| ADR-002 | Qwen2.5:14b làm primary LLM | Tốt nhất tiếng Việt + tool calling trong local models |
| ADR-003 | Document-aware chunking (512–1024 tokens tùy loại) | Tránh context fragmentation |
| ADR-004 | pgvector (MVP) → Qdrant (Production) | Tận dụng PostgreSQL sẵn có, nâng cấp khi cần |
| ADR-005 | Confirmation Gate bắt buộc cho ERP write actions | AI không bao giờ tự execute write |
| ADR-006 | LangGraph (ERP Agent) + LlamaIndex (RAG Engine) | State machine cho ERP, ecosystem mạnh cho RAG |
| ADR-007 | LiteLLM Proxy làm LLM Gateway | Admin UI tường minh, model routing, virtual keys |

### Tech stack tóm tắt

- **LLM Runtime:** Ollama
- **LLM Model:** Qwen2.5:14b (MVP), Qwen2.5:32b (Production)
- **Embedding:** BGE-M3 + BGE-Reranker-v2-m3
- **Agent:** LangGraph
- **RAG:** LlamaIndex + Hybrid Search (Dense + BM25)
- **Vector DB:** pgvector → Qdrant
- **ERP:** Odoo CE 17 qua MCP Server
- **Monitoring:** LiteLLM + Langfuse + Prometheus + Grafana

### Roadmap 4 Phase

- **Phase 1** (3–4 tuần): ERP Read-Only Chatbot
- **Phase 2** (3–4 tuần): Document RAG Assistant
- **Phase 3** (4–6 tuần): ERP Action Agent (write + RBAC + Next.js)
- **Phase 4** (6–8 tuần): Multi-Agent Architecture

---

## 3. So sánh LLM Runtime Frameworks

Làm rõ sự khác biệt giữa các công cụ thường bị nhầm lẫn:

| Công cụ | Loại | Phù hợp khi nào |
|---------|------|-----------------|
| **LM Studio** | Desktop GUI để test model | Thử model cá nhân, không phải production |
| **Ollama** | Local model server, Docker-native | Dev + Production nhẹ, phù hợp project này |
| **vLLM** | Production inference engine, throughput cao | >15 concurrent users, cần Linux/WSL2 |
| **LMDeploy** | Production inference, tối ưu cho Qwen | Khi cần throughput cao với Qwen models |

**Kết luận:** Ollama là đúng và đủ cho Phase 1–3. Throughput của Ollama là queue tuần tự — chấp nhận được với SME <20 concurrent users. Migration sang vLLM/LMDeploy chỉ đổi 1 dòng `base_url` vì tất cả dùng OpenAI-compatible API.

---

## 4. Bổ sung LiteLLM Gateway (ADR-007)

Vấn đề đặt ra: Ollama chạy "âm thầm" — không có UI để xem request/response từng query, không thấy latency, token count theo từng caller. Đây là khoảng trống so với LM Studio.

**Giải pháp:** Thêm **LiteLLM Proxy** đứng giữa Agent Layer và Ollama:

- Admin UI tại `/ui` hiển thị real-time: request/response đầy đủ, latency, tokens/sec — tương đương "Logs tab" của LM Studio nhưng chạy như backend service
- Virtual API key per-caller: Agent, MCP Server, Open WebUI có key riêng → log tách biệt theo nguồn
- Model routing: một endpoint duy nhất route đến Qwen3:8b, Qwen3:4b, hoặc fallback
- Self-hosted, Docker-native, OpenAI-compatible

**Phân biệt hai tầng observability:**
- **LiteLLM:** "Request thô gửi model là gì, trả lời gì, mất bao lâu"
- **Langfuse:** "Agent suy luận thế nào, gọi tool nào, RAG retrieve chunk nào"

---

## 5. Điều chỉnh Model Selection cho RTX 5060 Ti 8GB

**Phát hiện quan trọng từ Docker setup report:**

- GPU: RTX 5060 Ti, VRAM: **8GB**
- Docker GPU passthrough: **OK**
- CUDA 13.1: **OK**

**Vấn đề:** Qwen2.5:14b (đề xuất ban đầu trong TAD) cần 9–10GB VRAM với Q4_K_M quantization → **không chạy được** trên 8GB.

**Điều chỉnh model lineup:**

| Slot | Model cũ (TAD) | Model mới (thực tế) | VRAM |
|------|---------------|---------------------|------|
| Primary | Qwen2.5:14b | **Qwen3:8b** | ~5.5GB ✓ |
| Fast | Qwen2.5:7b | **Qwen3:4b** | ~3.5GB ✓ |
| Fallback | — | **Qwen2.5:7b** | ~5.0GB ✓ |

**Cấu hình Ollama quan trọng cho 8GB:**
- `OLLAMA_MAX_LOADED_MODELS: 1` — chỉ giữ 1 model trong VRAM
- `OLLAMA_NUM_PARALLEL: 1` — xử lý tuần tự, không batch
- `OLLAMA_NUM_GPU: 1` — force GPU, không CPU fallback

---

## 6. Tóm tắt trạng thái cuối

Tính đến điểm dừng này, đã hoàn thành:

- ✅ TAD đầy đủ 15 chương (file `ERP_AI_Assistant_TAD_v1.0.md`)
- ✅ Kiến trúc xác định, các ADR được ghi lại
- ✅ Docker setup verify thành công (GPU passthrough OK)
- ✅ Model selection điều chỉnh phù hợp RTX 5060 Ti 8GB
- ✅ LiteLLM Gateway được thêm vào kiến trúc (ADR-007)
- 🔄 Bước tiếp theo: Deploy stack + viết LangGraph Agent

**Thứ tự ưu tiên ngay:**
0. **Đọc `ERP_AI_Assistant_TAD_v1.1_Refinements.md`** — chốt fix trước khi code (LangGraph interrupt, BM25 thật, VRAM CPU-embedding, SQL guard)
1. Tạo thư mục project + `.env` (PIN image versions, không `:latest`/`main`)
2. Pull Docker images + `qwen3:8b` (verify tag tồn tại; test `/no_think` tool-calling)
3. Khởi động core stack (postgres, ollama, litellm, backend, open-webui)
4. Verify bằng `health-check.sh`
5. Tạo user admin + **SQL read-only login** (`ai_readonly`, chỉ `db_datareader`)
6. `uv add` deps còn thiếu (llama-index, FlagEmbedding, underthesea, sqlparse, slowapi, langgraph-checkpoint-postgres)
7. Viết LangGraph ERP Agent (AsyncPostgresSaver checkpointer + thread_id ngay từ Phase 1)
