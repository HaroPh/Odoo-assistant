# **Báo cáo Nghiên cứu: Các Quy trình Nghiệp vụ Cơ bản trong Odoo ERP**

## **1\. Tổng quan**

Odoo ERP là một hệ thống quản trị doanh nghiệp toàn diện với kiến trúc module linh hoạt. Sự liên kết chặt chẽ giữa các module giúp luồng dữ liệu di chuyển liền mạch qua các phòng ban. Các quy trình cơ bản trong Odoo thường xoay quanh chuỗi cung ứng lõi: Bán hàng (Sales), Mua hàng (Purchase), Quản lý Kho (Inventory), Kế toán (Accounting) và Sản xuất (Manufacturing)1.

## **2\. Quản lý Quan hệ Khách hàng (CRM) và Bán hàng (Sales)**

Quy trình cơ bản của bộ phận kinh doanh bắt đầu từ việc tiếp cận khách hàng cho đến khi chốt đơn hàng:

* **Quản lý Khách hàng tiềm năng (Leads/Opportunities):** Ghi nhận thông tin khách hàng từ nhiều nguồn khác nhau. Nhân viên kinh doanh sẽ theo dõi tiến trình chăm sóc và chuyển đổi (convert) Lead thành Cơ hội (Opportunity) khi khách hàng có nhu cầu thực tế3.  
* **Báo giá và Đơn bán hàng:** Từ Cơ hội, hệ thống cho phép tạo Báo giá (Quotation) để gửi cho khách hàng. Khi khách hàng đồng ý, Báo giá được xác nhận và chuyển trạng thái thành Đơn bán hàng (Sale Order)4. Khi Đơn bán hàng được xác nhận, hệ thống sẽ tự động tạo một Lệnh giao hàng cho bộ phận Kho để chuẩn bị xuất hàng.

## **3\. Quản lý Mua hàng (Purchase)**

Quy trình mua hàng chuẩn giúp doanh nghiệp kiểm soát chi phí đầu vào và đảm bảo nguồn cung cho sản xuất hoặc thương mại:

* **Yêu cầu Báo giá (RFQ):** Khi có nhu cầu vật tư, nhân viên mua hàng tạo Yêu cầu Báo giá để gửi đến nhà cung cấp, trong đó ghi rõ danh sách sản phẩm, số lượng và ngày giao hàng mong muốn5.  
* **Đơn mua hàng (Purchase Order):** Sau khi chốt được giá và điều kiện với nhà cung cấp, RFQ sẽ được xác nhận thành Đơn mua hàng (PO). Tương tự như quy trình bán hàng, việc xác nhận PO sẽ tự động kích hoạt một Phiếu nhận hàng (Receipt) bên phân hệ Kho5.

## **4\. Quản lý Kho vận (Inventory)**

Odoo áp dụng phương pháp quản lý kho theo nguyên tắc "ghi sổ kép" (double-entry inventory). Điều này có nghĩa là hàng hóa không bao giờ biến mất mà chỉ dịch chuyển từ vị trí này (source location) sang vị trí khác (destination location)7.

* **Nhập kho và Xuất kho:** Bộ phận kho tiếp nhận hàng hóa từ nhà cung cấp thông qua Phiếu nhận hàng (Receipts), hoặc xuất giao hàng cho khách thông qua Phiếu giao hàng (Deliveries)7.  
* **Điều chuyển nội bộ (Internal Transfers):** Dịch chuyển hàng hóa giữa các nhà kho khác nhau của công ty hoặc giữa các khu vực lưu trữ trong cùng một kho7.  
* **Quản lý Lô và Số Sê-ri (Lots & Serial Numbers):** Tính năng truy xuất nguồn gốc thiết yếu. Số sê-ri được dùng để theo dõi từng đơn vị sản phẩm độc lập (như thiết bị điện tử), trong khi Lô được dùng để quản lý một mẻ sản phẩm được sản xuất hoặc nhập cùng lúc. Việc quản lý Lô thường đi kèm với việc theo dõi Ngày hết hạn (Expiration Dates)9.  
* **Kiểm kê kho (Inventory Adjustments):** Định kỳ, thủ kho sẽ tiến hành đếm số lượng thực tế trên kệ và nhập vào hệ thống để cập nhật lại số lượng tồn kho (On-hand Quantity), ghi nhận các hao hụt hoặc hư hỏng12.

