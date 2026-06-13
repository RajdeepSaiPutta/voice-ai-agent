"""groq and gemini api clients with mock fallbacks."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import struct
from datetime import datetime

import httpx

from voicr.config import (
    GROQ_API_KEY,
    GROQ_STT_URL,
    GROQ_LLM_URL,
    GOOGLE_AI_KEY,
    GEMINI_TTS_URL,
    GEMINI_SEARCH_URL,
    ELEVENLABS_KEY,
    MOCK_MODE,
    TTS_VOICE,
    GEMINI_TTS_VOICE,
)

_KEYS_PATTERN = re.compile(r"([?&](?:key|token|api_key|apikey)=[^&]*)", re.IGNORECASE)

logger = logging.getLogger("voicr.api")


def _redact_url(url: str) -> str:
    """strip api keys/tokens from urls for safe logging."""
    return _KEYS_PATTERN.sub("?key=[REDACTED]", url)


def _redact_exception(exc: Exception) -> str:
    """return a safe string representation of an exception, with any urls redacted."""
    return _KEYS_PATTERN.sub("?key=[REDACTED]", str(exc)[:500])


# ---------------------------------------------------------------------------
# mock implementations
# ---------------------------------------------------------------------------

class MockGroqSTT:
    _COUNTER = 0
    _TRANSCRIPTS = [
        "Schedule a team meeting for tomorrow at 2pm.",
        "What's on my calendar for this week?",
        "Take a note. Remember to buy groceries after work.",
        "Cancel my 3 o'clock appointment.",
        "Read me my latest notes.",
    ]

    @classmethod
    async def transcribe(cls, audio_data: bytes) -> dict:
        await asyncio.sleep(0.3)
        text = cls._TRANSCRIPTS[cls._COUNTER % len(cls._TRANSCRIPTS)]
        cls._COUNTER += 1
        return {"text": text, "duration_ms": len(audio_data) / 32, "confidence": 0.95}


class MockGroqLLM:
    _RESPONSES = {
        "schedule": {
            "content": "I'll check your calendar and book that for you.",
            "tool_calls": [
                {
                    "id": "tc_001",
                    "type": "function",
                    "function": {
                        "name": "calendar_list_events",
                        "arguments": json.dumps({"start_date": "2026-06-14"}),
                    },
                }
            ],
        },
        "calendar": {
            "content": "Let me look at your calendar for this week.",
            "tool_calls": [
                {
                    "id": "tc_002",
                    "type": "function",
                    "function": {
                        "name": "calendar_list_events",
                        "arguments": json.dumps(
                            {"start_date": "2026-06-13", "end_date": "2026-06-20"}
                        ),
                    },
                }
            ],
        },
        "note": {
            "content": "Got it, I'll save that note for you.",
            "tool_calls": [
                {
                    "id": "tc_003",
                    "type": "function",
                    "function": {
                        "name": "fs_write_file",
                        "arguments": json.dumps(
                            {
                                "filepath": "notes/quick_note.md",
                                "content": "Remember to buy groceries after work.",
                                "mode": "append",
                            }
                        ),
                    },
                }
            ],
        },
        "default": {"content": "I understand. How can I help you with that?", "tool_calls": None},
    }

    @classmethod
    async def chat(cls, messages: list, tools: list | None = None) -> dict:
        await asyncio.sleep(0.5)
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        lower = last_user.lower()
        if any(w in lower for w in ("schedule", "book", "meeting")):
            resp = cls._RESPONSES["schedule"]
        elif any(w in lower for w in ("calendar", "week", "today")):
            resp = cls._RESPONSES["calendar"]
        elif any(w in lower for w in ("note", "remember", "save")):
            resp = cls._RESPONSES["note"]
        else:
            resp = cls._RESPONSES["default"]
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": resp["content"],
                        "tool_calls": resp["tool_calls"],
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 340,
                "completion_tokens": 45,
                "total_tokens": 385,
            },
        }


class MockGeminiTTS:
    @classmethod
    async def synthesize(cls, text: str) -> bytes:
        await asyncio.sleep(0.2)
        sample_rate = 24000
        duration = min(len(text) * 0.05, 5.0)
        num_samples = int(sample_rate * duration)
        audio = bytearray()
        for i in range(num_samples):
            t = i / sample_rate
            env = min(1.0, t * 10) * max(0.0, 1.0 - (t - duration + 0.1) * 10)
            sample = int(32767 * 0.3 * env * math.sin(2 * math.pi * 440 * t))
            audio.extend(struct.pack("<h", max(-32768, min(32767, sample))))
        return bytes(audio)


class MockGeminiSearch:
    """mock web search using gemini."""
    @classmethod
    async def search(cls, query: str) -> str:
        await asyncio.sleep(0.3)
        return (
            f"search results for: {query}\n"
            "- the latest information suggests positive trends\n"
            "- experts recommend checking multiple sources\n"
            "- data shows consistent growth in this area"
        )


def pcm16_to_wav(pcm_data: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """convert raw pcm int16 data to a wav file with headers."""
    bits_per_sample = 16
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_data)

    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF',
        36 + data_size,
        b'WAVE',
        b'fmt ',
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b'data',
        data_size,
    )
    return header + pcm_data


# ---------------------------------------------------------------------------
# real api clients
# ---------------------------------------------------------------------------

async def groq_stt(audio_data: bytes) -> dict:
    if MOCK_MODE:
        return await MockGroqSTT.transcribe(audio_data)
    wav_data = pcm16_to_wav(audio_data)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            GROQ_STT_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("audio.wav", wav_data, "audio/wav")},
            data={"model": "whisper-large-v3-turbo", "response_format": "json", "language": "en"},
        )
        resp.raise_for_status()
        result = resp.json()
        return {"text": result["text"], "confidence": 0.95}


async def groq_llm(messages: list, tools: list | None = None, max_tokens: int = 1024) -> dict:
    if MOCK_MODE:
        return await MockGroqLLM.chat(messages, tools)
    payload: dict = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            GROQ_LLM_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error("Groq LLM %s: %s", resp.status_code, resp.text[:500])
            logger.error("Groq LLM request messages: %s", json.dumps(messages, default=str, indent=2)[:2000])
        resp.raise_for_status()
        return resp.json()


async def _gemini_tts(text: str) -> bytes:
    """gemini tts with retry on rate limits."""
    payload = {
        "contents": [{"parts": [{"text": f"Please read this aloud: {text}"}]}],
        "generationConfig": {
            "response_modalities": ["AUDIO"],
            "speech_config": {
                "voice_config": {
                    "prebuilt_voice_config": {"voice_name": GEMINI_TTS_VOICE}
                }
            },
        },
    }
    last_err = None
    for attempt in range(2):
        if attempt > 0:
            await asyncio.sleep(1)
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                resp = await client.post(
                    f"{GEMINI_TTS_URL}?key={GOOGLE_AI_KEY}", json=payload
                )
                resp.raise_for_status()
                result = resp.json()
                audio_b64 = result["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
                import base64
                return base64.b64decode(audio_b64)
            except httpx.HTTPStatusError as e:
                last_err = e
                if e.response.status_code == 429:
                    continue
                raise RuntimeError(_redact_exception(e)) from None
    raise RuntimeError(f"Gemini TTS: rate limited after 2 attempts") from last_err


async def _elevenlabs_tts(text: str) -> bytes:
    """elevenlabs tts as fallback."""
    if not ELEVENLABS_KEY:
        raise ValueError("No ElevenLabs API key configured")
    url = "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM"
    headers = {
        "xi-api-key": ELEVENLABS_KEY,
        "Content-Type": "application/json",
    }
    payload = {"text": text, "model_id": "eleven_turbo_v2_5", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.content


async def _edge_tts(text: str, voice: str = "") -> bytes:
    """edge-tts using microsoft edge's free tts service."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice or TTS_VOICE)
    audio_chunks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
    if not audio_chunks:
        raise RuntimeError("edge-tts returned no audio")
    return b"".join(audio_chunks)


