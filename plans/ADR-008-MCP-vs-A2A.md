# ADR-008 — MCP vs A2A vs Hybrid cho ERP AI Assistant

**Date:** 2026-06-24
**Status:** Accepted
**Context:** Chốt hướng kiến trúc tổng thể sau khi vertical slice Phase 1 đã chạy + tham khảo side project A2A (TiktokResearch_A2A).
**Liên quan:** TAD §10 (Agent), §9 (MCP), ADR-006 (LangGraph+LlamaIndex), Phase 4 (Multi-Agent).

---

## 0. Trạng thái thực tế đã build (systematize)

| Thành phần | Trạng thái | Đã verify |
|---|---|---|
| Core infra (postgres 5433 / ollama / litellm) | ✅ chạy, healthy | docker compose ps |
| Odoo 19 + demo data | ✅ kết nối XML-RPC | discover + 24 SO/47 product |
| Odoo MCP server (SSE :8001) | ✅ 8 read tools + security/rate-limit/log | test 4/4 + audit log |
| LiteLLM gateway + Qwen3:8b | ✅ tool-calling tiếng Việt | smoke test PASS |
| FastAPI backend (OpenAI-compat) | ✅ /v1 chat + stream | test trực tiếp |
| Open WebUI | ✅ chat browser end-to-end | container → backend OK |
| **Kiến trúc hiện tại** | **Single-agent + MCP tools** | **3/3 câu nghiệp vụ đúng** |

**Đo được (quan trọng cho quyết định):** mỗi tool call Odoo ~2s; mỗi LLM round-trip qua Ollama queue tuần tự (NUM_PARALLEL=1, 1 GPU 8GB).

Use cases mục tiêu (TAD): (1) ERP chatbot Odoo + SQL Server; (2) Document RAG. SME, <20 concurrent user, 1 workstation.

---

## 1. Reframe: MCP và A2A KHÔNG phải lựa chọn loại trừ nhau

Đây là hiểu lầm cốt lõi cần gỡ. Hai protocol giải hai bài toán **vuông góc nhau**:

```
        A2A (ngang — agent ↔ agent)
        ┌──────────┬──────────┬──────────┐
        │ Agent A  │ Agent B  │ Agent C  │   ← điều phối, uỷ thác task
        └────┬─────┴────┬─────┴────┬─────┘
   MCP (dọc)│      MCP  │     MCP  │
        ┌───┴───┐  ┌────┴───┐ ┌────┴───┐
        │ tools │  │ tools  │ │ tools  │     ← agent ↔ công cụ/dữ liệu
        └───────┘  └────────┘ └────────┘
```

- **MCP** (Anthropic): chuẩn hoá **agent → tools/data**. Tích hợp *dọc*. → đây là tầng ta đã build (Odoo MCP).
- **A2A** (Google): chuẩn hoá **agent → agent** (discovery qua Agent Card, uỷ thác task, trao đổi artifact). Tích hợp *ngang*. → đây là cái side project TikTok dùng (Orchestra điều phối Retrieval/Script/Analyzer/Visualize).

→ "Hybrid" **đúng nghĩa** = dùng cả hai: A2A giữa các agent, mỗi agent dùng MCP cho tool của nó. Câu hỏi thật KHÔNG phải "MCP hay A2A" mà là: **project này có cần NHIỀU agent (A2A) hay 1 agent là đủ?**

---

## 2. Ba phương án cụ thể

| | Mô tả | Ví dụ |
|---|---|---|
| **A. Single-agent + MCP** | 1 LangGraph agent, routing nội bộ (ERP-read / ERP-write / RAG) bằng conditional edge. Tool qua MCP. | Hiện trạng đã chạy |
| **B. Multi-agent A2A** | Mỗi capability = 1 agent server riêng; 1 orchestrator điều phối qua A2A. | Side project TikTok |
| **C. Hybrid** | Single-agent giờ, nhưng tách module (ERP/RAG) thành subgraph có "đường nối A2A-ready" để sau split rẻ. | Khuyến nghị |

---

## 3. Yếu tố quyết định cho ĐÚNG project này

### 3.1 🔴 Latency trên 1 GPU 8GB — yếu tố sát thủ

Ollama chạy **tuần tự** (`NUM_PARALLEL=1`, 1 model/lần trên 8GB). Mọi LLM call xếp hàng chung 1 queue.

- **Single-agent**: 1 câu hỏi ≈ 2–3 LLM round-trip (chọn tool → [tool ~2s] → tổng hợp), tuần tự → ~6–15s.
- **A2A multi-agent**: orchestrator gọi LLM → uỷ thác sub-agent → sub-agent gọi LLM 1–2 lần → trả về → orchestrator tổng hợp (thêm 1 call). 3–4 agent ⇒ **8–12 LLM call tuần tự cho 1 câu hỏi** ⇒ 30–90s. **Không dùng được.**

Side project chạy được A2A vì gemini-2.5-flash là **cloud, song song, nhanh** — overhead A2A bị che. Local 1-GPU **không che được**: A2A nhân latency thay vì giảm.

### 3.2 Model nhỏ (Qwen3:8b) thích cấu trúc, ghét tầng nấc

