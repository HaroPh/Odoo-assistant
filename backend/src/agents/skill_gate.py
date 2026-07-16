"""ERP_SKILLS_ENABLED — emergency routing kill-switch for tier-2 SOP skills.
Default ON (graduated from pilot flag after live verification 2026-07-15/16).
Set "0" to disable — the ONLY recognized off-value; any other value (or
unset) enables. Routing-level only: write safety is enforced independently
by write_gate.py (Odoo param, fail-closed) at the MCP layer."""
import os


def skills_enabled() -> bool:
    return os.environ.get("ERP_SKILLS_ENABLED", "1") != "0"
