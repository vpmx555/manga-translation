"""Persist uncertainty across process exits; an idle model listing is not completion proof."""
import json
import os
import time
from pathlib import Path

from ..storage.io import atomic_json, read_json


class InferenceUncertain(RuntimeError):
    """Do not admit more GPU work until the old server is proven stopped."""


def server_identity():
    try:
        path = os.environ.get("MANGA_LLM_SERVER_IDENTITY_FILE")
        if path:
            value = read_json(Path(path))
            return value if isinstance(value, dict) else None
        value = json.loads(os.environ.get("MANGA_LLM_SERVER_IDENTITY", "null"))
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError, OSError):
        return None


def identity_alive(identity):
    if not identity or os.name == "nt":
        return False
    try:
        root = Path(f"/proc/{identity['pid']}")
        stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
        return (stat[0] != "Z" and stat[19] == identity["start_ticks"]
                and str((root / "exe").resolve()) == identity["executable"])
    except (OSError, KeyError, IndexError):
        return False


def server_replaced(previous):
    current = server_identity()
    return bool(previous and current and previous != current
                and not identity_alive(previous) and not process_group_alive(previous.get("pid"))
                and identity_alive(current))


def process_group_alive(pgid):
    if os.name == "nt" or not isinstance(pgid, int):
        return True  # No Linux process evidence: fail closed.
    try:
        for root in Path("/proc").iterdir():
            if not root.name.isdigit():
                continue
            try:
                fields = (root / "stat").read_text().rsplit(")", 1)[1].split()
                if fields[0] != "Z" and int(fields[2]) == pgid:
                    return True
            except (OSError, ValueError, IndexError):
                continue
        return False
    except OSError:
        return True


def require_ready(path):
    path = Path(path)
    record = read_json(path) if path.exists() else {}
    if not isinstance(record, dict):
        raise InferenceUncertain("LLM inference paused: malformed pause receipt")
    if record.get("status") != "paused":
        return
    if not server_replaced(record.get("server_identity")):
        raise InferenceUncertain("LLM inference paused; previous request completion is uncertain. "
                                 "Inspect the saved receipt; no automatic server restart or duplicate request is allowed")
    atomic_json(path, {**record, "status": "resolved_by_external_server_replacement", "resolved_at": time.time()})


def recovery_proven(previous, path):
    record = read_json(Path(path)) if Path(path).exists() else {}
    return bool(previous and record.get("server_identity") == previous
                and str(record.get("status", "")).startswith("resolved_by_")
                and not identity_alive(previous) and not process_group_alive(previous.get("pid")))


def pause(path, *, request_key, reason):
    atomic_json(Path(path), {"status": "paused", "request_key": request_key,
                            "server_identity": server_identity(), "reason": str(reason)[:500],
                            "created_at": time.time()})
