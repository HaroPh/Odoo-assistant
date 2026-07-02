# backend/src/agents/disambiguation.py
"""Deterministic parser for a user's reply to a disambiguation interrupt.

The user is shown a numbered candidate list ("1. Azur Interior  2. Azur
Furniture") and replies with an index or a name. This maps the reply to a
candidate id, or None when it cannot be resolved cleanly (the caller re-asks).
No LLM — a write flow must not guess which entity was meant."""


def parse_selection(reply: str, options: list[dict]) -> int | None:
    s = (reply or "").strip().lower()
    if not s or not options:
        return None
    if s.isdigit():
        i = int(s)
        return options[i - 1]["id"] if 1 <= i <= len(options) else None
    exact = [o for o in options if (o["name"] or "").strip().lower() == s]
    if len(exact) == 1:
        return exact[0]["id"]
    subs = [o for o in options if s in (o["name"] or "").strip().lower()]
    if len(subs) == 1:
        return subs[0]["id"]
    return None
