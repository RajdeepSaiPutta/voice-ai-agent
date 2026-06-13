"""fallback audio streaming for degraded-mode operation."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import WebSocket

from voicr.config import ASSETS_DIR

logger = logging.getLogger("voicr.fallback")

FALLBACK_AUDIO_FILES = {
    "rate_limit": ASSETS_DIR / "one_moment_please.mp3",
    "stt_error": ASSETS_DIR / "could_not_hear.mp3",
    "llm_error": ASSETS_DIR / "processing_error.mp3",
    "tts_error": ASSETS_DIR / "response_ready_text_only.mp3",
    "general_error": ASSETS_DIR / "technical_difficulty.mp3",
}


async def play_fallback_audio(websocket: WebSocket, error_type: str) -> None:
    """stream pre-recorded fallback audio to client via websocket."""
    audio_path = FALLBACK_AUDIO_FILES.get(error_type, FALLBACK_AUDIO_FILES["general_error"])
    try:
        if audio_path.exists():
            with open(audio_path, "rb") as f:
                while chunk := f.read(4096):
                    await websocket.send_bytes(chunk)
        await websocket.send_json(
            {"type": "tts_complete", "fallback": True, "error_type": error_type}
        )
    except FileNotFoundError:
        logger.warning("Fallback audio file missing: %s", audio_path)
        await websocket.send_json(
            {
                "type": "error",
                "code": "FALLBACK_MISSING",
                "message": "Fallback audio not available.",
            }
        )
