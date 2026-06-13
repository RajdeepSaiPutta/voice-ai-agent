"""prompt injection detection - blocks manipulation attempts in user input."""

from __future__ import annotations

import re


INJECTION_PATTERNS = [
    r"(?i)ignore (all |your |previous )?instructions",
    r"(?i)forget (everything|your|all)",
    r"(?i)you are now",
    r"(?i)new instructions:",
    r"(?i)system prompt:",
    r"(?i)act as|pretend to be|roleplay as",
    r"(?i)reveal your (prompt|instructions|system)",
    r"(?i)<\|.*\|>",
    r"(?i)\[INST\]|\[/INST\]",
    r"(?i)<<SYS>>|<</SYS>>",
]


class PromptInjectionDetector:
    """detects and blocks prompt injection attempts in user text."""

    def check(self, text: str) -> tuple[bool, str]:
        """returns (is_safe, reason)."""
        for pattern in INJECTION_PATTERNS:
            if re.search(pattern, text):
                return False, f"Prompt injection detected: {pattern}"

        special_ratio = sum(1 for c in text if not c.isalnum() and c != " ") / max(
            len(text), 1
        )
        if special_ratio > 0.4:
            return False, "Excessive special characters detected"

        return True, "clean"