8B đã bịa ngày, dính computed-field. Thêm tầng orchestrator phải tự "phân rã task → chọn agent → tổng hợp" = thêm chỗ cho 8B sai. Single-agent với routing rõ ràng (intent → nhánh) dễ kiểm soát hơn nhiều.

### 3.3 Quy mô không kích hoạt giá trị của A2A

Giá trị thật của A2A: scale từng agent độc lập, agent đa ngôn ngữ/đa team, tái dùng agent xuyên sản phẩm, marketplace agent. SME 1 workstation, 2 use case, 1 team → **không có cái nào áp dụng**.

### 3.4 Debug & vận hành

Single LangGraph = 1 graph, 1 state, routing deterministic, 1 chỗ debug. A2A = distributed tracing xuyên nhiều agent server, thêm failure mode (network giữa agent, discovery, version skew). Với team nhỏ → chi phí vận hành A2A không đáng.

### Bảng điểm (cho project này)

| Tiêu chí | A. Single+MCP | B. A2A | C. Hybrid |
|---|:--:|:--:|:--:|
| Latency trên 8GB/1-GPU | 🟢 | 🔴 | 🟢 |
| Hợp model 8B | 🟢 | 🔴 | 🟢 |
| Đúng quy mô SME | 🟢 | 🔴 | 🟢 |
| Dễ debug | 🟢 | 🟡 | 🟢 |
| Mở rộng tương lai | 🟡 | 🟢 | 🟢 |
| Effort tới production | 🟢 thấp | 🔴 cao | 🟢 thấp |

---

## 4. Quyết định

> **Chọn C (nghiêng mạnh A) — MCP-first single-agent, có seam A2A-ready, HOÃN A2A đến khi có trigger cụ thể.**

Cụ thể:

1. **Tầng tool = MCP** (giữ nguyên, đã chứng minh): Odoo MCP, sắp tới SQL Server MCP, Filesystem MCP (cho RAG Phase 2). Đây là chỗ MCP toả sáng.
2. **1 LangGraph agent với intent routing nội bộ**: `intent_router → {erp_read | erp_write(confirmation) | rag}` là các **subgraph/nhánh**, KHÔNG phải agent server riêng. Đạt được "hành vi chuyên biệt" của multi-agent mà KHÔNG trả giá process/latency của A2A.
3. **Thiết kế ERP và RAG thành module tách rời** (subgraph độc lập, interface rõ) → nếu sau này cần split thành A2A thì chỉ bọc 1 lớp server, không refactor lõi. Đây là phần "hybrid": **A2A-ready, chưa A2A**.
4. **Tool granularity** (ADR phụ): trong agent, dùng hybrid tool = vài generic guardrailed (`odoo_search`, `odoo_aggregate`) + vài tool nghiệp vụ cụ thể. (Quyết định riêng, không liên quan A2A.)

---

## 5. Khi nào MỚI nâng lên A2A (trigger rõ ràng)

Chỉ khi xuất hiện **agent thật sự độc lập về tài nguyên/vòng đời**:

- **Report Agent chạy cron** (TAD Phase 4): nặng, chạy nền, lịch riêng → tách ra hợp lý.
- **Khác phần cứng**: một agent cần GPU lớn / đẩy lên cloud trong khi agent khác chạy local.
- **Khác team sở hữu** hoặc tái dùng agent cho sản phẩm khác.
- **Concurrency tăng** đến mức cần nhiều LLM backend song song (lúc đó đã chuyển vLLM, nhiều GPU).

Trước các trigger này, A2A chỉ thêm latency + độ phức tạp, không thêm giá trị.

---

## 6. Kiến trúc khuyến nghị (chốt)

```
            Open WebUI / Next.js
                   │  /v1 (OpenAI-compat)
            ┌──────┴───────┐
            │ FastAPI back │
            └──────┬───────┘
                   │
        ┌──────────┴───────────┐   ← 1 LangGraph agent
        │     intent_router    │
        └───┬──────┬───────┬───┘
       erp_read  erp_write  rag        ← subgraph (A2A-ready seam)
            │   (confirm)    │
        ┌───┴────┐       ┌───┴────┐
        │  MCP   │       │ MCP FS │     ← tầng MCP (đã chứng minh)
        │ Odoo / │       │ + RAG  │
        │ SQLSrv │       │ engine │
        └────────┘       └────────┘
                   │
            LiteLLM → Ollama (Qwen3:8b, queue tuần tự)
```

A2A (Orchestra-style) = **lớp bọc tương lai** quanh các subgraph này, bật khi chạm trigger §5 — không build bây giờ.

---

## 7. Hệ quả

**Được:** latency thấp, hợp 8B + 8GB, dễ debug, effort thấp tới production, vẫn giữ đường nâng cấp.
**Mất / chấp nhận:** chưa có agent độc lập scale riêng (không cần ở quy mô này); khi cần A2A phải làm seam→server (đã thiết kế sẵn nên rẻ).
**Bài học lấy từ side project:** chỉ lấy ý tưởng **Retrieval_Agent** (generic tool + schema-in-prompt) cho tầng tool — KHÔNG bê bộ khung A2A/ADK (đó là Phase 4).
