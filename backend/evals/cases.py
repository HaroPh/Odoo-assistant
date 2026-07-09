# backend/evals/cases.py
"""Eval sets cho gate M3 (ADR-009) — đo model THẬT, không mock.

INTENT_CASES: (câu tiếng Việt, intent kỳ vọng) — 5 nhánh × 8.
CONFIRM_CASES: (reply, nhãn kỳ vọng) — cases chọn để NÉ keyword fast-path
(confirmation.py xử lý 'có/không/ok/hủy...' bằng keyword, không tới LLM),
nên eval này đo đúng chất lượng LLM fallback.
"""

INTENT_CASES = [
    # erp_read
    ("tồn kho Desk Pad còn bao nhiêu?", "erp_read"),
    ("liệt kê các đơn bán tháng này", "erp_read"),
    ("doanh thu tháng 6 là bao nhiêu?", "erp_read"),
    ("top 5 sản phẩm bán chạy nhất", "erp_read"),
    ("chi tiết đơn S00042", "erp_read"),
    ("khách Azure Interior có bao nhiêu đơn chưa thanh toán?", "erp_read"),
    ("hóa đơn nào đang quá hạn?", "erp_read"),
    ("còn lô nào của Large Cabinet trong kho không?", "erp_read"),
    # erp_write
    ("tạo báo giá cho Azure Interior, 2 Large Cabinet", "erp_write"),
    ("xác nhận đơn S00042", "erp_write"),
    ("giao hàng cho đơn S00040 luôn nhé", "erp_write"),
    ("đổi số lượng Desk Pad trong S00043 thành 5", "erp_write"),
    ("tạo đơn mua 10 Cabinet with Doors từ Wood Corner", "erp_write"),
    ("xuất hóa đơn cho đơn S00039", "erp_write"),
    ("điều chỉnh tồn kho Desk Pad về 100", "erp_write"),
    ("làm ơn tạo báo giá mới nhất cho khách Gemini Furniture, số lượng 3 bàn", "erp_write"),  # từng misroute
    # rag
    ("chính sách đổi trả hàng như thế nào?", "rag"),
    ("SLA giao hàng nội thành là bao lâu?", "rag"),
    ("quy trình xử lý khiếu nại khách hàng?", "rag"),
    ("hàng giảm giá có được hoàn trả không?", "rag"),
    ("điều kiện bảo hành sản phẩm gỗ?", "rag"),
    ("quy định về đặt cọc cho đơn hàng lớn?", "rag"),
    ("thời gian xử lý hoàn tiền là bao lâu?", "rag"),
    ("SOP nhập kho gồm những bước nào?", "rag"),
    # mixed
    ("đơn S00042 có được miễn phí giao không theo chính sách?", "mixed"),
    ("đơn của Azure Interior trễ SLA chưa?", "mixed"),
    ("đơn S00040 của khách này đủ điều kiện chiết khấu theo bảng giá không?", "mixed"),
    ("theo chính sách đổi trả, đơn S00035 còn hạn đổi không?", "mixed"),
    ("tồn kho Desk Pad có dưới ngưỡng cảnh báo trong SOP không?", "mixed"),
    ("đơn nào đang vi phạm SLA giao hàng?", "mixed"),
    ("giá trong đơn S00039 có khớp bảng giá hiện hành không?", "mixed"),
    ("khách Wood Corner có đơn nào vượt hạn mức công nợ theo chính sách không?", "mixed"),
    # unknown
    ("chào bạn", "unknown"),
    ("cảm ơn nhé", "unknown"),
    ("bạn là ai?", "unknown"),
    ("thời tiết hôm nay thế nào?", "unknown"),
    ("kể chuyện cười đi", "unknown"),
    ("1+1 bằng mấy?", "unknown"),
    ("bạn đang dùng model gì vậy?", "unknown"),
    ("hay đấy", "unknown"),
]

CONFIRM_CASES = [
    # CONFIRM kỳ vọng
    ("chốt luôn đi", "confirm"),
    ("vậy triển đi nhé", "confirm"),
    ("gật", "confirm"),
    ("duyệt nhé", "confirm"),
    ("cứ thế mà làm", "confirm"),
    ("chốt đơn giùm mình", "confirm"),
    ("êm, quất luôn", "confirm"),
    ("lên đơn đi bạn", "confirm"),
    # CANCEL kỳ vọng
    ("để sau đi", "cancel"),
    ("từ từ đã", "cancel"),
    ("chưa vội đâu", "cancel"),
    ("đợi mình xem lại đã", "cancel"),
    ("bỏ qua giùm", "cancel"),
    ("để mình suy nghĩ thêm", "cancel"),
    ("hôm khác làm", "cancel"),
    ("sai rồi, làm lại cái khác", "cancel"),
    # UNCLEAR kỳ vọng (câu hỏi / yêu cầu sửa / mơ hồ)
    ("giá này rẻ hơn hôm qua à?", "unclear"),
    ("đơn này của khách nào vậy?", "unclear"),
    ("2 cái hay 3 cái nhỉ?", "unclear"),
    ("bạn nghĩ sao?", "unclear"),
    ("quy trình này hoạt động ra sao?", "unclear"),
    ("cho mình đổi thành 5 cái được chứ?", "unclear"),
    ("hmm để coi", "unclear"),
    ("à mà giá bao nhiêu ấy nhỉ?", "unclear"),
]

# ── Chitchat eval-gate (khóa #10, ADR-009) ────────────────────────────────────
# respond_unknown() KHÔNG có system prompt (backend/src/agents/nodes.py) — nếu
# router misroute 1 yêu cầu ERP thật vào đây, model có thể bịa đã thực hiện
# hành động (không tool nào chạy thật — rủi ro trust/UX, không phải data-sai).
# Gate tuyệt đối: violations phải = 0. Heuristic từ khóa, không LLM-judge —
# residual: có thể bỏ sót cách diễn đạt không khớp danh sách (spec §7).

HALLUCINATION_MARKERS = [
    "đã tạo", "đã xác nhận", "đã hủy", "đã huỷ", "đã giao", "đã nhận hàng",
    "đã xuất hóa đơn", "đã xuất hoá đơn", "đã điều chỉnh", "đã cập nhật",
    "đã lưu", "hoàn tất giao dịch", "thực hiện thành công", "giao dịch thành công",
]

CHITCHAT_CASES = (
    # Nhóm A: chit-chat thật (tái dùng đúng 8 câu nhãn "unknown" trong
    # INTENT_CASES — tránh trùng lặp nội dung, cùng input, khác tầng kiểm tra).
    [text for text, label in INTENT_CASES if label == "unknown"]
    # Nhóm B: near-miss — hình dạng giống yêu cầu ERP thật, đủ mơ hồ để có thể
    # bị router misroute vào "unknown" (input thực tế nhất có thể chạm node này).
    + [
        "chốt đơn kia luôn nhé",
        "giao hàng được chưa vậy",
        "hủy giùm cái đơn lúc nãy",
        "tồn kho còn không ta",
        "báo giá xong chưa vậy",
        "đơn đó sao rồi",
        "làm luôn đi đừng hỏi nữa",
        "cập nhật giúp cái kia với",
    ]
)
