"""real-time security monitoring for anomaly detection."""

import json
from collections import defaultdict

from voicr.audit import AuditLogger

import logging

logger = logging.getLogger("voicr.security")


class SecurityMonitor:
    """detects abuse patterns and potential attacks in real time."""

    def __init__(self, audit: AuditLogger):
        self.audit = audit
        self.alert_thresholds = {
            "failed_auth_per_ip": 5,
            "injection_attempts_per_session": 3,
            "messages_per_minute": 9999,
            "tool_calls_per_minute": 20,
        }
        self._counters: dict[str, dict] = defaultdict(
            lambda: {"count": 0, "window_start": 0.0}
        )

    def check_anomaly(self, event_type: str, identifier: str) -> bool:
        import time

        key = f"{event_type}:{identifier}"
        now = time.time()
        bucket = self._counters[key]

        if now - bucket["window_start"] > 60:
            bucket["count"] = 0
            bucket["window_start"] = now

        bucket["count"] += 1
        threshold = self.alert_thresholds.get(event_type, 100)

        if bucket["count"] > threshold:
            self._trigger_alert(event_type, identifier, bucket["count"])
            return True
        return False

    def _trigger_alert(
        self, event_type: str, identifier: str, count: int
    ) -> None:
        severity = (
            "CRITICAL"
            if count > self.alert_thresholds.get(event_type, 100) * 2
            else "HIGH"
        )
        alert = {
            "severity": severity,
            "type": event_type,
            "identifier": identifier,
            "count": count,
        }
        logger.critical("SECURITY ALERT: %s", json.dumps(alert))
        self.audit.log_security_event(
            "ALERT", identifier, json.dumps(alert)
        )
