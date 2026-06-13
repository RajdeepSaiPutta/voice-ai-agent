"""input validation and sanitization for all user-facing data."""

import re
import struct


class InputSanitizer:
    """validates and sanitizes audio payloads, text, and file paths."""

    MAX_AUDIO_SIZE = 960_000  # 30s of 16khz 16-bit mono pcm
    MIN_AUDIO_SIZE = 640     # 20ms
    MAX_TEXT_LENGTH = 1000

    @staticmethod
    def validate_audio(data: bytes) -> tuple[bool, str]:
        if len(data) > InputSanitizer.MAX_AUDIO_SIZE:
            return False, "Audio too large"
        if len(data) < InputSanitizer.MIN_AUDIO_SIZE:
            return False, "Audio too short"
        if len(data) % 2 != 0:
            return False, "Invalid PCM format"
        return True, "valid"

    @staticmethod
    def sanitize_text(text: str, max_length: int | None = None) -> str:
        limit = max_length or InputSanitizer.MAX_TEXT_LENGTH
        text = text.replace("\x00", "")
        text = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
        text = text[:limit]
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def sanitize_filepath(filepath: str) -> str:
        filepath = filepath.replace("..", "")
        filepath = filepath.replace("//", "/")
        filepath = filepath.lstrip("/")
        filepath = re.sub(r"[^a-zA-Z0-9\-_./]", "", filepath)
        return filepath
