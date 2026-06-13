"""pii detection and redaction for transcripts and logs."""

import re


PII_PATTERNS = {
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "phone_us": r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "credit_card": r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
    "ip_address": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
}


class PIIRedactor:
    """detects and redacts pii from text before logging or llm input."""

    @classmethod
    def redact(cls, text: str) -> tuple[str, list[dict]]:
        detections: list[dict] = []
        redacted = text

        for pii_type, pattern in PII_PATTERNS.items():
            for match in re.finditer(pattern, redacted):
                detections.append(
                    {
                        "type": pii_type,
                        "position": match.start(),
                        "length": len(match.group()),
                    }
                )
            redacted = re.sub(pattern, f"[{pii_type.upper()}_REDACTED]", redacted)

        return redacted, detections
