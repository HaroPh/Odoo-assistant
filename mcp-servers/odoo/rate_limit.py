"""Sliding-window in-memory rate limiter (spec 2026-07-13-mcp-server-
modularization). Port từ rate_limiting.py."""
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from config import RATE_LIMIT

_rate_cache: dict[str, list[datetime]] = defaultdict(list)
_rate_lock = threading.Lock()

def check_rate_limit(caller: str = "default") -> bool:
    """True = còn trong giới hạn; False = vượt limit."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=1)
    with _rate_lock:
        _rate_cache[caller] = [t for t in _rate_cache[caller] if t > cutoff]
        if len(_rate_cache[caller]) >= RATE_LIMIT:
            return False
        _rate_cache[caller].append(now)
        return True
