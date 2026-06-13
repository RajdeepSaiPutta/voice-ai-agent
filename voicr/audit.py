"""immutable audit logger with integrity hashing and tamper detection."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import datetime
from pathlib import Path

from voicr.config import AUDIT_LOG_KEY


# fields to redact from audit logs
_SENSITIVE_FIELDS = {"content", "query", "text", "transcript", "arguments", "result"}


def _redact_value(value: str) -> str:
    """redact sensitive text, keeping first and last 2 chars."""
    if len(value) <= 4:
        return "[REDACTED]"
    return value[:2] + "[REDACTED]" + value[-2:]


def _redact_dict(d: dict) -> dict:
    """recursively redact sensitive fields in a dict."""
    redacted = {}
    for k, v in d.items():
        if k in _SENSITIVE_FIELDS and isinstance(v, str):
            redacted[k] = _redact_value(v)
        elif k in _SENSITIVE_FIELDS and isinstance(v, dict):
            redacted[k] = _redact_dict(v)
        elif isinstance(v, dict):
            redacted[k] = _redact_dict(v)
        elif isinstance(v, list):
            redacted[k] = [
                _redact_dict(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            redacted[k] = v
    return redacted


def _redact_query(query: str) -> str:
    """redact PII-like patterns from search queries."""
    query = re.sub(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", "[EMAIL]", query)
    query = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE]", query)
    query = re.sub(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", "[CARD]", query)
    query = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]", query)
    return query


class AuditLogger:
    """structured jsonl audit log with HMAC integrity and chain hashing."""

    def __init__(self, log_dir: str = "./data/audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_date: str = ""
        self._last_hash: str = ""
        self._key = (AUDIT_LOG_KEY or "").encode()

    @property
    def log_file(self) -> Path:
        today = datetime.utcnow().strftime("%Y%m%d")
        if today != self._current_date:
            self._current_date = today
            self._last_hash = ""
        return self.log_dir / f"audit_{today}.jsonl"

    def _compute_hash(self, entry_str: str) -> str:
        """compute HMAC-SHA256 for tamper detection."""
        if self._key:
            return hmac.new(self._key, entry_str.encode(), hashlib.sha256).hexdigest()[:16]
        return hashlib.sha256(entry_str.encode()).hexdigest()[:16]

    def log(self, event_type: str, session_id: str, details: dict, redact: bool = True) -> None:
        if redact:
            details = _redact_dict(details)

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "session_id": session_id,
            "prev_hash": self._last_hash,
            "details": details,
        }
        entry_str = json.dumps(entry, sort_keys=True)
        entry["hash"] = self._compute_hash(entry_str)
        self._last_hash = entry["hash"]

        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def log_tool_call(
        self,
        session_id: str,
        tool_name: str,
        args: dict,
        result: dict,
        latency_ms: float,
    ) -> None:
        safe_result = {"success": result.get("success", False)}
        if not result.get("success"):
            safe_result["error"] = result.get("error", "unknown")

        self.log(
            "TOOL_CALL",
            session_id,
            {
                "tool": tool_name,
                "arguments": args,
                "result": safe_result,
                "latency_ms": latency_ms,
            },
        )

    def log_tool_call_with_query(
        self,
        session_id: str,
        tool_name: str,
        query: str,
        result: dict,
        latency_ms: float,
    ) -> None:
        self.log(
            "TOOL_CALL",
            session_id,
            {
                "tool": tool_name,
                "query": _redact_query(query),
                "result": {"success": result.get("success", False)},
                "latency_ms": latency_ms,
            },
        )

    def log_auth_event(self, event: str, client_id: str, success: bool) -> None:
        self.log("AUTH", client_id, {"event": event, "success": success}, redact=False)

    def log_security_event(
        self, event: str, session_id: str, details: str
    ) -> None:
        self.log("SECURITY", session_id, {"event": event, "details": details}, redact=False)
