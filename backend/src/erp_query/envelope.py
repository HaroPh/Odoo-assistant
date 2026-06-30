"""The one output shape for every erp_query function: machine-readable `data`
plus a human `display`. Orchestration (C) reads `data`; the UI shows `display`."""


def ok(data, display: str) -> dict:
    return {"status": "success", "data": data, "display": display}


def err(message: str, display: str | None = None) -> dict:
    return {"status": "error", "data": None, "display": display or message, "error": message}