## **5\. Quy trình Xuất Nhập khẩu (Import/Export) & Chi phí về kho (Landed Costs)**

Đối với các doanh nghiệp có hoạt động giao thương quốc tế, Odoo cung cấp các luồng định tuyến (routing) hàng hóa qua các khu vực hải quan để đảm bảo tính tuân thủ pháp lý:

* **Quy trình Nhập khẩu:** Khi xác nhận một Đơn mua hàng từ đối tác nước ngoài, hàng hóa sẽ không đi thẳng vào kho nội bộ. Thay vào đó, hàng được nhận vào một địa điểm ảo là Khu vực Nhập khẩu và Thông quan (Import \- Custom Zone). Người dùng phải tạo Hồ sơ thông quan (Customs clearance document). Chỉ khi hồ sơ này được duyệt và các nghĩa vụ thuế hoàn tất, hệ thống mới cho phép điều chuyển hàng từ khu vực thông quan vào kho lưu trữ chính14.  
* **Quy trình Xuất khẩu:** Tương tự như nhập khẩu, Đơn bán hàng quốc tế sẽ định tuyến hàng hóa từ kho nội bộ ra Khu vực Xuất khẩu (Export \- Custom Zone) chờ thông quan trước khi giao cho đối tác16.  
* **Phân bổ Chi phí về kho (Landed Costs):** Để tính chính xác Giá vốn hàng bán (COGS), các chi phí phụ trợ như cước vận tải biển, phí bảo hiểm, và thuế hải quan có thể được cộng dồn vào giá trị hàng tồn kho. Odoo hỗ trợ phân bổ các chi phí này theo giá trị (Value), khối lượng (Weight), hoặc thể tích (Volume) của từng mặt hàng trong lô17.

## **6\. Kế toán và Tài chính (Accounting)**

Phân hệ Kế toán giúp doanh nghiệp theo dõi sức khỏe tài chính và thực hiện việc hạch toán:

* **Hóa đơn (Invoicing):** Kế toán tạo Hóa đơn khách hàng (Customer Invoices) để ghi nhận doanh thu và Hóa đơn nhà cung cấp (Vendor Bills) để ghi nhận chi phí.  
* **Đối chiếu 3 bước (3-way matching):** Một quy trình kiểm soát chặt chẽ để đảm bảo doanh nghiệp không thanh toán thừa. Kế toán sẽ đối chiếu tính khớp nhau giữa Đơn mua hàng (PO), Phiếu nhận hàng thực tế ở kho (Receipt) và Hóa đơn do nhà cung cấp gửi đến (Bill)20.  
* **Đối chiếu Ngân hàng (Bank Reconciliation):** Nhập sao kê ngân hàng vào hệ thống và đối chiếu các khoản tiền gửi/rút với các hóa đơn thanh toán tương ứng trong Odoo21.

## **7\. Quản lý Sản xuất (Manufacturing)**

Dành cho các doanh nghiệp có hoạt động chế biến, lắp ráp:

* **Định mức Nguyên vật liệu (BOM):** Khai báo công thức sản xuất. Ví dụ để sản xuất ra 1 sản phẩm A, cần tiêu hao 2 linh kiện B và 1 linh kiện C22.  
* **Lệnh sản xuất (Manufacturing Orders):** Quản đốc xưởng khởi tạo lệnh sản xuất dựa trên BOM. Khi lệnh sản xuất được đánh dấu hoàn tất (Done), hệ thống sẽ tự động trừ đi số lượng linh kiện thô trong kho và tăng số lượng thành phẩm tương ứng5.

