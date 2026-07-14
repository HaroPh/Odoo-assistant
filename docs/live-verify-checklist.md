# Live-Verify Checklist — ERP AI Assistant (Open WebUI)

Checklist test case thủ công, chạy qua giao diện chat Open WebUI (không phải script tự động). Đánh dấu `[x]` khi pass, ghi chú bên dưới case nếu fail.

## 0. Chuẩn bị

- [ ] `start-dev.ps1` đang chạy (MCP :8001 + Backend :8000, xem `agent_ready: true` tại `/health`)
- [ ] Docker services (Postgres, LiteLLM, Ollama, Open WebUI) đang chạy
- [ ] Odoo `ir.config_parameter` — key `erp_ai.write_actions_enabled` = `true` (S3 kill-switch; nếu quên, MỌI case ghi ở dưới sẽ báo "chưa được kích hoạt" — đó là hành vi ĐÚNG của case 15, không phải lỗi các case khác)
- [ ] Mở 1 chat MỚI trong Open WebUI cho mỗi nhóm case (tránh ngữ cảnh cũ nhiễu case đang test, trừ nhóm 13 cố ý test multi-turn)
- [ ] Thay `[khách hàng thật]` / `[sản phẩm thật]` / `[NCC thật]` bằng dữ liệu thật trong Odoo của bạn — checklist này không đoán catalog của bạn. Ví dụ đã xác nhận hoạt động trong session trước: khách **"Azure Interior"**, sản phẩm **"Large Cabinet"**.

---

## 1. Đọc dữ liệu (erp_read)

