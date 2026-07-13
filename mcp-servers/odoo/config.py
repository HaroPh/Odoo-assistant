"""Env-derived config cho Odoo MCP server (spec 2026-07-13-mcp-server-
modularization)."""
import os

ODOO_URL  = os.environ["ODOO_URL"]       # http://host.docker.internal:8069
ODOO_DB   = os.environ["ODOO_DB"]        # odoo
ODOO_USER = os.environ["ODOO_USERNAME"]  # phamhao14170@gmail.com
ODOO_PWD  = os.environ["ODOO_PASSWORD"]

RATE_LIMIT    = int(os.environ.get("MCP_RATE_LIMIT", "60"))   # calls/phút
DATABASE_URL  = os.environ.get("DATABASE_URL")                # log nếu có; không thì skip
