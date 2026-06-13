"""circuit breaker pattern for external api fault tolerance."""

from __future__ import annotations

import asyncio
import time
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    pass


class CircuitBreaker:
    """circuit breaker wrapping async external api calls.

    states:
    - closed: normal. failures increment counter.
    - open: all calls rejected. fallback activated. timer starts.
    - half_open: after cooldown, allow one test request.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout_s: int = 60,
        call_timeout_s: float = 10.0,
    ):
        self.name = name
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.call_timeout_s = call_timeout_s
        self._last_failure: float = 0.0

    async def call(self, func, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.state == CircuitState.OPEN:
            if self._should_attempt_recovery():
                self.state = CircuitState.HALF_OPEN
            else:
                raise CircuitBreakerOpen(
                    f"{self.name} circuit OPEN. "
                    f"Retry in {self._time_until_recovery()}s"
                )

        try:
            result = await asyncio.wait_for(
                func(*args, **kwargs), timeout=self.call_timeout_s
            )
            self._on_success()
            return result
        except CircuitBreakerOpen:
            raise
        except Exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        self.failure_count += 1
        self._last_failure = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN

    def _should_attempt_recovery(self) -> bool:
        return (time.monotonic() - self._last_failure) >= self.recovery_timeout_s

    def _time_until_recovery(self) -> int:
        elapsed = time.monotonic() - self._last_failure
        return max(0, int(self.recovery_timeout_s - elapsed))