#### **Nguồn trích dẫn**

1. Odoo Là Gì? Khám Phá Giải Pháp ERP Toàn Diện Cho Doanh Nghiệp \- Mageplaza, [https://www.mageplaza.com/vi/blog/odoo-la-gi/](https://www.mageplaza.com/vi/blog/odoo-la-gi/)  
2. Phần Mềm Odoo Là Gì? Toàn Tập Về Nền Tảng ERP Toàn Diện Cho Doanh Nghiệp, [https://lctech.vn/phan-mem-odoo-la-gi/](https://lctech.vn/phan-mem-odoo-la-gi/)  
3. Lead Management in Odoo 17 CRM | Odoo 17 Community Book \- Cybrosys Technologies, [https://www.cybrosys.com/odoo/odoo-books/v17-ce/crm/lead-management/](https://www.cybrosys.com/odoo/odoo-books/v17-ce/crm/lead-management/)  
4. Creating a New Opportunity from a Lead in Odoo 17 CRM \- Cybrosys Technologies, [https://www.cybrosys.com/odoo/odoo-books/v17-ce/crm/creating-a-new-opportunity-from-a-lead/](https://www.cybrosys.com/odoo/odoo-books/v17-ce/crm/creating-a-new-opportunity-from-a-lead/)  
5. Purchase Management \- odoo-17-book \- Cybrosys Technologies, [https://www.cybrosys.com/odoo/odoo-books/v17-ce/purchase/purchase-management/](https://www.cybrosys.com/odoo/odoo-books/v17-ce/purchase/purchase-management/)  
6. Purchase Order in Odoo 17 Purchase | Odoo v17 Enterprise Edition Book, [https://www.cybrosys.com/odoo/odoo-books/v17/purchase/purchase-order/](https://www.cybrosys.com/odoo/odoo-books/v17/purchase/purchase-order/)  
7. Quản lý kho và chuỗi cung ứng với Odoo/ERPOnline, [https://erponline.vn/vi/docs/13.0/d/quan-ly-kho-va-chuoi-cung-ung-voi-odoo-erponline-527](https://erponline.vn/vi/docs/13.0/d/quan-ly-kho-va-chuoi-cung-ung-voi-odoo-erponline-527)  
8. Phần mềm Kho Odoo: Thiết lập quy tắc cung ứng \- ERPViet, [https://erpviet.vn/c-thiet-lap-quy-tac-cung-ung/](https://erpviet.vn/c-thiet-lap-quy-tac-cung-ung/)  
9. Lot numbers — Odoo 19.0 documentation, [https://www.odoo.com/documentation/19.0/applications/inventory\_and\_mrp/inventory/product\_management/product\_tracking/lots.html](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/product_management/product_tracking/lots.html)  
10. Use lots to manage groups of products — Odoo 15.0 documentation, [https://www.odoo.com/documentation/15.0/applications/inventory\_and\_mrp/inventory/management/lots\_serial\_numbers/lots.html](https://www.odoo.com/documentation/15.0/applications/inventory_and_mrp/inventory/management/lots_serial_numbers/lots.html)  
11. Odoo Lot & Serial Number Tracking: Complete Traceability Guide 2026, [https://www.odooskillz.com/blog/odoo-skillz-insights-1/odoo-lot-serial-number-tracking-complete-traceability-guide-354](https://www.odooskillz.com/blog/odoo-skillz-insights-1/odoo-lot-serial-number-tracking-complete-traceability-guide-354)  
12. Nhập kho và giao hàng một bước — Tài liệu Odoo 19.0, [https://www.odoo.com/documentation/19.0/vi/applications/inventory\_and\_mrp/inventory/shipping\_receiving/daily\_operations/receipts\_delivery\_one\_step.html](https://www.odoo.com/documentation/19.0/vi/applications/inventory_and_mrp/inventory/shipping_receiving/daily_operations/receipts_delivery_one_step.html)  
13. Use serial numbers to track products — Odoo 17.0 documentation, [https://www.odoo.com/documentation/17.0/applications/inventory\_and\_mrp/inventory/product\_management/product\_tracking/serial\_numbers.html](https://www.odoo.com/documentation/17.0/applications/inventory_and_mrp/inventory/product_management/product_tracking/serial_numbers.html)  
14. Nhập khẩu hàng hóa trong Odoo ERPOnline, [https://erponline.vn/vi/docs/13.0/d/cac-buoc-nhap-khau-hang-hoa-voi-odoo-erponline-2095](https://erponline.vn/vi/docs/13.0/d/cac-buoc-nhap-khau-hang-hoa-voi-odoo-erponline-2095)  
15. Steps to import goods with Odoo/ERPOnline, [https://erponline.vn/docs/13.0/d/steps-to-import-goods-with-odoo-erponline-2041](https://erponline.vn/docs/13.0/d/steps-to-import-goods-with-odoo-erponline-2041)  
16. Steps to export goods with Odoo/ERPOnline, [https://erponline.vn/docs/13.0/d/steps-to-export-goods-with-odoo-erponline-2042](https://erponline.vn/docs/13.0/d/steps-to-export-goods-with-odoo-erponline-2042)  
17. Landed Cost Calculation 2026: Import Duties Formula \+ Templates | ECOSIRE, [https://ecosire.com/blog/landed-cost-calculation-import-duties](https://ecosire.com/blog/landed-cost-calculation-import-duties)  
18. Landed costs — Odoo 19.0 documentation, [https://www.odoo.com/documentation/19.0/applications/inventory\_and\_mrp/inventory/inventory\_valuation/landed\_costs.html](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/inventory_valuation/landed_costs.html)  
19. Integrating additional costs to products (landed costs) — Odoo 15.0 documentation, [https://www.odoo.com/documentation/15.0/applications/inventory\_and\_mrp/inventory/management/reporting/integrating\_landed\_costs.html](https://www.odoo.com/documentation/15.0/applications/inventory_and_mrp/inventory/management/reporting/integrating_landed_costs.html)  
20. Odoo Purchase: Phần mềm quản lý mua hàng hiệu quả \- A1 Consulting, [https://www.a1consulting.vn/odoo-purchase](https://www.a1consulting.vn/odoo-purchase)  
21. Báo cáo nhóm 4 về Kế toán & Tài chính trong Odoo \- Môn ERP \- Studocu, [https://www.studocu.vn/vn/document/truong-dai-hoc-kinh-te-thanh-pho-ho-chi-minh/erp-scm/odoo-accounting-group-4/41785872](https://www.studocu.vn/vn/document/truong-dai-hoc-kinh-te-thanh-pho-ho-chi-minh/erp-scm/odoo-accounting-group-4/41785872)  
22. Bill of Materials in Odoo 19: Multi-Level BoM, Kits & Phantom Assemblies \- Octura Solutions, [https://octurasolutions.com/resources/odoo-19-bill-of-materials-multi-level-bom-kits-and-phantom-assemblies](https://octurasolutions.com/resources/odoo-19-bill-of-materials-multi-level-bom-kits-and-phantom-assemblies)  
23. Hướng Dẫn Sử Dụng Odoo cho Dự Án Mô Hình Kinh Doanh Thực Phẩm Chay \- Studocu, [https://www.studocu.vn/vn/document/truong-dai-hoc-fpt/quan-tri-thuong-hieu/huong-dan-su-dung-odoo-cho-du-an-mo-hinh-kinh-doanh-thuc-pham-chay/127216168](https://www.studocu.vn/vn/document/truong-dai-hoc-fpt/quan-tri-thuong-hieu/huong-dan-su-dung-odoo-cho-du-an-mo-hinh-kinh-doanh-thuc-pham-chay/127216168)