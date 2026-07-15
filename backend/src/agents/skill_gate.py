"""ERP_SKILLS_ENABLED kill-switch — routing gate for the SOP skill pilot.
Mirrors the RAG_RERANK_ENABLED/ERP_SEMANTIC_RESOLVE env-var pattern (simple,
read fresh per call), not write_gate.py's heavier Odoo-parameter-backed
toggle — this flag decides routing for a developer-controlled experiment,
not a production security gate. See spec §3."""
import os


def skills_enabled() -> bool:
    return os.environ.get("ERP_SKILLS_ENABLED", "0") == "1"
