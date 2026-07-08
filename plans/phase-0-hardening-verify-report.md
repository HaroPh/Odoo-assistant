# Phase 0 — Hardening-Verify: báo cáo cổng (M1 + M2)

**Date:** 2026-07-08
**Status:** ✅ Cổng ĐẠT — mở đường A1/A2 **và A3**. Quyết định M2 đã chốt (2026-07-08): egress chia theo độ nhạy cảm (xem §M2). Còn 1 finding không-block (M1b, vào risk register R7).
**Nguồn:** [ADR-009](ADR-009-architecture-baseline-synthesis.md) §2.1 (M1, M2). Audit read-only trực tiếp trên codebase, không suy diễn.
**Phương pháp:** đối chiếu từng check-item với mã nguồn thật; mỗi kết luận kèm bằng chứng file:line.

---

## M1 — Security-verify sweep

| Check | Kết quả | Bằng chứng |
|---|---|---|
| M1(a) không rò secret ra response | 🟢 PASS (1 note nhỏ) | dưới |
| M1(b) thread_id scope theo user | 🔴 **FINDING** | dưới |
| M1(c) audit log không ghi PII thô | 🟢 PASS | dưới |
| M1(d) không có raw-SQL read ngoài erp_query | 🟢 PASS (mạnh) | dưới |

### M1(a) — Rò secret: PASS

- Secret (`ODOO_PASSWORD`, `LITELLM_MASTER_KEY`, `DATABASE_URL` DSN) chỉ đọc từ env để dựng client/transport; KHÔNG nội suy vào bất kỳ chuỗi trả về user nào (`gateway.py:68-69`, `erp_agent.py:19-23`, `transport.py:15-16`).
- **Vector rò tiềm năng duy nhất — đã đóng:** lỗi `psycopg` ở RAG path có thể chứa DSN (kèm mật khẩu DB). Nhưng `rag_node` nuốt exception và trả `SAFE_MSG` (`nodes.py:83-85`: `except Exception: logger.exception(...); answer = SAFE_MSG`) — DSN không bao giờ tới client. Exception được log server-side, không surface.
- Exception không bắt được → FastAPI trả HTTP 500 chung (traceback chỉ ở server), không leak.
- Lỗi auth chỉ trả chuỗi gợi ý `"...kiểm tra ODOO_USERNAME/PASSWORD"` (`transport.py:24`), KHÔNG phải giá trị password thật.
- **Note (thấp):** đường tool-error của write-coordinator surface `{e}` verbatim (`_msg(f"Lỗi ...: {e}")`). `{e}` là XML-RPC fault của Odoo (văn bản lỗi nghiệp vụ), không mang secret trong thực tế — chấp nhận, không cần sửa.

### M1(b) — thread_id scoping: FINDING 🔴

`_derive_thread_id` (`main.py:60-74`): thread_id = `body.get("session_id") or body.get("id")` (client tự khai, không kiểm) **HOẶC** fallback `"conv-" + sha1(tin_nhắn_user_đầu_tiên)`. **Không gắn với danh tính user đã xác thực.** Đây đúng lớp lỗi ChangAI BLK5/D1.

Hệ quả (2 mức):
- **Single-user (correctness bug ngay bây giờ):** hai hội thoại KHÁC nhau mở đầu bằng cùng một câu (vd hai chat cùng gõ "tạo báo giá cho Azure") → cùng thread_id → checkpoint dùng chung (lịch sử, working_context, và **confirm đang treo**). Chat thứ 2 vô tình nối vào state của chat thứ nhất.
- **Multi-user / demo cho khách (security):** hai user va vào cùng thread → một user có thể **resume confirm-ghi đang treo của user khác** → rò quyền-ghi chéo user. Nghiêm trọng hơn rò lịch sử đọc.

**Không block A3** (thread scoping trực giao với model routing). Nhưng là finding thật cần vào risk register.

**Khuyến nghị:** (a) ghi nhận là ràng buộc đã biết ngay bây giờ (deployment cá nhân 1-user chấp nhận được); (b) **bắt buộc sửa TRƯỚC khi Open WebUI phục vụ >1 user** (kể cả demo dùng chung) — nạp danh tính user đã xác thực + 1 nonce per-conversation vào thread_id, và không tin `session_id` client gửi xuyên user mà không scope.

