"""pipeline state machine and session state container."""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import WebSocket


class PipelineState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PROCESSING_STT = "PROCESSING_STT"
    PROCESSING_LLM = "PROCESSING_LLM"
    TOOL_CALLING = "TOOL_CALLING"
    PROCESSING_TTS = "PROCESSING_TTS"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"


class SessionState:
    """per-session mutable state container."""

    def __init__(self, session_id: str, websocket: WebSocket):
        self.session_id = session_id
        self.websocket = websocket
        self.state = PipelineState.IDLE
        self.audio_buffer = bytearray()
        self.conversation_history: list[dict] = []
        self.tts_task: asyncio.Task | None = None
        self.current_llm_response = ""
        self.created_at = datetime.utcnow()
        self.metrics: dict = {
            "vad_ms": 0,
            "stt_ms": 0,
            "llm_ttft_ms": 0,
            "llm_total_ms": 0,
            "tts_ttfb_ms": 0,
            "e2e_ms": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "tpm_used": 0,
        }
        self.client_ip = ""
        self.subject = ""
        self.role = "user"
        self.tts_voice: str = ""


# global session registry
sessions: dict[str, SessionState] = {}
