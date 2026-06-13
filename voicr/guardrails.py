"""output guardrail - validates and sanitizes llm output before tts."""

from __future__ import annotations

import re


FORBIDDEN_PATTERNS = [
    (r"\$\d+", "Price references"),
    (r"(?i)premium|pro plan|upgrade|subscribe", "Paid feature mentions"),
    (r"(?i)credit card|payment|billing", "Payment references"),
    (r"(?i)guarantee|100%|always works", "Absolute guarantees"),
    (r"\b\d{3}-\d{3}-\d{4}\b", "Phone numbers (PII)"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Emails (PII)"),
    (r"(?i)i('m| am) (sure|certain|positive) that", "Overconfidence"),
]

BLACKLIST_PHRASES = [
    "as an AI language model",
    "I cannot access the internet",
    "I don't have real-time",
    "my training data",
    "as of my last update",
]


class OutputGuardrail:
    """validates and cleans llm output before sending to tts."""

    def validate(self, text: str) -> tuple[str, list[str]]:
        warnings: list[str] = []
        cleaned = text

        for pattern, label in FORBIDDEN_PATTERNS:
            if re.search(pattern, cleaned):
                warnings.append(f"Forbidden pattern: {label}")
                cleaned = re.sub(pattern, "[REDACTED]", cleaned)

        for phrase in BLACKLIST_PHRASES:
            if phrase.lower() in cleaned.lower():
                warnings.append(f"Blacklisted phrase: {phrase}")
                cleaned = cleaned.replace(phrase, "")

        words = cleaned.split()
        if len(words) > 100:
            cleaned = " ".join(words[:100]) + "..."
            warnings.append("Response truncated to 100 words for voice output")

        return cleaned.strip(), warnings
