"""
voicr - production orchestrator
================================
run: python app.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from voicr.config import (
    MOCK_MODE,
    HOST,
    PORT,
    LOG_LEVEL,
    CORS_ORIGINS,
    MAX_AUDIO_SIZE_BYTES,
    MAX_AUDIO_BUFFER_BYTES,
    MAX_MESSAGES_PER_MINUTE,
    MAX_CONNECTIONS_PER_IP,
    AUTH_REQUIRED,
)
from voicr.state import PipelineState, SessionState, sessions
from voicr.api_clients import groq_stt, groq_llm, gemini_tts
from voicr.mcp_router import MCPRouter
from voicr.prompts import build_system_prompt
from voicr.tools_schemas import TOOL_SCHEMAS
from voicr.auth import AuthManager
from voicr.rate_limiter import RateLimiter
from voicr.pii import PIIRedactor
from voicr.audit import AuditLogger
from voicr.sanitizer import InputSanitizer
from voicr.security import SecurityMonitor
from voicr.guardrails import OutputGuardrail
from voicr.injection import PromptInjectionDetector
from voicr.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from voicr.fallback import play_fallback_audio

# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("voicr")


def _redact_url(url: str) -> str:
    """redact query strings and API keys from URLs for safe logging."""
    url = re.sub(r"[?&]key=[^&]*", "?key=[REDACTED]", url)
    url = re.sub(r"[?&]token=[^&]*", "?token=[REDACTED]", url)
    return url


_REDACT_PATTERN = re.compile(r"([?&](?:key|token|api_key|apikey)=[^&]*)", re.IGNORECASE)


def _redact(text: str) -> str:
    """strip api keys/tokens from any string for safe logging or client display."""
    return _REDACT_PATTERN.sub("?key=[REDACTED]", text)


def _clean_response(text: str) -> str:
    """strip tool-call-like patterns the model sometimes emits as text."""
    import re
    # strip patterns like {web_search.query="..."} or {function_name {args}}
    text = re.sub(r'\{[a-z_]+\.[a-z_]+="[^"]*"\}', '', text)
    text = re.sub(r'\{[a-z_]+\([^)]*\)\}', '', text)
    # strip bare tool names like {web_search} or {calendar_list_events}
    text = re.sub(r'\{[a-z_]+\}', '', text)
    # strip <function=name ...> and </function> patterns
    text = re.sub(r'</?function[^>]*>', '', text)
    # strip ```function ...``` patterns
    text = re.sub(r'```function[^`]*```', '', text)
    # strip any remaining XML/HTML-like tags
    text = re.sub(r'</?[a-z]+[^>]*>', '', text)
    # collapse multiple spaces/newlines
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# ---------------------------------------------------------------------------
# shared singletons
# ---------------------------------------------------------------------------
audit = AuditLogger()
security_monitor = SecurityMonitor(audit)
rate_limiter = RateLimiter(max_tokens=MAX_MESSAGES_PER_MINUTE, refill_rate=MAX_MESSAGES_PER_MINUTE / 60.0)
mcp_router = MCPRouter(audit)
output_guardrail = OutputGuardrail()
injection_detector = PromptInjectionDetector()
auth_manager = AuthManager()

# circuit breakers for external services
stt_breaker = CircuitBreaker("groq_stt", failure_threshold=5, recovery_timeout_s=30)
llm_breaker = CircuitBreaker("groq_llm", failure_threshold=5, recovery_timeout_s=30)
tts_breaker = CircuitBreaker("gemini_tts", failure_threshold=5, recovery_timeout_s=30)

# ---------------------------------------------------------------------------
# fastapi app
# ---------------------------------------------------------------------------
app = FastAPI(title="voicr", version="1.0.0")

# --- CORS validation ---
if "*" in CORS_ORIGINS and len(CORS_ORIGINS) > 1:
    logger.warning("CORS_ORIGINS contains wildcard with other origins; filtering wildcard")
    CORS_ORIGINS = [o for o in CORS_ORIGINS if o != "*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials="*" not in CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["authorization", "content-type", "sec-websocket-protocol"],
)


# --- security headers middleware ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/") and not request.url.path.startswith("/ws"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "connect-src 'self' ws: wss:; "
                "img-src 'self' data:; "
                "media-src 'self' blob:"
            )
        return response


app.add_middleware(SecurityHeadersMiddleware)


# serve dashboard if present
_index = Path(__file__).resolve().parent / "index.html"


@app.get("/favicon.ico")
async def favicon():
    from fastapi.responses import Response
    return Response(content=b"", media_type="image/x-icon")


@app.get("/")
async def serve_dashboard():
    if _index.exists():
        return FileResponse(_index)
    return {"message": "voicr API is running. Dashboard not found."}


@app.get("/api/health")
async def health_check():
    # minimal public health response - no sensitive details
    return {"status": "ok"}


@app.get("/api/health/details")
async def health_details(request: Request):
    # admin-only detailed health
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Authentication required"})
    token = auth_header[7:]
    try:
        payload = AuthManager.verify_token(token)
        if payload.get("role") != "admin":
            return JSONResponse(status_code=403, content={"error": "Admin access required"})
    except Exception:
        return JSONResponse(status_code=401, content={"error": "Invalid token"})

    return {
        "status": "healthy",
        "mock_mode": MOCK_MODE,
        "active_sessions": len(sessions),
        "version": "1.0.0",
    }


@app.get("/api/sessions")
async def list_sessions(request: Request):
    # require authentication
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(status_code=401, content={"error": "Authentication required"})
    token = auth_header[7:]
    try:
        payload = AuthManager.verify_token(token)
    except Exception:
        return JSONResponse(status_code=401, content={"error": "Invalid token"})

    subject = payload.get("sub", "unknown")
    role = payload.get("role", "user")

    return {
        "sessions": [
            {
                "id": sid,
                "state": s.state.value,
                "created_at": s.created_at.isoformat(),
                "messages": len(s.conversation_history),
            }
            for sid, s in sessions.items()
            if role == "admin" or s.session_id.startswith(subject[:8])
        ]
    }


@app.get("/api/voices")
async def list_voices():
    """list available edge-tts voices."""
    import edge_tts
    voices = await edge_tts.list_voices()
    return {"voices": voices}


@app.get("/api/voices/preview")
async def preview_voice(voice: str = "en-US-AriaNeural"):
    """generate a short audio preview for a given voice."""
    import edge_tts
    from fastapi.responses import Response
    text = "Hello! I'm your voice assistant. How can I help you today?"
    try:
        communicate = edge_tts.Communicate(text, voice=voice)
        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
        if not audio_chunks:
            return JSONResponse(status_code=500, content={"error": "no audio generated"})
        return Response(content=b"".join(audio_chunks), media_type="audio/mpeg")
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ---------------------------------------------------------------------------
# pipeline processing
# ---------------------------------------------------------------------------

async def process_pipeline(session: SessionState) -> None:
    """audio -> stt -> llm -> (tool calls) -> tts -> audio."""
    pipeline_start = datetime.now(timezone.utc)

    try:
        # ---- stt ----
        session.state = PipelineState.PROCESSING_STT
        await session.websocket.send_json({"type": "state", "state": "PROCESSING_STT"})

        audio_data = bytes(session.audio_buffer)
        session.audio_buffer = bytearray()

        logger.info("[%s] Processing %d bytes of audio", session.session_id, len(audio_data))

        if len(audio_data) < 800:
            logger.warning("[%s] Audio too short (%d bytes), skipping", session.session_id, len(audio_data))
            session.state = PipelineState.IDLE
            await session.websocket.send_json({"type": "state", "state": "IDLE"})
            return

        try:
            stt_result = await stt_breaker.call(groq_stt, audio_data)
        except CircuitBreakerOpen:
            await session.websocket.send_json({
                "type": "error",
                "code": "RATE_LIMIT",
                "message": "STT service temporarily unavailable.",
            })
            await play_fallback_audio(session.websocket, "stt_error")
            session.state = PipelineState.IDLE
            await session.websocket.send_json({"type": "state", "state": "IDLE"})
            return

        transcript = stt_result.get("text", "").strip()
        stt_ms = (datetime.now(timezone.utc) - pipeline_start).total_seconds() * 1000
        session.metrics["stt_ms"] = round(stt_ms)

        if not transcript:
            session.state = PipelineState.IDLE
            await session.websocket.send_json({"type": "state", "state": "IDLE"})
            return

        # injection check on transcript
        is_safe, reason = injection_detector.check(transcript)
        if not is_safe:
            logger.warning("[%s] Injection blocked: %s", session.session_id, reason)
            security_monitor.check_anomaly("injection_attempts", session.session_id)
            audit.log_security_event("INJECTION_BLOCKED", session.session_id, reason)
            await session.websocket.send_json({
                "type": "error",
                "code": "CONTENT_POLICY",
                "message": "Input rejected by security filter.",
            })
            session.state = PipelineState.IDLE
            await session.websocket.send_json({"type": "state", "state": "IDLE"})
            return

        # pii redact for logging
        safe_transcript, pii_detections = PIIRedactor.redact(transcript)
        if pii_detections:
            audit.log_security_event(
                "PII_DETECTED", session.session_id,
                json.dumps(pii_detections),
            )

        await session.websocket.send_json({
            "type": "transcript",
            "variant": "final",
            "text": transcript,
        })
        logger.info("[%s] STT (%sms): %s", session.session_id, round(stt_ms), safe_transcript[:80])

        # ---- llm ----
        session.state = PipelineState.PROCESSING_LLM
        await session.websocket.send_json({"type": "state", "state": "PROCESSING_LLM"})

        session.conversation_history.append({"role": "user", "content": transcript})
        messages = [{"role": "system", "content": build_system_prompt()}]
        messages.extend(session.conversation_history[-10:])

        try:
            llm_result = await llm_breaker.call(groq_llm, messages, TOOL_SCHEMAS)
        except CircuitBreakerOpen:
            await session.websocket.send_json({
                "type": "error",
                "code": "RATE_LIMIT",
                "message": "LLM service temporarily unavailable.",
            })
            await play_fallback_audio(session.websocket, "llm_error")
            session.state = PipelineState.IDLE
            await session.websocket.send_json({"type": "state", "state": "IDLE"})
            return
        except Exception as tool_exc:
            # model sometimes generates malformed tool calls (xml-style) causing 400
            # retry without tools so it answers from training data
            logger.warning("[%s] Tool call failed (%s), retrying without tools", session.session_id, tool_exc)
            retry_messages = list(messages)
            retry_messages[0] = {"role": "system", "content": (
                "You are voicr, a voice assistant. Answer the user's question directly and in full detail. "
                "Do NOT use or mention any tools. Do NOT output function calls or tool syntax. "
                "Just give a complete, helpful answer from your knowledge. "
                "Be thorough - the user wants detailed information."
            )}
            try:
                llm_result = await llm_breaker.call(groq_llm, retry_messages, max_tokens=2048)
            except CircuitBreakerOpen:
                await session.websocket.send_json({
                    "type": "error",
                    "code": "RATE_LIMIT",
                    "message": "LLM service temporarily unavailable.",
                })
                await play_fallback_audio(session.websocket, "llm_error")
                session.state = PipelineState.IDLE
                await session.websocket.send_json({"type": "state", "state": "IDLE"})
                return

        llm_ms = (datetime.now(timezone.utc) - pipeline_start).total_seconds() * 1000
        session.metrics["llm_total_ms"] = round(llm_ms)

        choice = llm_result["choices"][0]["message"]
        usage = llm_result.get("usage", {})
        session.metrics["prompt_tokens"] = usage.get("prompt_tokens", 0)
        session.metrics["completion_tokens"] = usage.get("completion_tokens", 0)

        # ---- tool calls with policy enforcement ----
        if choice.get("tool_calls"):
            session.state = PipelineState.TOOL_CALLING
            await session.websocket.send_json({"type": "state", "state": "TOOL_CALLING"})

            assistant_msg = {"role": "assistant", "tool_calls": choice["tool_calls"]}
            if choice.get("content"):
                assistant_msg["content"] = choice["content"]
            session.conversation_history.append(assistant_msg)

            tool_results = await mcp_router.execute_tool_calls(
                session.session_id, choice["tool_calls"],
                role=session.role,
                conversation_history=session.conversation_history,
            )

            for tr in tool_results:
                session.conversation_history.append(tr)
                tool_call_id = tr["tool_call_id"]
                tool_name = next(
                    (
                        tc["function"]["name"]
                        for tc in choice["tool_calls"]
                        if tc["id"] == tool_call_id
                    ),
                    "unknown",
                )
                await session.websocket.send_json({
                    "type": "tool_status",
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "status": "completed",
                    "result": tr["content"],
                })

            # second llm call with tool results
            messages = [{"role": "system", "content": build_system_prompt()}]
            messages.extend(session.conversation_history[-12:])

            try:
                llm_result2 = await llm_breaker.call(groq_llm, messages)
            except CircuitBreakerOpen:
                await session.websocket.send_json({
                    "type": "error",
                    "code": "RATE_LIMIT",
                    "message": "LLM service temporarily unavailable.",
                })
                session.state = PipelineState.IDLE
                await session.websocket.send_json({"type": "state", "state": "IDLE"})
                return
            except Exception as retry_exc:
                # first attempt failed - log full payload and retry with minimal messages
                logger.error("[%s] Second LLM call failed: %s", session.session_id, retry_exc)
                logger.error("[%s] Failed payload: %s", session.session_id, json.dumps(messages, default=str, indent=2)[:3000])
                logger.error("[%s] Conversation history: %s", session.session_id, json.dumps(session.conversation_history, default=str, indent=2)[:3000])

                # retry with only the last user message + tool results (drop old history)
                user_msg = next((m for m in reversed(session.conversation_history) if m["role"] == "user"), None)
                tool_results = [m for m in session.conversation_history if m.get("role") == "tool"]
                retry_messages = [{"role": "system", "content": (
                    "You are voicr, a voice assistant. Answer the user's question directly and in full detail. "
                    "Do NOT use or mention any tools. Do NOT output function calls or tool syntax. "
                    "Just give a complete, helpful answer from your knowledge. "
                    "Be thorough - the user wants detailed information."
                )}]
                if user_msg:
                    retry_messages.append(user_msg)
                retry_messages.extend(tool_results[-4:])

                logger.error("[%s] Retry with %d messages: %s", session.session_id, len(retry_messages), json.dumps(retry_messages, default=str, indent=2)[:2000])
                try:
                    llm_result2 = await llm_breaker.call(groq_llm, retry_messages, max_tokens=2048)
                except Exception as retry_exc2:
                    logger.error("[%s] Retry also failed: %s", session.session_id, retry_exc2)
                    response_text = "I found some information but had trouble processing it. Please try again."
                    session.conversation_history.append({"role": "assistant", "content": response_text})
                    await session.websocket.send_json({"type": "llm_response", "variant": "complete", "text": response_text})
                    session.state = PipelineState.IDLE
                    await session.websocket.send_json({"type": "state", "state": "IDLE"})
                    return

            choice = llm_result2["choices"][0]["message"]

        response_text = _clean_response(choice.get("content", "I'm not sure how to respond to that."))

        # output guardrail
        response_text, guard_warnings = output_guardrail.validate(response_text)
        if guard_warnings:
            for w in guard_warnings:
                logger.warning("[%s] Guardrail: %s", session.session_id, w)

        session.current_llm_response = response_text
        session.conversation_history.append({"role": "assistant", "content": response_text})

        await session.websocket.send_json({
            "type": "llm_response",
            "variant": "complete",
            "text": response_text,
        })
        logger.info("[%s] LLM: %s", session.session_id, response_text[:80])

        # ---- tts ----
        session.state = PipelineState.PROCESSING_TTS
        await session.websocket.send_json({"type": "state", "state": "PROCESSING_TTS"})

        try:
            audio_response = await tts_breaker.call(gemini_tts, response_text, voice=session.tts_voice)
        except Exception:
            await session.websocket.send_json({"type": "tts_fallback", "text": response_text})
            session.state = PipelineState.IDLE
            await session.websocket.send_json({"type": "state", "state": "IDLE"})
            return

        tts_ms = (datetime.now(timezone.utc) - pipeline_start).total_seconds() * 1000
        session.metrics["tts_ttfb_ms"] = round(tts_ms)

        # ---- stream audio ----
        session.state = PipelineState.SPEAKING
        await session.websocket.send_json({"type": "state", "state": "SPEAKING"})

        chunk_size = 4096
        chunks_sent = 0
        for i in range(0, len(audio_response), chunk_size):
            if session.state == PipelineState.INTERRUPTED:
                break
            await session.websocket.send_bytes(audio_response[i : i + chunk_size])
            chunks_sent += 1
            await asyncio.sleep(0.02)

        if session.state != PipelineState.INTERRUPTED:
            await session.websocket.send_json({
                "type": "tts_complete",
                "total_chunks": chunks_sent,
            })

        # ---- metrics ----
        e2e_ms = (datetime.now(timezone.utc) - pipeline_start).total_seconds() * 1000
        session.metrics["e2e_ms"] = round(e2e_ms)
        await session.websocket.send_json({"type": "metrics", "pipeline": session.metrics})
        logger.info("[%s] Pipeline complete: %sms", session.session_id, round(e2e_ms))

    except Exception as exc:
        logger.error("[%s] Pipeline error: %s", session.session_id, _redact(str(exc)), exc_info=True)
        session.state = PipelineState.ERROR
        try:
            await session.websocket.send_json({
                "type": "error",
                "code": "PIPELINE_ERROR",
                "message": f"Error: {_redact(str(exc))[:200]}",
            })
        except Exception:
            pass
        try:
            await play_fallback_audio(session.websocket, "general_error")
        except Exception:
            pass

    finally:
        if session.state not in (PipelineState.INTERRUPTED, PipelineState.LISTENING):
            session.state = PipelineState.IDLE
            await session.websocket.send_json({"type": "state", "state": "IDLE"})


# ---------------------------------------------------------------------------
# websocket endpoint
# ---------------------------------------------------------------------------

@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    """main websocket endpoint for bi-directional voice interaction."""
    await websocket.accept()
    session_id = str(uuid.uuid4())
    session = SessionState(session_id, websocket)
    session.client_ip = websocket.client.host if websocket.client else "unknown"

    # --- authentication (required in live mode) ---
    token = AuthManager.extract_from_websocket(websocket)
    if AUTH_REQUIRED and not token:
        audit.log_auth_event("WS_AUTH", session.client_ip, False)
        await websocket.close(code=4001, reason="Authentication required")
        return

    if token:
        try:
            payload = AuthManager.verify_token(token)
            session.role = payload.get("role", "user")
            session.subject = payload.get("sub", session.client_ip)
            audit.log_auth_event("WS_AUTH", session.client_ip, True)
        except Exception:
            audit.log_auth_event("WS_AUTH", session.client_ip, False)
            await websocket.close(code=4001, reason="Authentication failed")
            return
    else:
        # mock mode without auth
        session.subject = session.client_ip

    # --- connection limits ---
    role_cfg = AuthManager.get_role_config(session.role)
    if not rate_limiter.add_connection(
        session.client_ip, session_id, MAX_CONNECTIONS_PER_IP
    ):
        await websocket.close(code=4008, reason="Too many connections from this IP")
        return

    sessions[session_id] = session

    logger.info("[%s] WebSocket connected from %s (role=%s)", session_id, session.client_ip, session.role)

    try:
        await websocket.send_json({
            "type": "state",
            "state": "IDLE",
            "session_id": session_id,
            "mock_mode": MOCK_MODE,
        })
    except (WebSocketDisconnect, RuntimeError):
        sessions.pop(session_id, None)
        rate_limiter.remove_connection(session.client_ip, session_id)
        return

    speech_buffer_timeout: asyncio.Task | None = None

    try:
        while True:
            try:
                data = await websocket.receive()
            except RuntimeError:
                break

            # --- rate limit by subject ---
            allowed, retry_after = rate_limiter.check_subject(
                session.subject, role_cfg.get("rate_limit_rpm", MAX_MESSAGES_PER_MINUTE)
            )
            if not allowed:
                await websocket.send_json({
                    "type": "error",
                    "code": "RATE_LIMIT",
                    "message": f"Rate limit exceeded. Retry in {retry_after}s.",
                    "retry_after_s": retry_after,
                })
                continue

            # global anomaly check
            if security_monitor.check_anomaly("messages_per_minute", session.client_ip):
                await websocket.send_json({
                    "type": "error",
                    "code": "RATE_LIMIT",
                    "message": "Too many messages. Please slow down.",
                })
                continue

            if "bytes" in data and data["bytes"]:
                audio_chunk = data["bytes"]

                # validate individual chunk size
                if len(audio_chunk) > MAX_AUDIO_SIZE_BYTES:
                    await websocket.send_json({
                        "type": "error",
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": "Audio chunk too large.",
                    })
                    continue

                # validate PCM format
                valid, reason = InputSanitizer.validate_audio(audio_chunk)
                if not valid:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INVALID_AUDIO",
                        "message": "Invalid audio data.",
                    })
                    continue

                # enforce cumulative audio buffer limit from role config
                max_buffer = role_cfg.get("max_audio_buffer_bytes", MAX_AUDIO_BUFFER_BYTES)
                if len(session.audio_buffer) + len(audio_chunk) > max_buffer:
                    await websocket.send_json({
                        "type": "error",
                        "code": "BUFFER_OVERFLOW",
                        "message": "Audio buffer limit reached. Send shorter messages.",
                    })
                    session.audio_buffer = bytearray()
                    session.state = PipelineState.IDLE
                    await websocket.send_json({"type": "state", "state": "IDLE"})
                    continue

                if session.state in (PipelineState.IDLE, PipelineState.LISTENING):
                    session.audio_buffer.extend(audio_chunk)

                    if session.state == PipelineState.IDLE:
                        session.state = PipelineState.LISTENING
                        await websocket.send_json({"type": "state", "state": "LISTENING"})
                        logger.info("[%s] Started listening, buffer: %d bytes", session.session_id, len(session.audio_buffer))

                    # reset silence timer
                    if speech_buffer_timeout and not speech_buffer_timeout.done():
                        speech_buffer_timeout.cancel()

                    async def _process_after_silence(s: SessionState = session):
                        await asyncio.sleep(0.5)
                        if s.state == PipelineState.LISTENING and len(s.audio_buffer) > 0:
                            await process_pipeline(s)

                    speech_buffer_timeout = asyncio.create_task(_process_after_silence())

                elif session.state == PipelineState.SPEAKING:
                    # barge-in interrupt
                    session.state = PipelineState.INTERRUPTED
                    if session.tts_task and not session.tts_task.done():
                        session.tts_task.cancel()
                    session.audio_buffer = bytearray(audio_chunk)
                    await websocket.send_json({"type": "state", "state": "INTERRUPTED"})
                    session.state = PipelineState.LISTENING
                    await websocket.send_json({"type": "state", "state": "LISTENING"})

            elif "text" in data and data["text"]:
                try:
                    msg = json.loads(data["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "control":
                    action = msg.get("action", "")

                    if action == "interrupt":
                        session.state = PipelineState.INTERRUPTED
                        if session.tts_task and not session.tts_task.done():
                            session.tts_task.cancel()
                        session.audio_buffer = bytearray()
                        session.state = PipelineState.IDLE
                        await websocket.send_json({"type": "state", "state": "IDLE"})

                    elif action == "stop_recording":
                        if session.state == PipelineState.LISTENING and len(session.audio_buffer) > 0:
                            await process_pipeline(session)
                        elif session.state in (PipelineState.IDLE, PipelineState.LISTENING):
                            session.state = PipelineState.IDLE
                            await websocket.send_json({"type": "state", "state": "IDLE"})

                    elif action == "ping":
                        await websocket.send_json({"type": "pong"})

                    elif action == "end_session":
                        break

                    elif action == "set_voice":
                        voice = msg.get("voice", "")
                        session.tts_voice = voice
                        logger.info("[%s] Voice set to: %s", session.session_id, voice or "default")

                elif msg_type == "text_input":
                    text = msg.get("text", "").strip()
                    if not text:
                        continue

                    text = InputSanitizer.sanitize_text(text)

                    # injection check
                    is_safe, reason = injection_detector.check(text)
                    if not is_safe:
                        security_monitor.check_anomaly(
                            "injection_attempts", session.session_id
                        )
                        audit.log_security_event(
                            "INJECTION_BLOCKED", session.session_id, reason
                        )
                        await websocket.send_json({
                            "type": "error",
                            "code": "CONTENT_POLICY",
                            "message": "Input rejected by security filter.",
                        })
                        continue

                    session.audio_buffer = bytearray()
                    session.conversation_history.append({"role": "user", "content": text})

                    await websocket.send_json({
                        "type": "transcript",
                        "variant": "final",
                        "text": text,
                    })

                    session.state = PipelineState.PROCESSING_LLM
                    await websocket.send_json({"type": "state", "state": "PROCESSING_LLM"})

                    messages = [{"role": "system", "content": build_system_prompt()}]
                    messages.extend(session.conversation_history[-10:])

                    try:
                        llm_result = await llm_breaker.call(
                            groq_llm, messages, TOOL_SCHEMAS
                        )
                    except CircuitBreakerOpen:
                        await websocket.send_json({
                            "type": "error",
                            "code": "RATE_LIMIT",
                            "message": "LLM service temporarily unavailable.",
                        })
                        await play_fallback_audio(websocket, "llm_error")
                        session.state = PipelineState.IDLE
                        await websocket.send_json({"type": "state", "state": "IDLE"})
                        continue
                    except Exception:
                        logger.warning("[%s] Tool call failed, retrying without tools", session.session_id)
                        retry_messages = list(messages)
                        retry_messages[0] = {"role": "system", "content": (
                            "You are voicr, a voice assistant. Answer the user's question directly and in full detail. "
                            "Do NOT use or mention any tools. Do NOT output function calls or tool syntax. "
                            "Just give a complete, helpful answer from your knowledge. "
                            "Be thorough - the user wants detailed information."
                        )}
                        try:
                            llm_result = await llm_breaker.call(groq_llm, retry_messages, max_tokens=2048)
                        except CircuitBreakerOpen:
                            await websocket.send_json({
                                "type": "error",
                                "code": "RATE_LIMIT",
                                "message": "LLM service temporarily unavailable.",
                            })
                            await play_fallback_audio(websocket, "llm_error")
                            session.state = PipelineState.IDLE
                            await websocket.send_json({"type": "state", "state": "IDLE"})
                            continue

                    choice = llm_result["choices"][0]["message"]

                    if choice.get("tool_calls"):
                        session.state = PipelineState.TOOL_CALLING
                        await websocket.send_json({"type": "state", "state": "TOOL_CALLING"})

                        assistant_msg = {"role": "assistant", "tool_calls": choice["tool_calls"]}
                        if choice.get("content"):
                            assistant_msg["content"] = choice["content"]
                        session.conversation_history.append(assistant_msg)

                        tool_results = await mcp_router.execute_tool_calls(
                            session.session_id, choice["tool_calls"],
                            role=session.role,
                            conversation_history=session.conversation_history,
                        )
                        for tr in tool_results:
                            session.conversation_history.append(tr)

                        messages = [{"role": "system", "content": build_system_prompt()}]
                        messages.extend(session.conversation_history[-12:])
                        try:
                            llm_result2 = await llm_breaker.call(groq_llm, messages)
                        except CircuitBreakerOpen:
                            await websocket.send_json({
                                "type": "error",
                                "code": "RATE_LIMIT",
                                "message": "LLM service temporarily unavailable.",
                            })
                            await play_fallback_audio(websocket, "llm_error")
                            session.state = PipelineState.IDLE
                            await websocket.send_json({"type": "state", "state": "IDLE"})
                            continue
                        except Exception as retry_exc:
                            logger.error("[%s] Second LLM call failed: %s", session.session_id, retry_exc)
                            logger.error("[%s] Failed payload: %s", session.session_id, json.dumps(messages, default=str, indent=2)[:3000])
                            logger.error("[%s] Conversation history: %s", session.session_id, json.dumps(session.conversation_history, default=str, indent=2)[:3000])

                            user_msg = next((m for m in reversed(session.conversation_history) if m["role"] == "user"), None)
                            tool_results_msgs = [m for m in session.conversation_history if m.get("role") == "tool"]
                            retry_messages = [{"role": "system", "content": (
                                "You are voicr, a voice assistant. Answer the user's question directly and in full detail. "
                                "Do NOT use or mention any tools. Do NOT output function calls or tool syntax. "
                                "Just give a complete, helpful answer from your knowledge. "
                                "Be thorough - the user wants detailed information."
                            )}]
                            if user_msg:
                                retry_messages.append(user_msg)
                            retry_messages.extend(tool_results_msgs[-4:])

                            logger.error("[%s] Retry with %d messages", session.session_id, len(retry_messages))
                            try:
                                llm_result2 = await llm_breaker.call(groq_llm, retry_messages, max_tokens=2048)
                            except Exception:
                                response_text = "I found some information but had trouble processing it. Please try again."
                                session.conversation_history.append({"role": "assistant", "content": response_text})
                                await websocket.send_json({"type": "llm_response", "variant": "complete", "text": response_text})
                                session.state = PipelineState.IDLE
                                await websocket.send_json({"type": "state", "state": "IDLE"})
                                continue

                        choice = llm_result2["choices"][0]["message"]

                    response_text = _clean_response(choice.get(
                        "content", "I'm not sure how to help with that."
                    ))
                    response_text, _ = output_guardrail.validate(response_text)
                    session.conversation_history.append(
                        {"role": "assistant", "content": response_text}
                    )

                    await websocket.send_json({
                        "type": "llm_response",
                        "variant": "complete",
                        "text": response_text,
                    })

                    # tts
                    session.state = PipelineState.PROCESSING_TTS
                    await websocket.send_json({"type": "state", "state": "PROCESSING_TTS"})

                    try:
                        audio = await tts_breaker.call(gemini_tts, response_text, voice=session.tts_voice)
                    except Exception:
                        await websocket.send_json({"type": "tts_fallback", "text": response_text})
                        session.state = PipelineState.IDLE
                        await websocket.send_json({"type": "state", "state": "IDLE"})
                        continue

                    session.state = PipelineState.SPEAKING
                    await websocket.send_json({"type": "state", "state": "SPEAKING"})
                    for i in range(0, len(audio), 4096):
                        await websocket.send_bytes(audio[i : i + 4096])
                        await asyncio.sleep(0.02)

                    await websocket.send_json({"type": "tts_complete"})
                    session.state = PipelineState.IDLE
                    await websocket.send_json({"type": "state", "state": "IDLE"})

    except WebSocketDisconnect:
        logger.info("[%s] WebSocket disconnected", session_id)
    except RuntimeError:
        logger.info("[%s] WebSocket connection lost", session_id)
    except Exception as exc:
        logger.error("[%s] WebSocket error: %s", session_id, _redact(str(exc)), exc_info=True)
    finally:
        sessions.pop(session_id, None)
        rate_limiter.remove_connection(session.client_ip, session_id)
        if speech_buffer_timeout and not speech_buffer_timeout.done():
            speech_buffer_timeout.cancel()
        logger.info("[%s] Session cleaned up", session_id)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    logger.info(
        "Starting voicr [%s] [AUTH=%s]",
        "MOCK MODE" if MOCK_MODE else "LIVE MODE",
        "required" if AUTH_REQUIRED else "optional",
    )
    uvicorn.run(app, host=HOST, port=PORT, log_level=LOG_LEVEL)
