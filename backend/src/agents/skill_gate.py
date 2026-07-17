"""ERP_SKILLS_ENABLED — emergency routing kill-switch for agentic SOP skills
(tier 2), plus the deterministic text-fold primitive that trigger matching
is built on. Default ON (graduated from pilot flag after live verification
2026-07-15/16). Set "0" to disable — the ONLY recognized off-value; any
other value (or unset) enables. Routing-level only: write safety is enforced
independently by write_gate.py (Odoo param, fail-closed) at the MCP layer."""
import os
import unicodedata


def skills_enabled() -> bool:
    return os.environ.get("ERP_SKILLS_ENABLED", "1") != "0"


_EXTRA_FOLD = str.maketrans("đĐ", "dD")


def _fold(s: str) -> str:
    # đ/Đ (U+0111/U+0110) are standalone Vietnamese letters with no NFD
    # decomposition — combining-mark stripping alone leaves them untouched,
    # unlike vowels with tone/horn marks (á, ơ...). Explicit translate closes
    # that gap (found 2026-07-16: a trigger phrase containing "đơn" silently
    # failed to match naturally-typed diacritic input).
    nfd = unicodedata.normalize("NFD", (s or "").lower())
    stripped = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return stripped.translate(_EXTRA_FOLD)
