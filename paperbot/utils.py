"""Small helpers."""
from __future__ import annotations

import threading


def call_with_timeout(fn, limit: float, /, *args, **kwargs):
    """Run fn in a daemon thread and give up after `limit` seconds (hard wall-clock limit).

    Socket-level timeouts don't always fire (e.g. behind some proxies a TLS read can
    hang forever), so network calls go through this watchdog to keep the 1m loop alive.
    """
    box: dict = {}

    def target():
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 - re-raised in caller
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(limit)
    if t.is_alive():
        raise TimeoutError(f"{getattr(fn, '__name__', 'call')} exceeded {limit:.0f}s")
    if "error" in box:
        raise box["error"]
    return box.get("result")