async def gemini_tts(text: str, voice: str = "") -> bytes:
    """tts with edge-tts primary, gemini fallback, elevenlabs last resort."""
    if MOCK_MODE:
        return await MockGeminiTTS.synthesize(text)

    # try edge-tts first (free, no API key needed)
    try:
        return await _edge_tts(text, voice=voice)
    except Exception as edge_err:
        logger.warning("edge-tts failed: %s", edge_err)

    # try gemini
    try:
        return await _gemini_tts(text)
    except Exception as gemini_err:
        logger.warning("gemini tts failed: %s", gemini_err)

    # try elevenlabs
    try:
        return await _elevenlabs_tts(text)
    except Exception:
        pass

    # all failed - raise the edge-tts error (most likely to succeed next time)
    raise RuntimeError("All TTS providers failed")


async def gemini_web_search(query: str) -> str:
    """search the web using gemini with google search grounding.
    returns summarized search results as text."""
    if MOCK_MODE:
        return await MockGeminiSearch.search(query)

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "extract the key facts, current data, and relevant details "
                            "for this query. return concise bullet points only, "
                            f"with no boilerplate: {query}"
                        )
                    }
                ]
            }
        ],
        "tools": [{"googleSearch": {}}],
        "generationConfig": {
            "maxOutputTokens": 1024,
            "temperature": 0.2,
        },
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                f"{GEMINI_SEARCH_URL}?key={GOOGLE_AI_KEY}", json=payload
            )
            resp.raise_for_status()
            result = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error("Gemini search HTTP %s: %s", e.response.status_code, _redact_exception(e))
            raise RuntimeError(_redact_exception(e)) from None
        except Exception as e:
            logger.error("Gemini search error: %s", _redact_exception(e))
            raise RuntimeError(_redact_exception(e)) from None

    # extract text from response
    candidates = result.get("candidates", [])
    if not candidates:
        logger.warning("Gemini search: no candidates in response")
        return "no search results found."

    parts = candidates[0].get("content", {}).get("parts", [])
    text_chunks = [p.get("text", "") for p in parts if "text" in p]
    raw_text = "\n".join(text_chunks).strip()

    if not raw_text:
        logger.warning("Gemini search: no text in response parts")
        return "no search results found."

    # dedupe lines
    seen = set()
    lines = []
    for line in raw_text.splitlines():
        cleaned = line.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            lines.append(cleaned)

    return "\n".join(lines)[:3000]


async def duckduckgo_search(query: str) -> str:
    """search the web using duckduckgo as a fallback."""
    if MOCK_MODE:
        return await MockGeminiSearch.search(query)

    from ddgs import DDGS

    try:
        results = list(DDGS().text(query, max_results=5))
        if not results:
            return "no search results found."
        lines = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            if title:
                lines.append(f"- {title}: {body}")
            elif body:
                lines.append(f"- {body}")
        return "\n".join(lines)[:3000]
    except Exception as e:
        logger.error("DuckDuckGo search error: %s", e)
        raise RuntimeError(f"Search unavailable: {e}") from None


async def web_search(query: str) -> str:
    """search the web with gemini primary, duckduckgo fallback."""
    try:
        return await gemini_web_search(query)
    except Exception:
        logger.info("Gemini search failed, falling back to DuckDuckGo")
        return await duckduckgo_search(query)