### M1(c) — Audit log PII: PASS

`log_mcp_event` (`server.py:106-125`) ghi vào `mcp_call_log` (Postgres nội bộ, hạ tầng tin cậy) đúng các cột: `event_type, caller, tool_name, model_name, operation, duration_ms, error_code, error_message` (truncate 10k). **KHÔNG ghi payload args** (tên khách, số tiền, dòng hàng) — chỉ ghi tên model (vd `sale.order`), operation (`write`), tên tool. `ODOO_PWD` không bao giờ được log. Đây là log metadata audit sạch, không phải PII sink.

### M1(d) — Raw-SQL read ngoài erp_query: PASS (mạnh)

- **Không tồn tại bề mặt Text2SQL nào.** Đọc Odoo chỉ qua `gateway.py` với 4 guard cứng (method allowlist `search_read/read_group/name_search` + model denylist `res.users/ir.config_parameter/ir.model.access/account.journal/account.bank.statement` + regex sanitize + forced limit). "Read-only by construction."
- Tầng MCP `odoo()` (`server.py:140-173`) thêm lớp thứ 2 độc lập: classify deny-by-default + enforce read-only (`WRITE_ENABLED` gate) + rate-limit + audit.
- 3 chỗ dùng psycopg thô đều là hạ tầng cố định, KHÔNG do LLM/user điều khiển: RAG vector store (`rag/db.py`, parameterized), LangGraph checkpointer (`erp_agent.py`, AsyncPostgresSaver bảng riêng), MCP log (INSERT parameterized). Không có SQL nào do LLM sinh, bất kỳ đâu.

---

## M2 — Data-egress: hiện trạng + đích, và quyết định bắt buộc trước A3

### Hiện trạng (trước A3): egress ra internet = **KHÔNG**

Mọi thứ chạy trên workstation:
- Mọi LLM call → LiteLLM (:4000) → Ollama → `qwen3:8b` **local**.
- Embedding → `bge-m3` qua Ollama **local**.
- RAG vector store → Postgres **local**; Odoo → XML-RPC **local**.

→ Đây là baseline privacy: 0 byte rời máy.

### Đích (sau A3), theo bảng 7 vai trò của spec — dữ liệu gì rời máy