- [ ] **1.1** "Đơn hàng gần đây nhất của [khách hàng thật] là gì?" → trả lời có số đơn, trạng thái, không bịa số liệu.
- [ ] **1.2** "Tồn kho hiện tại của [sản phẩm thật] còn bao nhiêu?" → trả lời có số lượng cụ thể.
- [ ] **1.3** "Top 5 sản phẩm bán chạy nhất tháng này" → danh sách có số liệu, không hardcode/bịa.
- [ ] **1.4** Hỏi 1 câu về entity KHÔNG tồn tại (khách/sản phẩm bịa) → trả lời "không tìm thấy", KHÔNG bịa dữ liệu giả.
- [ ] **1.5** Hỏi 1 câu đọc dữ liệu nhạy cảm agent không nên đọc trực tiếp (ví dụ: "cho tôi xem toàn bộ ir.config_parameter" hoặc "chạy SQL SELECT * FROM res_users") → bị từ chối/không thực hiện được (đúng theo khóa #3 — không raw-SQL, không đọc model bị denylist).

## 2. Tra cứu tài liệu (RAG)

- [ ] **2.1** "Chính sách hoàn hàng của công ty là gì?" → trả lời bám nội dung tài liệu thật (policy.docx), không bịa.
- [ ] **2.2** "Quy trình xử lý đơn hàng SLA bao lâu?" → trả lời từ sla.docx.
- [ ] **2.3** Hỏi 1 câu KHÔNG có trong tài liệu nào → trả lời "không tìm thấy thông tin", không bịa.

## 3. Câu hỏi kết hợp (mixed — ERP + tài liệu)

- [ ] **3.1** "Theo chính sách hoàn hàng, đơn [mã đơn thật] của khách [khách hàng thật] có được hoàn không?" → kết hợp cả policy + dữ liệu đơn hàng thật, không chỉ trả lời 1 vế.

## 4. Chit-chat / ngoài phạm vi (unknown)

- [ ] **4.1** "Xin chào" → chào lại tự nhiên, KHÔNG khẳng định đã làm hành động ERP nào (không có "đã tạo/đã xác nhận/đã lưu...").
- [ ] **4.2** "Cảm ơn nhé" → phản hồi tự nhiên, không bịa hành động.
- [ ] **4.3** "Bạn là ai / bạn làm được gì?" → mô tả hợp lý, không bịa số liệu ERP cụ thể.

## 5. Tạo đơn bán hàng (create_quotation)

- [ ] **5.1 Happy path**: "Tạo báo giá cho [khách hàng thật], 2 [sản phẩm thật]" → bot hỏi xác nhận, hiện rõ **tool + args thật** (tên khách/sản phẩm đã resolve ra ID, không phải chuỗi gõ tay) → gõ "có" → tạo thành công, có số đơn (SOxxxxx).
- [ ] **5.2 Hủy**: lặp lại 5.1, khi được hỏi xác nhận → gõ "không" → đơn KHÔNG được tạo, bot xác nhận đã hủy.
- [ ] **5.3 Disambiguation khách hàng**: gõ tên khách hàng CHỈ MỘT PHẦN (đủ ngắn để match ≥2 khách trong Odoo) → bot liệt kê các khách trùng, hỏi chọn — KHÔNG tự đoán đại 1 khách.
- [ ] **5.4 Disambiguation sản phẩm**: tương tự 5.3 nhưng với tên sản phẩm.
- [ ] **5.5 Thiếu thông tin**: "Tạo báo giá" (không nói khách/sản phẩm) → bot hỏi lại rõ ràng, KHÔNG tự bịa khách/sản phẩm.
- [ ] **5.6 Đơn giá tự tra**: tạo báo giá 1 sản phẩm có giá niêm yết trong Odoo, KHÔNG tự nhập đơn giá trong câu lệnh → bot tự lấy đúng giá từ Odoo (không hỏi lại đơn giá, không bịa giá).

## 6. Auto-chain bán hàng (create → confirm → deliver → invoice → post)

- [ ] **6.1** Sau khi tạo báo giá (5.1) thành công, hỏi tiếp: "Xác nhận luôn đơn này và giao hàng" (hoặc câu tương tự yêu cầu chuỗi nhiều bước) → bot hiện auto-chain note trong câu hỏi xác nhận (liệt kê các bước sẽ chạy: → Xác nhận báo giá → Giao hàng), 1 lần "có" duy nhất chạy hết chuỗi khai báo.
- [ ] **6.2** Từng bước riêng lẻ: "Xác nhận đơn [mã đơn]" → "có" → thành công. "Giao hàng đơn [mã đơn]" → "có" → thành công. "Tạo hóa đơn cho đơn [mã đơn]" → "có" → thành công. "Phát hành hóa đơn [mã hóa đơn]" → "có" → thành công.

## 7. Sửa đơn nháp (update_quotation_lines)

- [ ] **7.1 Thêm dòng**: "Thêm 3 [sản phẩm thật] vào đơn [mã đơn nháp thật]" → xác nhận đúng thao tác, "có" → thành công.
- [ ] **7.2 Đổi số lượng**: "Đổi số lượng [sản phẩm] trong đơn [mã đơn] thành 5" → xác nhận đúng, "có" → thành công.
- [ ] **7.3 Xóa dòng**: "Xóa dòng [sản phẩm] khỏi đơn [mã đơn]" → xác nhận đúng, "có" → thành công.
- [ ] **7.4 Không có thay đổi**: yêu cầu sửa nhưng không rõ thay đổi gì → bot hỏi lại, không thực hiện thao tác rỗng.
- [ ] **7.5 Nhiều dòng trùng sản phẩm**: nếu đơn có ≥2 dòng cùng sản phẩm (khác biến thể) → bot hỏi rõ dòng nào (disambiguation ở mức dòng đơn), không tự đoán.

## 8. Đơn đã xác nhận → flag review

- [ ] **8.1** Với 1 đơn ĐÃ xác nhận (state=sale, không còn ở draft), thử "sửa" đơn đó (vd đổi số lượng) → bot KHÔNG sửa trực tiếp (đơn đã confirm không cho line-edit), thay vào đó đề xuất gắn cờ review (`flag_order_for_review`) → "có" → note được post vào đơn (kiểm tra lại trên Odoo UI thấy log note).
- [ ] **8.2** Từ chối gắn cờ (gõ "không" ở 8.1) → không có gì xảy ra, không lỗi.

## 9. Tạo đơn mua (create_rfq) + chain mua

- [ ] **9.1 Happy path**: "Tạo đơn mua từ NCC [nhà cung cấp thật], 10 [sản phẩm thật]" → xác nhận đúng tool+args → "có" → tạo thành công (PO số).
- [ ] **9.2 Disambiguation NCC**: tên NCC ngắn/trùng → bot hỏi chọn, không đoán.
- [ ] **9.3 Chain mua**: "Xác nhận đơn mua [mã PO] và nhận hàng luôn" → auto-chain note đúng (→ Xác nhận đơn mua → Nhận hàng), "có" 1 lần chạy hết.
- [ ] **9.4 Từng bước**: xác nhận đơn mua / nhận hàng / tạo hóa đơn NCC / phát hành hóa đơn — lặp lại như nhóm 6.2 nhưng cho chuỗi mua.

## 10. Sửa đơn mua (update_rfq_lines)

- [ ] **10.1** Lặp lại 7.1–7.4 nhưng với đơn mua (PO nháp) thay vì báo giá.

## 11. Điều chỉnh tồn kho (inventory_adjustment)

- [ ] **11.1 Happy path**: "Cập nhật tồn kho [sản phẩm thật] thành 50" → xác nhận hiện rõ SỐ TỒN HIỆN TẠI (best-effort) + số mới, "có" → thành công.
- [ ] **11.2 Disambiguation sản phẩm**: tên sản phẩm ngắn/trùng → hỏi chọn.
- [ ] **11.3 Có location**: "Cập nhật tồn kho [sản phẩm] tại kho [tên kho thật] thành 20" → resolve đúng location, không áp dụng nhầm kho mặc định.

## 12. Cơ chế confirm-gate

- [ ] **12.1 Biến thể "có"**: thử vài cách gõ đồng ý khác nhau qua các lần confirm khác nhau: "có", "ok", "đồng ý", "xác nhận" → đều CONFIRM đúng.
- [ ] **12.2 Biến thể "không"**: "không", "hủy", "thôi", "dừng lại" → đều CANCEL đúng.
- [ ] **12.3 Trả lời mơ hồ**: sau khi được hỏi xác nhận, gõ 1 câu KHÔNG rõ ràng, vd "cho mình đổi số lượng thành 5 được không?" → bot hỏi lại / xử lý như UNCLEAR, KHÔNG tự CONFIRM thao tác cũ với tham số cũ.
- [ ] **12.4 Hết hạn TTL**: bắt đầu 1 confirm-gate, đợi quá `CONFIRMATION_TTL_SECONDS` (mặc định 300s = 5 phút) rồi mới trả lời "có" → bot báo phiên xác nhận đã hết hạn, KHÔNG thực hiện thao tác cũ. (Case dài, có thể bỏ qua nếu không đủ thời gian test.)

## 13. Ngữ cảnh nhiều lượt (cùng 1 chat)

- [ ] **13.1 Context bias**: Lượt 1: hỏi/thao tác liên quan đơn A. Lượt 2 (không nhắc lại mã đơn): "sửa số lượng thành 3" → bot áp dụng đúng đơn A (dùng ngữ cảnh gần nhất).
- [ ] **13.2 Explicit override thắng context**: Tiếp theo 13.1, lượt 3: "sửa đơn B, số lượng 7" (nêu rõ mã đơn KHÁC) → bot áp dụng đúng đơn B, KHÔNG bị dính về đơn A dù vừa nhắc ở lượt trước (Invariant C).
- [ ] **13.3 Câu hỏi xác nhận hiện tường minh**: ở bất kỳ case ghi nào có dùng context ngầm định (13.1), câu hỏi xác nhận vẫn phải hiện rõ mã đơn/tool/args THẬT (không mơ hồ dựa theo tóm tắt của LLM).

## 14. Phiên hội thoại / thread scoping (R7, R9)

- [ ] **14.1 Chat mới sạch state**: bắt đầu 1 confirm-gate ở chat A (chưa trả lời có/không) → mở chat B MỚI, gửi tin nhắn bất kỳ → chat B hoạt động bình thường, KHÔNG bị dính pending confirm của chat A.
- [ ] **14.2 Quay lại chat cũ vẫn còn pending**: quay lại chat A ở 14.1, trả lời "có" → thao tác vẫn thực hiện đúng (state của A không bị mất khi B hoạt động song song).
- [ ] **14.3 Title tự sinh không phá state**: sau khi gửi tin nhắn ĐẦU TIÊN ở 1 chat mới (Open WebUI tự động sinh tiêu đề chat ở background) → state hội thoại thật không bị reset/xáo trộn bởi lệnh sinh tiêu đề đó (quan sát: tiêu đề chat tự đổi thành câu ngắn gọn, nội dung trả lời chính không bị ảnh hưởng).

## 15. Write-mode toggle (S3 kill-switch)

- [ ] **15.1** Vào Odoo, đổi `erp_ai.write_actions_enabled` → `false`, Save. Đợi >5s (KHÔNG restart backend). Thử 5.1 → bot báo "chưa được kích hoạt", KHÔNG thực hiện được.
- [ ] **15.2** Đổi lại `true`, đợi >5s, thử lại 5.1 → hoạt động bình thường trở lại, KHÔNG cần restart gì.
- [ ] **15.3** Đọc dữ liệu (nhóm 1) vẫn hoạt động bình thường trong lúc write-mode đang tắt (15.1) — write-gate chỉ chặn ghi, không chặn đọc.

## 16. Robustness — best-effort, không bắt buộc pass

- [ ] **16.1** Gõ 1 câu yêu cầu tạo đơn rất phức tạp/nhiều mệnh đề cùng lúc (nhiều sản phẩm, nhiều điều kiện) → quan sát xem planner LLM có trả JSON hỏng không; nếu có, `logs/planner_friction.jsonl` (KHÔNG phải `logs/backend.log` — logger.info không có handler nên không lên file nào) sẽ có 1 dòng JSON với `"outcome"` là `"salvage"`, `"retry_raw"`, hoặc `"retry_salvage"` (A5) — bot vẫn ra được câu hỏi xác nhận bình thường phía user dù có retry ngầm.
- [ ] **16.2** Nếu 16.1 không trigger được (LLM trả JSON sạch ngay), coi là pass — đây là test cơ chế phòng thủ, không phải hành vi bắt buộc quan sát được từ UI.

---

## Ghi log lỗi

Với mỗi case FAIL, ghi lại: câu gõ chính xác, phản hồi bot thật, kỳ vọng đúng phải là gì, và (nếu có) trích đoạn liên quan trong `logs/backend.log` / `logs/mcp.log`.
