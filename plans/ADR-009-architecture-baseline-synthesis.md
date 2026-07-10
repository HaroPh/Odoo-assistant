# ADR-009 — Tài liệu nền kiến trúc: tổng hợp, khóa quyết định, roadmap hợp nhất

**Date:** 2026-07-08
**Status:** Accepted
**Context:** Hợp nhất 3 nguồn thành 1 tài liệu nền tự-chứa cho toàn project: (1) spec Core+Satellites `docs/superpowers/specs/2026-07-08-final-architecture-design.md`; (2) nghiên cứu mổ xẻ 26 pattern kiến trúc của ChangAI (Phụ lục A); (3) production code-review ChangAI (Phụ lục B). ADR này là tầng "quyết định đã khóa" phía trên spec/plan — spec/plan mô tả *cách làm*, ADR này quy định *cái gì bất biến và vì sao*. Hai phụ lục gói trọn substance của 2 nghiên cứu để tài liệu không phụ thuộc nguồn ngoài repo.
**Liên quan:** [ADR-008](ADR-008-MCP-vs-A2A.md) (MCP-first single-agent), spec Core+Satellites (2026-07-08), auto-chain (2026-07-05, merged PR#9), Phase B cleanup (2026-07-08, merged).
**Supersede:** QĐ M2 (2026-07-08) **thay thế** bảng gán model spec §2.2 và phạm vi A3 của spec (5/7 role → 3/7 role; Read/Fusion/Synthesis ở lại local). Spec là working-doc gitignored; ADR này là nguồn sự thật được track.

---

## 0. Nguyên tắc đọc

- **ChangAI là dự án THAM CHIẾU, không phải codebase của ta.** Nó chạy Frappe/ERPNext + Text2SQL; hệ của ta chạy Odoo + LangGraph + MCP + erp_query. Mọi phát hiện về ChangAI được dịch thành một trong hai dạng, KHÔNG phải "tính năng để bê về":
  - **Bằng chứng khóa guardrail** — lỗi của ChangAI chứng minh vì sao một quyết định của ta nên khóa vĩnh viễn.
  - **Việc phải verify** — kiểm tra hệ của ta có vô tình dính cùng lớp lỗi không.
- Phần "áp dụng được" từ ChangAI rất ít và đều là *kỷ luật vận hành*, không phải *thành phần kiến trúc*.
- Chi tiết đầy đủ nằm ở **Phụ lục A** (26 pattern) và **Phụ lục B** (review). Thân ADR chỉ giữ tầng quyết định.

---

## 1. Tổng hợp phát hiện (đã khử trùng lặp & mâu thuẫn)

Ba nguồn hội tụ về cùng một số kết luận. Bảng dưới gộp các phát hiện trùng nhau thành 1 dòng (F#), trỏ tới phụ lục.

| # | Phát hiện hợp nhất | Nguồn | Ý nghĩa cho hệ của ta |
|---|---|---|---|
| F1 | Ghi dữ liệu KHÔNG có bước xác nhận = thảm họa mất dữ liệu | A·B3, B·BLK2 | Khóa: confirm-gate |
| F2 | Mơ hồ → "đoán rồi áp dụng cho tất cả" (≤50 bản ghi); field `clarify` có trong prompt nhưng code không đọc | A·B5, B·BLK2 | Khóa: disambiguation-interrupt |
| F3 | An toàn đặt cược 100% vào prompt (guard SELECT-only bị comment, bật/tắt ≥7 lần) | A·A2/A3, B·BLK3 | Khóa: enforcement ở tầng thực thi |
| F4 | Text2SQL-raw + permission bằng string-concat WHERE (hỏng với GROUP BY/ORDER BY/LIMIT, under-filter UNION, không base `has_permission`) | A·A3, B·BLK3 | Khóa: reads qua erp_query typed tools |
| F5 | Rò secret ra client (GCP private key trong payload); secret lưu plain-text | B·BLK4 | **Verify:** hệ ta có rò secret ra Open WebUI? |
| F6 | Truy cập chéo user (session_id/ticket/history không kiểm chủ sở hữu) | A·D1, B·BLK5 | **Verify:** thread_id có scope theo user? |
| F7 | Job nặng chạy trên web-worker DÙNG CHUNG → đói cả site; blocking poll 300s > timeout 120s → kill giữa lúc ghi | B·BLK6 | Khóa: Core+Satellites (job = process riêng) |
| F8 | Model ID hardcode rải rác 5 file, không nhất quán, có model không tồn tại (lỗi gõ) | A·C2, B·MED | Khóa: ROLE_MODELS registry, zero hardcode |
| F9 | Retry 4 cơ chế rời rạc; một cái âm thầm bỏ qua provider → rò dữ liệu sang Gemini kể cả khi cấu hình local | A·C3, B·HI4 | Khóa: escalation có kiểm soát, không đổi provider ngầm |
| F10 | Zero test coverage (3 file test rỗng `pass`) trên hệ sinh+chạy SQL và ghi ERP | B·BLK1 | Khóa: eval/regression-gate bắt buộc |
| F11 | Fuzzy cache khóa `token_set_ratio≥98` trên câu hỏi thô → "2023" vs "2024" trả SAI im lặng | A·A4, B·HI6 | Nếu cache: khóa (tool,args), không theo chuỗi |
| F12 | RCE surface: `allow_dangerous_deserialization=True` (pickle) ×3 + `trust_remote_code=True` trên model path cấu hình được | B·HI1 | **Verify:** ta dùng bge-m3 qua Ollama, không pickle in-process |
| F13 | Bộ nhớ N× per worker (embedding+3 FAISS load in-process); warm-up gắn login không phải deploy | B·HI2 | Khóa: không load model in-process per web-worker |
| F14 | Hai hạ tầng embedding song song (in-process vs FastAPI sidecar), một cái chết | B·HI3 | Bài học dọn: một cách làm một việc |
| F15 | Kỷ luật retry TỐT (bounded + checkpoint mỗi 10 đơn vị + circuit-breaker sau 5 lỗi) ở job enrichment | A·C5 | **Áp dụng:** chuẩn cho Job Runner |
| F16 | Cấu hình runtime sửa được từ UI, không cần deploy (nhưng secret sai fieldtype) | A·C1/C7 | **Áp dụng (có sửa):** toggle qua config Odoo-native |
| F17 | Debug surface (nhu cầu thật) nhưng lộ toàn bộ ra client, redact chỉ ở UI | A·A5/D2 | **Áp dụng (có sửa):** observability lọc quyền + redact server-side |
| F18 | README/spec mô tả sai hành vi thật ≥3 lần | A·D4/D5, B·MED | Khóa kỷ luật: luôn grep-verify, không tin tài liệu |
| F19 | Code chết tích tụ (api/v1, `call_model` định nghĩa 2 lần, `create_entity` unreachable, `validate()` ngoài class) | A·D3, B·MED | Khóa kỷ luật: dọn code cũ trong cùng nhánh thay thế |

**Mâu thuẫn đã gỡ:**
- *"KEEP debug surface" (dissection) vs "debug là lỗ bảo mật" (review)* → không mâu thuẫn: dissection phân loại MODIFY (giữ ý tưởng, sửa cơ chế); review giải thích VÌ SAO. Gộp thành F17 → "Có thể làm sau, có ràng buộc".
- *Spec đặt Phase A (flip cloud) ưu tiên #1 vs review lo data-egress* → gỡ bằng cách chèn cổng verify (Phase 0) TRƯỚC bước flip A3 (lúc dữ liệu mới bắt đầu rời máy). Không đảo ưu tiên, chỉ thêm cổng.

---

## 2. Phân loại quyết định

### 2.1 🔴 BẮT BUỘC làm (gate, không bỏ qua)

| ID | Việc | Vì sao bắt buộc | Nguồn |
|---|---|---|---|
| M1 | **Security-verify sweep trên hệ Odoo:** (a) không secret nào (LITELLM/Odoo cred) lọt ra Open WebUI/response; (b) `thread_id` scope đúng phiên/user, không đọc chéo; (c) audit log MCP không ghi PII thô; (d) xác nhận KHÔNG có đường đọc raw-SQL nào ngoài erp_query | F5,F6,F4 — lớp lỗi ChangAI dính; phải chứng minh ta miễn nhiễm trước khi mở rộng bề mặt | B·BLK4/5 |
| M2 | **Tài liệu data-egress trước khi flip A3** — ✅ **ĐÃ QUYẾT (2026-07-08): chia theo độ nhạy cảm.** Cloud chỉ Router/Evaluator/Chit-chat (tin nhắn thô); Read-ReAct/Fusion/Synthesis + Planner ở local (mang dữ liệu khách + tài liệu nội bộ). Chi tiết: [phase-0-hardening-verify-report.md](phase-0-hardening-verify-report.md) §M2 | F1 privacy + F18 | spec §2.2 |
| M3 | **Eval/regression-gate bắt buộc trước MỌI flip model/prompt.** LƯU Ý NỘI DUNG GATE (review fable 2026-07-08): 276 unit test hiện có MOCK toàn bộ LLM và 3 smoke test chỉ phủ write-flow — chúng KHÔNG đo được chất lượng model mới. Gate cho A3 phải gồm **eval set theo role**: (a) intent-routing accuracy (bộ câu tiếng Việt → intent kỳ vọng, so flash-lite vs baseline qwen3:8b); (b) confirmation-classify accuracy (bộ reply → CONFIRM/CANCEL/UNCLEAR). Việc SOẠN 2 eval set này là task thật, chưa được size — phải nằm trong plan A3. Job Runner sau đó tự động hóa chúng. | F10 — gate rỗng ruột thì pass trivially, không gác được gì | spec A5 |
| M4 | **Mọi model ID qua 1 registry** (ROLE_MODELS + LiteLLM alias), zero hardcode trong logic | F8 — tiên quyết cho A2 | spec §2.2 |
| M5 | **Trim history cho node chit-chat TRƯỚC khi flip nó lên cloud:** `respond_unknown` hiện gửi `state["messages"]` NGUYÊN VẸN — nếu lượt trước là erp_read (dữ liệu tồn kho/đơn hàng trong câu trả lời assistant), rồi user nói "cảm ơn" → route unknown → TOÀN BỘ history (kèm dữ liệu ERP) rời máy. Sửa: chỉ gửi tin nhắn user cuối (hoặc N user-turn cuối, không kèm assistant-turn chứa dữ liệu). *(Phát hiện bởi review fable 2026-07-08 — lỗ hổng duy nhất của QĐ M2.)* | Bảo toàn nguyên tắc M2 ở tầng thực thi (bài học F3: quyết định chỉ có giá trị bằng cơ chế thực thi) | nodes.py:93-98 |

### 2.2 🟢 Nên làm

| ID | Việc | Nguồn |
|---|---|---|
| S1 | **Escalation-on-validation-fail cho planner:** tường minh, tối đa 1 lần, sang model MẠNH hơn, TUYỆT ĐỐI không đổi provider ngầm. **RÀNG BUỘC M2 (review fable):** planner mang dữ liệu ghi → đích escalation KHÔNG được là cloud nếu vi phạm khóa #7; escalation local-only lại vướng VRAM 8GB (OLLAMA_MAX_LOADED_MODELS=1). A5 phải trả lời "escalate tới ĐÂU" trước khi làm — nếu không có đáp án sạch, drop A5 (nó vốn optional). **✅ REDEFINED + DONE 2026-07-10:** không có đáp án sạch cho "escalate tới đâu" (model local duy nhất còn qwen3:8b sau Phase B; cloud vi phạm khóa #7) → user quyết định redefine thành corrective-retry CÙNG model: parse pipeline 2 tầng (loads → salvage tất định strip <think>/fence) + retry đúng 1 lần kèm correction message; messages phụ không rò vào state; mọi plan cứu được vẫn qua confirm-gate (khóa #4). Spec docs/superpowers/specs/2026-07-10-a5-planner-json-retry-design.md | F9 + spec A5 |
| S2 | **Chuẩn retry cho Job Runner:** bounded + checkpoint + circuit-breaker, 1 helper dùng chung — ✅ DONE 2026-07-10: `backend/jobs/resilience.py` (`run_resilient`), 3 eval fn dùng chung; lỗi hết-retry → `errors` riêng → INFRA_ERROR (bảo toàn exit contract 1=model-kém/2=không-đo-được); checkpoint là bằng chứng không phải resume; spec docs/superpowers/specs/2026-07-10-s2-job-runner-resilience-design.md | F15 |
| S3 | **Toggle vận hành qua cơ chế Odoo-native** (WRITE_ACTIONS_ENABLED…) — không redeploy; secret ở secret-store đúng — ✅ DONE 2026-07-10 (vế toggle): đọc runtime từ ir.config_parameter key `erp_ai.write_actions_enabled` (backend `write_gate.py` + MCP server tự implement, cache TTL 5s, fail-closed; env var bị xóa hoàn toàn; setup 1 lần: tạo System Parameter value=true). CỐ Ý không qua erp_query gateway (denylist ir.config_parameter giữ nguyên). Vế "secret ở secret-store" NGOÀI scope — secrets vẫn ở .env local. Spec docs/superpowers/specs/2026-07-10-s3-odoo-native-write-toggle-design.md | F16 |
| S4 | **Điều kiện hóa `/no_think` theo family model** — gộp Phase A2. **THU NHỎ sau QĐ M2 (review fable):** `/no_think` chỉ tồn tại ở 3 prompt (SYSTEM_PROMPT/erp_read, RAG_SYNTHESIS, FUSION — prompts.py:15,81,99) và CẢ BA đều Ở LẠI LOCAL theo M2; các prompt lên cloud (router/evaluator/chit-chat) vốn không có `/no_think`. S4 rút còn: verify + ghi chú "3 prompt local giữ nguyên /no_think, prompt cloud không thêm" — effort ≈ 0. | spec §3.2 |

### 2.3 🟡 Có thể làm sau

| ID | Việc | Điều kiện kích hoạt | Nguồn |
|---|---|---|---|
| L1 | **Observability/debug surface** — redact + lọc quyền SERVER-side trước khi rời máy, gate role thật | Khi debug production khó thực sự | F17 |
| L2 | **Response cache khóa (tool, args chuẩn hóa)** — không bao giờ theo độ giống chuỗi | Khi volume LLM lặp đủ lớn | F11 |
| L3 | **Job nghiệp vụ #2** (report digest, cảnh báo tồn kho) | Sau khi eval-job chứng minh seam; có use case | spec Phase C |
| L4 | **SQL Server MCP** (trục "thêm nguồn") — read-only, AST-validate, KHÔNG string-concat permission | Có nhu cầu nguồn dữ liệu thật | spec Phase D, F4 |

### 2.4 ⛔ Không nên làm (khóa REJECT)

| ID | Không làm | Vì sao | Nguồn |
|---|---|---|---|
| N1 | Text2SQL-raw cho đọc | erp_query typed tools + ORM domain-filter loại cả lớp rủi ro | F4 |
| N2 | Write không qua confirm-gate | Mất dữ liệu; bất biến #1 | F1 |
| N3 | "Đoán rồi áp dụng hàng loạt" khi mơ hồ | Xoá/sửa nhầm nhiều bản ghi im lặng | F2 |
| N4 | Đặt an toàn vào chỉ-thị prompt | Prompt không phải cơ chế thực thi | F3 |
| N5 | A2A/multi-agent | Chưa chạm trigger ADR-008 §5; nhân latency 1-GPU | ADR-008 |
| N6 | UI riêng thay Open WebUI / đổi contract /v1 | Đã khóa; bề mặt bảo trì vô ích | spec §6 |
| N7 | Retry đổi provider ngầm | Rò dữ liệu sang cloud ngoài ý muốn | F9 |
| N8 | Load model in-process per web-worker; pickle-deserialization; trust_remote_code | Bộ nhớ N×, RCE surface | F12,F13 |
| N9 | Fuzzy-string cache key | Trả sai im lặng | F11 |
| N10 | Voice / dịch đa ngôn ngữ / fine-tune embedding / sinh training-data | Đặc thù ChangAI, ngoài phạm vi, YAGNI | A·C6/D8 |

---

## 3. Roadmap cập nhật (hợp nhất)

```
✅ Phase B (dọn dẹp) — DONE 2026-07-08, merged
     xoá erp_agent_mvp.py · xoá qwen3:4b/qwen2.5:7b · (/no_think → gộp A2)
        │
        ▼
🔴 Phase 0 (Hardening-Verify) — MỚI, cổng bắt buộc, effort thấp (verify không build)
     M1 security-verify sweep · M2 data-egress doc
        │
        ▼
   Phase A (AI Layer hybrid)
     A1 alias gateway (0.5d) → A2 ROLE_MODELS + /no_think điều kiện (1–2d, gồm M4/S4)
        → [CỔNG: M3 eval-gate — PHẢI soạn 2 eval set theo role trước, xem §2.1 M3]
        → A3 flip 3/7 role (Router/Eval/Chit-chat) sang cloud — **✅ 3/3 HOÀN
          TẤT (2026-07-09).** Router (Task 6) · Chit-chat (sau khi có eval-gate
          riêng, xem R10) · Evaluator (gate FAIL gốc false_confirm=1 → root-cause
          + sửa prompt confirmation.py → PASS chính thức, xem R11). Cả 3 role
          đều qua eval-gate thật trước flip, đúng khóa #10. Read/Fusion/Synthesis
          ở LOCAL tuyệt đối (QĐ M2, verify lại: model_for() vẫn trả qwen3:8b cho
          4 role này bất kể env); gồm M5 trim history chit-chat
        → A4 Quota Guard (HẠ ưu tiên sau M2: chỉ 3 role nhẹ dùng cloud, 500–1500 RPD
          khó chạm với 1 user — re-evaluate sau 1–2 tuần telemetry A3; LiteLLM
          fallback→local đã chặn outage) 
        → A5 planner escalation/S1 — ✅ REDEFINED + DONE 2026-07-10
          (corrective-retry cùng model, không escalation — xem §2.2 S1)
        │
        ▼ (có thể song song từ A2)
   Phase C (Job Runner satellite)
     skeleton + eval-job (M3 tự động hóa, S2 chuẩn retry) → job nghiệp vụ (L3)
        │
        ▼
   Phase D (MCP nguồn mới, L4) — chỉ khi có nhu cầu thật
```

**Thay đổi so với spec gốc:**
1. Phase B đánh dấu DONE (2 mục); `/no_think` gộp A2 (S4).
2. **Chèn Phase 0** (M1+M2) làm cổng TRƯỚC A3 — vì A3 là lúc dữ liệu bắt đầu rời máy sang cloud. Rẻ (verify + tài liệu), nhưng bắt buộc.
3. **Đẩy Job Runner eval-job lên sớm** (song song A2) để biến M3 từ kỷ luật thủ công thành cơ chế tự động TRƯỚC khi A3 flip — đúng bài học F10.

**Thứ tự khuyến nghị:** Phase 0 (verify, rẻ) → A1/A2 → C-skeleton+eval-job (tự động hóa cổng) → A3 (flip, giờ có eval thật gác) → A4 → A5/D theo nhu cầu.

---

## 4. Quyết định kiến trúc KHÓA (không thay đổi nữa)

> Bất biến. Muốn đổi phải viết ADR mới thay thế, kèm trigger cụ thể.

1. **MCP + 1 LangGraph agent, intent-routing nội bộ.** Không A2A tới khi chạm trigger ADR-008 §5.
2. **Open WebUI + `/v1` OpenAI-compat** — contract frontend/API bất biến. Mọi cơ chế mới đi qua text/markdown trong chat.
3. **Đọc qua erp_query typed Business Query API** (ORM + domain-filter). KHÔNG raw SQL do LLM sinh — bao giờ.
4. **Mọi write qua confirm-gate** (`interrupt`/`Command`). Không nới lỏng dù đổi model hay tối ưu tốc độ. *(auto-chain vẫn tôn trọng: 1 confirm đầu chuỗi cover các bước khai báo tường minh, mỗi bước vẫn qua executor có state-gate.)*
5. **Mơ hồ → disambiguation-interrupt (hỏi), không đoán.** Id-addressed.
6. **Enforcement ở tầng thực thi** (MCP state-gate, ORM Odoo, domain-filter), KHÔNG ở prompt text.
7. **Planner (ghi ERP) + MỌI role mang dữ liệu nghiệp vụ chạy local qwen3:8b.** Cloud CHỈ cho role mang tin nhắn thô, ít nhạy cảm: Router (intent), Evaluator (có/không), Chit-chat/unknown. Read-ReAct / Fusion / Synthesis ở LOCAL vì prompt mang dữ liệu khách + tài liệu nội bộ. *(Quyết định M2, 2026-07-08 — khóa; đổi phải xét lại điều khoản dùng-dữ-liệu của provider đích.)*
8. **Model ID chỉ qua ROLE_MODELS/LiteLLM registry** — zero hardcode.
9. **Việc nặng/nền = process satellite riêng** (client của `/v1`), không bao giờ trên đường chat tương tác. Chạy giờ thấp điểm.
10. **Không flip model/prompt khi chưa qua eval/regression-gate.**
11. **Embedding bge-m3 qua Ollama, một instance.** Đổi provider = re-index toàn bộ → không đổi.

---

## 5. Rủi ro lớn còn lại

| ID | Rủi ro | Mức | Giảm thiểu hiện có | Dư lượng |
|---|---|---|---|---|
| R1 | **Data-egress lên cloud (A3):** sau QĐ M2 chỉ còn tin nhắn user thô (Router/Eval/Chit-chat) rời máy; dữ liệu nghiệp vụ + tài liệu ở local | **Thấp** (hạ từ Cao sau QĐ M2) | Split theo độ nhạy cảm (QĐ M2) + degrade-to-local + M1 verify | Router vẫn gửi câu hỏi thô của user lên free-tier — chấp nhận (không chứa dữ liệu nghiệp vụ) |
| R7 | **thread_id không scope theo user** (M1b) — **ĐÃ FIX 2026-07-09** (feat/r7-thread-scoping): Lớp A — `ENABLE_FORWARD_USER_INFO_HEADERS=true` trên open-webui, backend ưu tiên header `x-openwebui-chat-id`/`-user-id` → thread `owui:{user}:{chat}` (identity thật per-chat: hết collision same-first-message lẫn cross-user; spike 2026-07-09 xác nhận header tới nơi). Lớp B — hội thoại mới (đúng 1 user message; CHỈ bật cho thread id server tự suy ra, client `session_id` tường minh giữ nguyên semantics resume) → `adelete_thread` wipe toàn bộ state cũ (parked confirm + `working_context`) trước khi chạy, bỏ qua nhánh resume. Spec: docs/superpowers/specs/2026-07-09-r7-thread-scoping-design.md. | Thấp (hạ từ TB) | Header identity + fresh-reset + TTL 300s (giữ nguyên) | Fix này là **identity/scoping, KHÔNG phải authorization**: multi-user THẬT vẫn cần auth (`WEBUI_AUTH` đang false) + permission layer trên write-tools. Header chỉ đáng tin khi backend nghe nội bộ — KHÔNG được coi là cơ chế bảo mật nếu expose backend ra ngoài. |
| R9 | **MỚI (2026-07-09, phát hiện qua live-verify R7 trên stack thật — KHÔNG lộ qua unit test/review trước đó), ĐÃ FIX cùng ngày:** Lớp B của R7 tự bản thân là 1 collision MỚI. Open WebUI tự bắn request nền sau MỖI câu trả lời của bot (auto title/tags/follow-up/query-gen, bật mặc định) — mang **CÙNG** header `x-openwebui-chat-id`/`-user-id` với hội thoại thật (verify trực tiếp qua log live: 2 loại request có header giống hệt nhau, không field nào phân biệt), luôn đúng 1 user message, không `session_id` → khớp *chính xác* điều kiện fresh-reset → `adelete_thread` xoá oan state hội thoại thật (kể cả confirm đang chờ) giữa lúc bot hỏi và user trả lời. Không phá khóa #4 (write-planner luôn `interrupt()` lại dù replan) nhưng phá trải nghiệm resume thật. Fix: nhận diện message đơn bắt đầu `### Task:\n` (marker nội bộ ổn định Open WebUI) → route qua `ERPAgent.answer_stateless` (role **local-pinned `synthesis`**, không đụng thread/checkpoint/graph). Bản đầu dùng role `chitchat` (cloud-eligible) — review độc lập bắt được đây là 1 kênh rò rỉ dữ liệu ERP MỚI ra cloud nếu `MODEL_CHITCHAT` từng flip (task-prompt nhúng lại lịch sử hội thoại, có thể chứa giá/tên khách do role local sinh ra); đã sửa trước merge. Spec §8 (docs/superpowers/specs/2026-07-09-r7-thread-scoping-design.md). | Thấp (đã fix, đã live-verify lại 2 lần: checkpoint count giữ nguyên qua request nền; confirm-flow sống sót qua độ trễ 20s giữa câu hỏi và "có") | Content-signature detection + role local-pinned (fail-closed ngoài `CLOUD_ALLOWED`) + test khoá invariant "không bao giờ dùng chitchat" | Phụ thuộc 1 chuỗi cụ thể Open WebUI đang dùng: (a) admin tự tuỳ biến template trong Open WebUI Admin Settings làm lộ lại NGAY LẬP TỨC, im lặng, không cảnh báo (dễ xảy ra hơn version upgrade); (b) user thật gõ đúng `"### Task:\n..."` bị trả lời stateless thay vì qua ERP agent (mất 1 turn, KHÔNG mất state). Chưa build cơ chế quan sát (log/metric) cho (a) — deferred. Bài học F10 lặp lại ở quy mô nhỏ hơn: review kỹ tới đâu cũng không thay được live-verify trên stack thật trước khi merge. |
| R2 | **Trần đúng-sai planner 8B local** (bịa ngày, computed-field). auto-chain khuếch đại hệ quả 1 plan sai | Cao | Confirm-gate + typed args + state-gate mỗi bước | Không triệt tiêu; là lý do R1 giữ planner local (không đổi privacy lấy model mạnh hơn lúc này) |
| R3 | **Throughput 1-GPU Ollama tuần tự** (NUM_PARALLEL=1) | TB | Satellite off-peak; quy mô cá nhân/demo | Concurrency tăng = phải vLLM/multi-GPU (cũng là trigger A2A) |
| R4 | **Interrupt-replay + drift trạng thái ngoài** (TOCTOU: đơn đổi state giữa pause↔resume) | Thấp–TB | MCP state-gate là gate thật; không mất dữ liệu | Nhánh draft→confirmed giữa chừng có thể route nhầm; đã ghi nhận, chấp nhận |
| R5 | **Eval-harness chưa tự động** — M3 hiện là kỷ luật (276 test + 3 smoke thủ công). **✅ ĐÃ ĐÓNG (2026-07-09/10, Phase C + S2):** `backend/jobs/eval_gate.py` chạy tự động qua Task Scheduler (lịch đêm) — "gate trước flip" giờ có cơ chế thật, không còn phụ thuộc kỷ luật con người. S2 (2026-07-10) thêm bounded retry + checkpoint + circuit-breaker cho vòng lặp per-case, tránh 1 lỗi thoáng qua làm mất toàn bộ kết quả đêm. | Thấp (hạ từ TB) | Roadmap đẩy eval-job lên sớm (Phase C song song A2) — đã build | Đã đóng bài học F10 (gate rỗng ruột) bằng cơ chế tự động, không còn phụ thuộc kỷ luật con người |
| R6 | **Quota Guard chưa build (A4)** | Thấp | Degrade-to-local tự động khi cloud lỗi/hết quota (hệ không chết) | Không cảnh báo chủ động 80%/95%; âm thầm chậm tới khi A4 xong. RPD thực tế (R8) khớp giả định gốc — không cần re-evaluate ưu tiên A4 |
| R8 | **MỚI (2026-07-09, đo lúc Task 6, ĐÃ SỬA sau khi user đính chính số liệu):** free-tier RPD thực tế qua Google AI Studio console: `gemini-3.1-flash-lite` = **500 RPD, 15 RPM**; `gemma-4-26b` = **1500 RPD, 15 RPM** — RPD khớp/cao hơn giả định gốc của spec §0 (không phải khủng hoảng). Nút thắt thật là **RPM=15** (thấp) — `run_eval.py` bắn 40-64 call liên tiếp không giãn cách trong 1 lần chạy → chạm rate-limit RPM, Google báo "reached a rate limit". | Thấp (đã đo lại, không phải rủi ro vận hành nghiêm trọng) | LiteLLM fallback khai báo hoạt động đúng thiết kế nếu RPM bị vượt. Đã paced-reverify (giãn cách 5s, an toàn dưới RPM=15) toàn bộ 24 case confirm-set + log model phục vụ thật mỗi call — **0/24 contaminated**, kết quả giống hệt lần gốc (acc=0.833, false_confirm=1) → xác nhận finding gốc (evaluator gate FAIL) là THẬT, không phải nhiễu rate-limit. | Việc còn lại: `run_eval.py` nên thêm pacing/backoff tôn trọng RPM trước khi dùng lại cho lần eval tiếp theo (tránh 429 giả gây nhiễu, dù lần này không gây nhiễu thật). **✅ ĐÃ LÀM** (`eval_gate.py`: `CLOUD_PACE_S = 5.0`, auto-chọn theo model qua `_auto_pace()` — pacing dưới RPM=15 áp dụng cho mọi lần eval sau, không riêng lần điều tra R8). Không ảnh hưởng quyết định router-flip (vẫn đúng, giờ càng vững hơn vì RPD dư dả). |
| R10 | **ĐÃ ĐÓNG (2026-07-09, feat/chitchat-eval-gate):** chitchat/gemma-cloud từng "ships ungated" (ghi nhận ở final review Phase A Task 6) — nay có gate riêng (`eval-gate --set chitchat`), khác cơ chế intent/confirm: KHÔNG so accuracy-vs-baseline (chitchat là sinh văn bản tự do, không có "câu trả lời đúng"), mà là kiểm tra AN TOÀN tuyệt đối chống model bịa đã thực hiện hành động ERP (`respond_unknown` không bind tool nào — mọi khẳng định "đã làm X" là bịa). `violations == 0` (heuristic từ khóa `HALLUCINATION_MARKERS`, không LLM-judge). Live-verify thật: qwen3:8b local PASS (0 violations) VÀ gemma-cloud PASS (0 violations, pace=5s/call đúng auto). **User quyết định flip `MODEL_CHITCHAT=gemma-cloud` (2026-07-09)** — cùng ngày Evaluator cũng flip xong (R11), A3 hoàn tất 3/3 role. Spec: docs/superpowers/specs/2026-07-09-chitchat-eval-gate-design.md. | Thấp | Gate tuyệt đối fail-closed + role-resolution qua `model_for()` (khóa cứng, không hardcode) | Heuristic từ khóa có thể bỏ sót cách diễn đạt bịa hành động không khớp `HALLUCINATION_MARKERS` (lớp phòng thủ thứ 2, router đã gate riêng accuracy phân loại). Cũng có rủi ro NGƯỢC (false-positive) ở vài marker ngắn (`đã cập nhật`, `thực hiện thành công`) — chưa quan sát thấy false-positive thật qua live-verify (0/16 case cả 2 model), điều chỉnh marker CHỈ khi có bằng chứng thật, không siết trước (đúng tinh thần spec). |
| R11 | **ĐÃ ĐÓNG (2026-07-09, fix/evaluator-confirm-gate):** evaluator/gemini-flash-lite từng gate FAIL (Task 6, `false_confirm=1` — điều kiện tuyệt đối, không thương lượng) trên case `"cho mình đổi thành 5 cái được chứ?"` (yêu cầu SỬA tham số bị đoán nhầm CONFIRM). Root-cause (systematic-debugging, không đoán): gọi thật model trên cả 8 case "unclear" — chỉ đúng 1 pattern hẹp này sai (7/8 case unclear khác đã đúng từ đầu), do prompt `confirmation.py`'s `_LLM_PROMPT` không có ví dụ phân biệt "câu hỏi về đề xuất" với "yêu cầu đổi đề xuất núp dưới câu hỏi lịch sự kèm 'được chứ?'". Fix: thêm 1 mệnh đề định nghĩa + 1 ví dụ — CHỈ mở rộng UNCLEAR/thu hẹp CONFIRM, không có đường nào nới lỏng (đúng khóa #4 "không nới lỏng dù đổi model"). Baseline `qwen3:8b` regenerate bắt buộc theo phương pháp (prompt đổi → baseline cũ không còn hợp lệ để so) — bản thân baseline CŨNG cải thiện (acc 0.500→0.625), xác nhận đây là fix chất lượng thật, không phải né cloud. Gate chính thức PASS (acc=0.917, baseline=0.625, false_confirm=0) — review độc lập tự chạy lại TOÀN BỘ pipeline (regenerate baseline + candidate) để verify, không chỉ tin số liệu. **User quyết định flip `MODEL_EVALUATOR=gemini-flash-lite` (2026-07-09)** — A3 hoàn tất 3/3 role. | Thấp | Prompt siết chặt + eval-gate tuyệt đối (`false_confirm==0`) chặn trước khi flip, không phải sau | 2 case mới lệch qua fix (`"từ từ đã"`, `"đợi mình xem lại đã"` — CANCEL bị đoán UNCLEAR) là hướng AN TOÀN (hệ thống hỏi lại, không tự hủy/tự thực thi nhầm) — không phải rủi ro mới. Baseline có thể dao động nhẹ giữa các lần chạy (qwen3:8b là thinking model, không hoàn toàn deterministic dù temperature=0) — reviewer tự chạy lại ra acc=0.583 (lệch 1 case so với 0.625 đã commit), nhưng gate PASS vững ở cả 2 mức baseline. Chưa có live-smoke-test qua Open WebUI thật cho luồng confirm dùng evaluator cloud (eval-gate đã exercise đúng prompt/model production thật, nhưng chưa phải full end-to-end chat) — khuyến nghị 1 lần live-verify nhẹ khi tiện. **✅ ĐÃ LÀM (2026-07-10, live-test toàn diện, case 12.3):** cố ý gửi câu trả lời không khớp keyword fast-path ("sửa lại thành 55 nhé") để buộc chạm đúng LLM evaluator thật qua chat thật (không phải eval-gate offline) — phân loại đúng UNCLEAR, hỏi lại nguyên văn câu hỏi gốc, không tự CONFIRM/CANCEL plan cũ. Đóng hoàn toàn residual này. |

---

## 6. Hệ quả

**Được:** một tài liệu nền tự-chứa khóa 11 quyết định bất biến; roadmap có cổng bảo mật đặt đúng chỗ (trước khi dữ liệu rời máy); bài học ChangAI được chuyển hóa thành guardrail đã khóa + checklist verify, không phải nợ kỹ thuật bê về.

**Mất / chấp nhận:** thêm 1 cổng (Phase 0) làm chậm đường tới A3 vài ngày — đổi lại chặn đúng lớp lỗi nghiêm trọng nhất của dự án tham chiếu. R1/R2 là đánh đổi có chủ đích: giữ planner local hy sinh sức mạnh model để giữ privacy + an toàn ghi.

**Điều rút ra lớn nhất từ ChangAI:** dự án đó có kiến trúc read/write *đúng hình dạng ở lớp ý tưởng* (ORM cho write, permission trước mutate, RAG ground schema) nhưng **thất bại ở tầng thực thi** — guard tắt, confirm không có, secret rò, test rỗng. Bài học nền: **một quyết định kiến trúc chỉ có giá trị bằng cơ chế thực thi của nó.** Đó là lý do ADR này nhấn "khóa ở tầng thực thi" (điểm 6, mục 4) hơn ở tài liệu hay prompt.

---

## Phụ lục A — Mổ xẻ 26 pattern kiến trúc ChangAI

Nguồn: 4 nghiên cứu read-only trực tiếp trên `D:\changai` (HEAD fd950ca), không tin README. Tally: **7 KEEP · 8 MODIFY · 11 REJECT**. Cột "QĐ" = verdict cho hệ Odoo của ta.

### A. Read pipeline
| ID | Pattern | Cơ chế thật (bằng chứng) | Điểm yếu chính | QĐ |
|---|---|---|---|---|
| A1 | RAG schema+entity retrieval 2 tầng | 1 embedding (nomic fine-tune) → FAISS table k=20 → field search → entity phonetic+rapidfuzz trên 6 master-doctype | Retrieval không lọc quyền đọc → lộ tên bảng/trường | KEEP (ý tưởng; ta đã có qua resolve.py) |
| A2 | LLM sinh raw SQL | v1 (3 model fine-tune, output giới hạn) → v2 LLM lớn + RAG sinh thẳng SQL | An toàn đặt vào prompt LAW, không ràng buộc cấu trúc | REJECT |
| A3 | Permission = string-concat WHERE | `execute_query` nối `f" AND {cond}"` cuối SQL; guard non-SELECT bị comment; không `has_permission` | Hỏng với GROUP BY/ORDER BY/LIMIT; under-filter UNION; doctype không có User Permission → không lọc | REJECT |
| A4 | Cache câu hỏi→SQL fuzzy | `token_set_ratio≥98` với ≤500 log → chạy lại SQL cache (số liệu tươi) | "2023"↔"2024" vượt ngưỡng → chạy sai SQL im lặng | MODIFY |
| A5 | Debug tab lộ nội bộ pipeline | Response LUÔN kèm payload debug; toggle chỉ ẩn ở UI; redact lúc render | Không role check server-side; từng rò GCP key nguyên văn | MODIFY |

### B. Write / CRUD
| ID | Pattern | Cơ chế thật | Điểm yếu chính | QĐ |
|---|---|---|---|---|
| B1 | Trích thực thể 2 tầng | LLM trả `is_cud/entity_words` → fuzzy/FAISS resolve thành candidate record (bỏ qua cho insert) | Index fuzzy không lọc quyền đọc | KEEP |
| B2 | Write chỉ qua ORM | 100% `get_doc().insert()/.save()`, `ignore_permissions=False`; grep raw-SQL write = 0 | Không sanity-check ngoài validation ORM | KEEP |
| B3 | KHÔNG có confirm trước ghi | NL→ghi trong 1 RPC đồng bộ; `generate_orm` gọi thẳng execute; luồng form-prefill an toàn nhưng prompt không emit trigger → chết | Không checkpoint con người giữa quyết định LLM và ghi DB | REJECT |
| B4 | Permission check trước mutate | `has_permission(doctype, action, throw=True)` dòng đầu mỗi nhánh | Chỉ doctype-level, sau khi LLM đã chọn target | KEEP |
| B5 | Mơ hồ → áp dụng TẤT CẢ | filter khớp 2–50 → loop `.save()`/xoá tất cả; `clarify` định nghĩa nhưng code không đọc | Sửa/xoá nhầm nhiều bản ghi im lặng | REJECT |
| B6 | Xử lý lỗi ghi: không retry/không txn | try/except phân loại; `insert_bulk` fail-partial, không commit/rollback | Batch không atomic → trạng thái nửa vời | MODIFY |

### C. AI orchestration
| ID | Pattern | Cơ chế thật | Điểm yếu chính | QĐ |
|---|---|---|---|---|
| C1 | Chọn provider qua Settings runtime | `call_model` rẽ theo field `llm` trong Doctype | Chỉ mức provider; version vẫn hardcode | KEEP |
| C2 | Model ID hardcode rải rác | 7 literal / 5 file; `claude-sonnet-4-6` không tồn tại (lỗi gõ); gpt-4o vs 4o-mini cùng việc | Nâng version = sửa nhiều nơi; lỗi gõ crash runtime | REJECT |
| C3 | 4 retry rời rạc, layer-2 phá config | RETRY_LIMIT=2 (same model) + 2 lần post-graph gọi thẳng Gemini (bỏ qua `llm`) + MAX_TRIES=4 + MAX_RETRIES=5 | Không escalate; rò sang Gemini kể cả cấu hình QWEN3/local | REJECT |
| C4 | Prompt phẳng/file, nạp 1 lần, `str.format` thô | ~24 file .txt; ghép question+context không delimiter | Không hot-reload; bề mặt prompt-injection | MODIFY |
| C5 | LLM bổ sung doc-schema, checkpoint+circuit-breaker | Backfill trường thiếu; checkpoint mỗi 10 bảng; dừng sau 5 lỗi liên tiếp | README gán nhầm cho Master Data (không gọi LLM); nhánh OpenAI dead | KEEP |
| C6 | Sinh training-data tương phản/module | LLM sinh anchor+positives → .jsonl fine-tune embedding | `max_loops` có thể trả thiếu mà báo ok:True | REJECT |
| C7 | Config trong DB Doctype, sửa từ UI | Singleton doctype; đọc `get_single()`, cache/request | Secret fieldtype `Data` (plain) không `Password` | MODIFY |

### D. Platform integration
| ID | Pattern | Cơ chế thật | Điểm yếu chính | QĐ |
|---|---|---|---|---|
| D1 | Chat history theo session_id client tự sinh | UUID browser (sessionStorage), không gắn `frappe.session.user` | `get_chat_history` không kiểm chủ sở hữu → đọc chéo | REJECT |
| D2 | 1 log doctype = audit+debug+cache | "ChangAI Logs" ghi mọi field trung gian mỗi lượt | Gộp 3 mục đích khó lọc quyền riêng | KEEP |
| D3 | Viết lại v1→v2, để v1 chết | grep v1 ngoài thư mục nó = 0; còn cả notebook train | Không dọn code chết sau thay thế | MODIFY |
| D4 | "Debug/Support tab" thực ra là SPA | README nói "native page"; thực tế Vue SPA, `page/` trống | Tài liệu mô tả sai kiến trúc | REJECT |
| D5 | Frontend SPA tiêm toàn cục mọi trang Desk | Vite build → asset app; `app_include_js` mọi trang | Tải toàn cục kể cả trang không dùng AI | REJECT |
| D6 | Hook cài đặt viết sẵn nhưng bị vô hiệu | `after_install/after_migrate` comment; chỉ `on_session_creation` sống, log mỗi login | Init 1-lần gắn vào sự kiện lặp (login) | MODIFY |
| D7 | Không scheduled job — sync là nút bấm | `scheduler_events` comment toàn bộ | Field DB ngụ ý tự động nhưng thực tế không | MODIFY |
| D8 | Dịch field theo yêu cầu bằng LLM | Claude Haiku dịch 1 field, ghi đè `doc.set()+.save()` | Bỏ qua i18n framework; chỉ field có sẵn | REJECT |

**Top ý tưởng production đáng học (xếp theo giá trị thật):** ① C5 kỷ luật retry+checkpoint+circuit-breaker (chuẩn cho Job Runner). ② C1/C7 config runtime không-deploy (Odoo-native, sửa lỗi thực thi). ③ A5/D2 debug surface (lọc quyền + redact server-side). ④ B1 trích thực thể 2 tầng (bỏ resolve cho insert). ⑤ A4 cache khóa theo ý định cấu trúc, không theo chuỗi.

---

## Phụ lục B — Production code-review ChangAI (verdict: BLOCK MERGE)

Review theo tầm Principal Engineer, gate trước production. Đánh dấu 🔴 = tác giả nhiều khả năng chưa nhận ra.

### 🚫 BLOCKING
| ID | Vấn đề | Bằng chứng |
|---|---|---|
| BLK1 | **Zero test coverage** trên hệ sinh+chạy SQL và ghi ERP; 3 file test chỉ `class(...): pass` | test_changai_settings/logs/help_desk.py |
| BLK2 | **Ghi không confirm + mơ hồ áp dụng cho tất cả (≤50)**; 🔴 permission ≠ intent | `text2sql_pipeline_v2.py` generate_orm; `operations.py:447-471,705-734` |
| BLK3 | **An toàn SQL đặt vào prompt; guard code comment-out** (bật/tắt ≥7 lần); permission string-concat hỏng cú pháp | `text2sql_pipeline_v2.py:923-941` |
| BLK4 | **Rò secret ra browser** (GCP private key trong payload, redact chỉ ở render); secret fieldtype `Data` | `get_frontend_settings`, `DebugTab.vue:31-53` |
| BLK5 | **Truy cập chéo user** (`get_user_tickets` không tham số → mọi ticket; `get_chat_history` không kiểm chủ) | `helpdesk_api.py`, `store_chats.py` |
| BLK6 | 🔴 **Đói web-worker dùng chung của cả ERPNext** — poll Replicate tới 300s blocking > timeout 120s → kill giữa lúc ghi | `clients.py:209-268` |

### 🟠 HIGH
| ID | Vấn đề | Bằng chứng |
|---|---|---|
| HI1 | 🔴 **RCE surface:** `allow_dangerous_deserialization=True` (pickle) ×3 + `trust_remote_code=True` trên model path cấu hình | `retrieve.py:201,219,256` |
| HI2 | 🔴 **Bộ nhớ N× per worker** (embedding+3 FAISS in-process module-global); warm-up gắn login, cold-start trong request | `retrieve.py:144-289` |
| HI3 | **Hai hạ tầng embedding song song** (in-process live vs FastAPI sidecar dead) | `emb_load_service.py`, `embedding_client.py` |
| HI4 | 🔴 **Retry layer-2 bỏ qua provider** → rò query sang Gemini kể cả cấu hình QWEN3/local | `text2sql_pipeline_v2.py:1165` |
| HI5 | **Không transaction control** CUD; `insert_bulk` fail-partial → trạng thái nửa vời | `operations.py` (không commit/rollback) |
| HI6 | **Fuzzy cache trả SAI im lặng** (token_set_ratio≥98 câu thô) | `store_chats.py:199-274` |
| HI7 | **Error Log spam** — `frappe.log_error` mỗi warmup và mỗi skip (mỗi login) | `retrieve.py:266-281` |

### 🟡 MEDIUM (maintainability)
`call_model` định nghĩa 2 lần (`clients.py:19,287`) · model ID hardcode 5 file (`claude-sonnet-4-6` không có thật) · egress OpenAI+Claude không khai báo trong README · dead code (toàn bộ api/v1, `save_message_doc`, `create_entity` unreachable, `validate()` viết ngoài class → không chạy) · prompt-injection surface qua `str.format` · field `clarify` prompt yêu cầu nhưng code không đọc · bug tên field `settings.get("location")` vs `gemini_location` → im lặng fallback.

**Điểm sáng công bằng (giữ được):** write path qua ORM đúng (`has_permission` trước mutate, không raw-SQL write); job enrichment schema có kỷ luật retry+checkpoint+circuit-breaker (C5) tốt hơn hẳn phần còn lại.

**Nếu là code đồng nghiệp — hard-block 6 việc:** BLK1 (viết test CUD+SQL-gen), BLK2 (confirm-gate + disambiguation), BLK3 (bật guard SELECT-only ở code + bỏ string-concat), BLK4 (không trả secret ra client + `Password` fieldtype), BLK5 (scope read theo user), BLK6 (đẩy pipeline sang background job — lỗi kiến trúc, không vá bằng tăng timeout).