| Vai trò | Model đích | Đi cloud? | Dữ liệu prompt mang theo |
|---|---|:--:|---|
| Router (intent) | gemini-flash-lite | ☁️ | Tin nhắn user (thô) |
| Evaluator (có/không) | gemini-flash-lite | ☁️ | Câu trả lời user + câu hỏi confirm |
| **Read ReAct (chọn tool đọc)** | gemini-flash-lite | ☁️ | **Câu hỏi + KẾT QUẢ tool đọc: tên khách, số tiền, đơn hàng, tồn kho** |
| **Fusion (RAG+ERP)** | gemini-3.5-flash | ☁️ | **Nội dung TÀI LIỆU nội bộ (policy/SLA/SOP/bảng giá) + dữ liệu ERP** |
| **Synthesis (trích dẫn)** | gemma | ☁️ | **Đoạn tài liệu nội bộ được truy hồi** |
| Chit-chat/unknown | gemma | ☁️ | Tin nhắn user |
| **Planner (ghi ERP)** | **qwen3:8b LOCAL** | ✅ ở lại | Ý định ghi + args — KHÔNG rời máy (quyết định khóa #7) |
| Embedding | bge-m3 LOCAL | ✅ ở lại | — |

### 🔴 Finding không hiển nhiên (điểm cốt lõi của M2)

**"Planner ở lại local" KHÔNG bảo vệ dữ liệu nghiệp vụ thật — vì dữ liệu đó rời máy qua đường ĐỌC, không phải đường ghi.**

- Quyết định khóa #7 (planner local) bảo vệ *ý định ghi + args* (privacy của thao tác ghi). Đúng, nhưng đó không phải nơi dữ liệu nhạy cảm nằm.
- **Dữ liệu nghiệp vụ thật** — tên khách hàng, giá trị đơn, tồn kho, và **toàn bộ nội dung tài liệu nội bộ** (policy, SLA, SOP, bảng giá) — chảy sang cloud qua Read-ReAct / Fusion / Synthesis khi các role này flip ở A3.
- **Provider đích là Google AI Studio free tier** (spec §0 chọn làm primary cloud). Điều khoản free tier của Google AI Studio **cho phép dùng dữ liệu** (product improvement / human review) — khác hẳn Vertex AI trả phí (confidential). Tức là: bật A3 nguyên trạng = gửi dữ liệu khách hàng + tài liệu nội bộ qua một endpoint có điều khoản dùng-dữ-liệu.

### ✅ QUYẾT ĐỊNH (2026-07-08): chia theo độ nhạy cảm

Đã chốt phương án **"chia theo độ nhạy cảm"** (khóa vào ADR-009 §4 #7):

| Role | Đích sau A3 | Lý do |
|---|---|---|
| Router (intent) | ☁️ cloud (gemini-flash-lite) | Mang tin nhắn thô, ít nhạy cảm; tần suất CAO → đúng chỗ cần RPD/latency |
| Evaluator (có/không) | ☁️ cloud | Mang câu trả lời user + câu hỏi confirm; ít nhạy cảm |
| Chit-chat/unknown | ☁️ cloud (gemma) | Mang tin nhắn thô; RPD dư dả. **⚠️ ĐIỀU KIỆN (M5, ADR-009):** node hiện gửi FULL history (`state["messages"]`) — assistant-turn trước có thể chứa dữ liệu ERP → phải trim về tin nhắn user cuối TRƯỚC khi flip role này |
| **Read-ReAct (đọc)** | 🖥️ **LOCAL qwen3:8b** | Mang dữ liệu khách/đơn/tồn kho; tần suất TB |
| **Fusion (RAG+ERP)** | 🖥️ **LOCAL** | Mang tài liệu nội bộ + dữ liệu ERP; tần suất THẤP → giữ local tốn ít RPD |
| **Synthesis (trích dẫn)** | 🖥️ **LOCAL** | Mang đoạn tài liệu nội bộ |
| Planner (ghi) + Embedding | 🖥️ LOCAL | Đã khóa từ trước |

**Cơ sở quyết định:** các role mang dữ liệu nhạy cảm (Read/Fusion/Synthesis) trùng đúng với các role tần suất thấp → giữ local tốn rất ít ngân sách RPD; role tần suất cao nhất (Router) chỉ mang tin nhắn thô → an toàn đẩy cloud, đúng chỗ hưởng lợi. **Không regression vs hôm nay** — 3 role local này đang chạy trên qwen3:8b và đã pass live-verify; A3 chỉ *thêm* cloud cho 3 role tầm thường, không *rút* gì khỏi local.

→ **A3 giờ = flip 3/7 role** (không phải 5/7 như spec gốc). Dữ liệu nghiệp vụ + tài liệu nội bộ KHÔNG rời máy.

---

## Verdict cổng Phase 0

- **M1: ĐẠT** — 3/4 PASS; M1(b) là finding đã ghi nhận (không block A3, bắt buộc sửa trước multi-user).
- **M2: HOÀN THÀNH + ĐÃ QUYẾT** — egress hiện tại = 0; egress đích đã lập bảng; đã chốt "chia theo độ nhạy cảm" (chỉ 3 role tầm thường lên cloud, dữ liệu + tài liệu ở local).
- **Kết luận:** Phase 0 mở đường **A1/A2 VÀ A3** (A3 giờ = flip 3/7 role, không rò dữ liệu nghiệp vụ). Chỉ còn M1b (thread scoping) là finding không-block, đã vào risk register R7 — bắt buộc sửa trước khi phơi nhiễm multi-user.

## Cập nhật risk register (feed vào ADR-009 §5)

- **R1 (data-egress) — sắc hơn:** không chỉ "PII rời máy" chung chung; cụ thể là Read/Fusion/Synthesis mang **dữ liệu khách + toàn bộ tài liệu nội bộ** sang **Google AI Studio free tier có điều khoản dùng-dữ-liệu**. "Planner local" không che phần này.
- **R7 (MỚI) — thread_id không scope theo user:** collision same-first-message (bug ngay ở single-user); rò quyền-ghi chéo user nếu >1 user. Giảm thiểu: deployment 1-user hiện tại. Bắt buộc sửa trước bất kỳ phơi nhiễm multi-user/demo dùng chung.
