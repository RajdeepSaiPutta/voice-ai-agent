"""token bucket rate limiter with subject-based and connection limits."""

from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class RateLimiter:
    """per-client and per-subject token bucket rate limiter with connection tracking."""

    def __init__(
        self,
        max_tokens: int = 20,
        refill_rate: float = 0.33,
    ):
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self._buckets: dict[str, dict] = defaultdict(
            lambda: {
                "tokens": float(max_tokens),
                "last_refill": time.time(),
            }
        )
        self._connections: dict[str, set[str]] = defaultdict(set)
        self._lock = Lock()

    def check(self, client_id: str) -> tuple[bool, int]:
        """returns (allowed, retry_after_seconds)."""
        if self.max_tokens >= 999:
            return True, 0
        bucket = self._buckets[client_id]
        now = time.time()

        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            self.max_tokens,
            bucket["tokens"] + elapsed * self.refill_rate,
        )
        bucket["last_refill"] = now

        if bucket["tokens"] < 1:
            retry_after = int((1 - bucket["tokens"]) / self.refill_rate) + 1
            return False, retry_after

        bucket["tokens"] -= 1
        return True, 0

    def check_subject(self, subject: str, max_rpm: int = 20) -> tuple[bool, int]:
        """rate limit by authenticated subject (user/client_id)."""
        if max_rpm >= 999:
            return True, 0
        key = f"subject:{subject}"
        bucket = self._buckets[key]
        now = time.time()
        refill = max_rpm / 60.0

        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            float(max_rpm),
            bucket["tokens"] + elapsed * refill,
        )
        bucket["last_refill"] = now

        if bucket["tokens"] < 1:
            retry_after = int((1 - bucket["tokens"]) / refill) + 1
            return False, retry_after

        bucket["tokens"] -= 1
        return True, 0

    def add_connection(self, client_ip: str, session_id: str, max_per_ip: int = 5) -> bool:
        """track a new connection. returns False if limit exceeded."""
        if max_per_ip >= 999:
            with self._lock:
                self._connections[client_ip].add(session_id)
            return True
        with self._lock:
            conns = self._connections[client_ip]
            if len(conns) >= max_per_ip:
                return False
            conns.add(session_id)
            return True

    def remove_connection(self, client_ip: str, session_id: str) -> None:
        """remove a connection from tracking."""
        with self._lock:
            conns = self._connections.get(client_ip)
            if conns:
                conns.discard(session_id)
                if not conns:
                    del self._connections[client_ip]

    def get_usage(self, client_id: str) -> dict:
        bucket = self._buckets[client_id]
        return {
            "remaining": int(bucket["tokens"]),
            "limit": self.max_tokens,
            "reset_in": int(
                (self.max_tokens - bucket["tokens"]) / self.refill_rate
            ),
        }
