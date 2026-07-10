import os
import sys

# server.py reads these at import time; set dummies before any test imports it.
os.environ.setdefault("ODOO_URL", "http://dummy:8069")
os.environ.setdefault("ODOO_DB", "dummy")
os.environ.setdefault("ODOO_USERNAME", "dummy")
os.environ.setdefault("ODOO_PASSWORD", "dummy")

# Make `import server` work regardless of pytest's rootdir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
