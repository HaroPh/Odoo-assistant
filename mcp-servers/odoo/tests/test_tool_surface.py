import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

REMOVED = ["get_late_orders", "search_orders", "get_inventory", "search_customers",
           "search_suppliers", "search_purchase_orders", "get_sales_summary",
           "get_top_products", "get_customer_invoices", "get_vendor_bills",
           "get_overdue_invoices", "get_deliveries", "get_receipts",
           "get_internal_transfers", "search_lots", "search_products",
           "get_sale_order_detail", "get_purchase_order_detail", "search_leads",
           "get_manufacturing_orders", "get_bom"]
KEPT = ["create_quotation", "create_rfq", "confirm_sale_order",
        "confirm_purchase_order", "post_invoice", "validate_picking",
        "inventory_adjustment", "create_invoice_from_order", "deliver_order",
        "receive_order", "create_bill_from_po", "internal_transfer"]


def test_read_tools_removed_do_tools_present(monkeypatch):
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"):
        monkeypatch.setenv(k, "x")
    import importlib, server
    importlib.reload(server)
    for gone in REMOVED:
        assert not hasattr(server, gone), f"{gone} should be removed"
    for kept in KEPT:
        assert hasattr(server, kept), f"{kept} should remain"
