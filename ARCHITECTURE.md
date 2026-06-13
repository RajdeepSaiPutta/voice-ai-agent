# voicr code architecture

## Overview

voicr is a voice-based AI assistant built with FastAPI and WebSockets. It captures microphone audio from the browser, streams it to the server, processes it through an STT-LLM-TTS pipeline, and streams synthesized audio back. The system supports tool calling via MCP (Model Context Protocol) for file system operations, calendar management, and web search.

## Directory Structure

```
voice-ai-agent/
  app.py                    # main fastapi server, websocket endpoint, security middleware
  index.html                # single-page dashboard UI (dark/light theme)
  requirements.txt          # pinned python dependencies
  .env.example              # environment variable template
  .gitignore
  voicr/                   # python package
    __init__.py
    config.py               # environment config with security validation
    state.py                # pipeline state machine and session container
    auth.py                 # jwt authentication with role-based access control
    rate_limiter.py         # subject-based token bucket rate limiter + connection tracking
    pii.py                  # pii detection and redaction
    audit.py                # hmac-signed chained audit logging with redaction
    sanitizer.py            # input validation (audio, text, file paths)
    security.py             # real-time anomaly detection
    api_clients.py          # groq and gemini api clients (with mocks)
    prompts.py              # system prompt builder
    tools_schemas.py        # mcp tool schemas for llm tool calling
    tools_fs.py             # file system mcp server with resource limits
    tools_calendar.py       # calendar mcp server
    mcp_router.py           # policy-enforced tool routing with PII redaction
    guardrails.py           # output validation before tts
    injection.py            # prompt injection detection
    circuit_breaker.py      # fault tolerance for external apis
    fallback.py             # fallback audio streaming
  data/
    sessions/               # per-session file storage (sandboxed)
    audit/                  # daily jsonl audit logs (HMAC-signed)
  assets/
    audio/                  # fallback audio files
```

## Data Flow

```
browser mic
  |
  v
[PCM 16kHz mono] --websocket--> server
  |
  v
authentication check (required in live mode)
  |
  v
connection + rate limit check (per subject + IP)
  |
  v
audio validation (size, format, cumulative buffer limit)
  |
  v
silence detection (800ms timeout)
  |
  v
groq whisper stt --> transcript
  |
  v
injection check --> pii redaction
  |
  v
groq llama 3.3 70b llm
  |
  +--> text response
  |      |
  |      v
  |    output guardrail
  |      |
  |      v
  |    gemini tts --> audio chunks --> websocket --> browser speaker
  |
  +--> tool call
         |
         v
       policy gate (role check, tool allowlist)
         |
         v
       mcp router (PII redaction on search queries)
         |
         +--> filesystem tool (sandboxed per session, size/count limits)
         +--> calendar tool (in-memory per session)
         +--> web search (PII-sanitized, query length limited)
         |
         v
       tool result injected back into llm
```

## State Machine

The pipeline is driven by a per-session finite state machine:

```
IDLE --> LISTENING --> PROCESSING_STT --> PROCESSING_LLM
                                                  |
                                          +-------+-------+
                                          |               |
                                    TOOL_CALLING    PROCESSING_TTS
                                          |               |
                                          v               v
                                    PROCESSING_LLM    SPEAKING
                                                          |
                                                    INTERRUPTED (barge-in)
                                                          |
                                                          v
                                                        LISTENING
```

Each state transition is broadcast to the client as a JSON message so the dashboard can update in real time.

## Module Responsibilities

### voicr/config.py
Loads all configuration from environment variables using python-dotenv. Validates security-critical settings at startup: fails if JWT_SECRET is missing in live mode, enforces minimum 32-char secret length. Defines API URLs, paths, rate limits, file tool limits, and audit log signing key. Sets MOCK_MODE=True when GROQ_API_KEY is absent.

### voicr/state.py
Defines the PipelineState enum (9 states) and SessionState class which holds per-session mutable state: websocket reference, audio buffer, conversation history, metrics, client IP, authenticated subject, and role. Maintains a global sessions dict.

### voicr/auth.py
JWT authentication using python-jose. Creates tokens with role-based access control (user/admin/readonly). Each role defines: max sessions, max audio duration, max audio buffer size, tools_enabled flag, allowed tools list, and rate limit RPM. Extracts tokens only from sec-websocket-protocol header (query params removed for security). Provides tool policy checks via is_tool_allowed() and is_mutating_tool().

### voicr/rate_limiter.py
Dual-layer rate limiting: per-IP token bucket (20/min default) and per-subject token bucket (configurable per role). Tracks active connections per IP with configurable max (default 5). Thread-safe connection add/remove. Supports separate limits for messages, audio bytes, and tool calls.

### voicr/pii.py
Regex-based PII detection for emails, US phone numbers, SSNs, credit cards, IP addresses. Redacts matched patterns with type-labeled placeholders.

### voicr/audit.py
Append-only JSONL audit log with daily rotation. Each entry is signed with HMAC-SHA256 using the audit log key (defaults to JWT_SECRET). Entries are chained by including the previous entry's hash, making tampering detectable. Automatically redacts sensitive fields (content, query, text, transcript, arguments) before storage. Provides separate redaction for web search queries (email, phone, card, SSN patterns).

### voicr/sanitizer.py
Input validation for audio payloads (size, format, silence detection), text (control chars, null bytes, length limits), and file paths (traversal prevention, character whitelist).

### voicr/security.py
Real-time anomaly detection with per-identifier counters. Tracks injection attempts, message rates, and tool call rates. Fires alerts via audit log when thresholds are exceeded.

### voicr/api_clients.py
HTTP clients for Groq STT, Groq LLM, Gemini TTS, and Gemini web search. Each has a mock implementation that returns realistic fake data when MOCK_MODE is true. Real clients use httpx with timeouts. Audio is converted from raw PCM to WAV with proper headers before sending to Groq STT.

### voicr/prompts.py
Builds the system prompt with current UTC date/time injection. Instructs the LLM to keep responses short, use tools for verification, and avoid fabricating data.

### voicr/tools_schemas.py
OpenAI-format tool schemas for 7 tools: fs_read_file, fs_write_file, fs_list_files, calendar_list_events, calendar_create_event, calendar_delete_event, web_search.

### voicr/tools_fs.py
File system MCP server. Resolves paths within per-session sandbox directories. Prevents path traversal using Path.is_relative_to() (not string prefix matching). Enforces per-session limits: max file size (100KB), max files per session (20), max total bytes (1MB), max path depth (5 levels). Rejects symlinks and binary content. Validates content size before writes.

### voicr/tools_calendar.py
Calendar MCP server. In-memory event storage per session. Supports list (by date range), create (with conflict detection), and delete (by partial ID match).

### voicr/mcp_router.py
Dispatches LLM tool calls with policy enforcement. Checks role-based tool allowlists before execution. Catches JSON parse errors in tool arguments gracefully. Redacts PII from web search queries before external API calls. Sanitizes search queries to remove conversation content. Logs all tool calls to the audit trail with redacted metadata.

### voicr/guardrails.py
Validates LLM output before TTS. Strips price references, payment mentions, absolute guarantees, PII, blacklisted phrases. Truncates responses over 100 words.

### voicr/injection.py
Detects prompt injection patterns in user input: instruction override attempts, special token injection, Llama format injection, role-play commands. Blocks input with high special-character ratios.

### voicr/circuit_breaker.py
Three-state circuit breaker (closed/open/half-open) for external API calls. Opens after 3 consecutive failures. Recovers after 60s cooldown. Wraps calls with asyncio timeout.

### voicr/fallback.py
Streams pre-recorded fallback audio files to the client when an API is unavailable. Falls back gracefully if fallback files are missing.

## Security Architecture

### Authentication
- JWT tokens required in live mode (AUTH_REQUIRED=true, default)
- Tokens extracted from sec-websocket-protocol header only (no query params)
- JWT_SECRET required at startup in live mode, minimum 32 characters
- Tokens expire after 1 hour (configurable)

### Authorization (Role-Based Access Control)
Three roles with distinct capabilities:

| Role | Max Sessions | Audio Buffer | Tools | Rate Limit |
|------|-------------|-------------|-------|------------|
| user | 3 | 480KB | enabled (all 7 tools) | 20 rpm |
| admin | 10 | 1.9MB | enabled (all 7 tools) | 60 rpm |
| readonly | 1 | 160KB | disabled | 10 rpm |

Mutating tools (fs_write_file, calendar_create_event, calendar_delete_event) require explicit confirmation in the policy gate.

### Rate Limiting
- Per-IP: token bucket (20 tokens, 0.33 refill/sec)
- Per-subject: token bucket (configurable per role)
- Connection limits: max 5 per IP, max 3 per user
- Separate anomaly detection for injection attempts and message floods

### Data Protection
- PII auto-redaction before logging and LLM input
- Web search queries sanitized: PII redacted, conversation content stripped, length limited to 200 chars
- Audit logs: HMAC-SHA256 signed, chained entries, sensitive fields redacted
- API keys redacted from all log output
- Security headers: CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, HSTS

### File System Security
- Path traversal prevention using Path.is_relative_to()
- Symlink rejection
- Per-session sandboxing
- Resource limits: 100KB per file, 20 files per session, 1MB total, 5 directory levels
- Binary content rejection
- Content size validation before writes

### Input Validation
- Audio: chunk size limit, cumulative buffer limit, PCM format validation
- Text: control char removal, null byte stripping, length limits
- File paths: character whitelist, traversal prevention

## WebSocket Protocol

### Client to Server
- Binary frames: raw PCM audio (16kHz, 16-bit mono)
- Text frames: JSON control messages

```json
{"type": "control", "action": "start_session|end_session|interrupt|ping"}
{"type": "text_input", "text": "user message", "session_id": "uuid"}
```

### Server to Client
- Text frames: JSON state updates, transcripts, responses
- Binary frames: TTS audio chunks (MP3/Opus)

```json
{"type": "state", "state": "IDLE|LISTENING|PROCESSING_STT|..."}
{"type": "transcript", "variant": "final", "text": "..."}
{"type": "llm_response", "variant": "complete", "text": "..."}
{"type": "tool_status", "tool_name": "...", "status": "completed"}
{"type": "metrics", "pipeline": {"stt_ms": 400, "llm_total_ms": 500, ...}}
{"type": "error", "code": "RATE_LIMIT|CONTENT_POLICY|BUFFER_OVERFLOW|...", "message": "..."}
{"type": "tts_complete", "total_chunks": 12}
```

### Error Codes
- `RATE_LIMIT`: rate limit exceeded or service unavailable
- `CONTENT_POLICY`: prompt injection or unsafe input detected
- `PAYLOAD_TOO_LARGE`: audio chunk exceeds size limit
- `BUFFER_OVERFLOW`: cumulative audio buffer limit reached
- `INVALID_AUDIO`: audio data failed format validation
- `PIPELINE_ERROR`: internal server error

## Running

```bash
cp .env.example .env
# edit .env with your api keys (or leave empty for mock mode)
# set JWT_SECRET for live mode (required, min 32 chars)
pip install -r requirements.txt
python app.py
# open http://localhost:8000
```

Without API keys the system runs in mock mode with simulated STT, LLM, and TTS responses. In mock mode, authentication is optional (AUTH_REQUIRED=false).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| GROQ_API_KEY | (empty) | Groq API key (empty = mock mode) |
| GOOGLE_AI_API_KEY | (empty) | Google AI API key for Gemini TTS/search |
| JWT_SECRET | (empty) | JWT signing secret (required in live mode, min 32 chars) |
| AUTH_REQUIRED | true | Require authentication for WebSocket connections |
| HOST | 0.0.0.0 | Server bind address |
| PORT | 8000 | Server port |
| CORS_ORIGINS | http://localhost:8000 | Comma-separated allowed origins |
| MAX_CONNECTIONS_PER_IP | 5 | Max concurrent WebSocket connections per IP |
| MAX_MESSAGES_PER_MINUTE | 60 | Max messages per minute per IP |
| MAX_AUDIO_SIZE_BYTES | 960000 | Max single audio chunk size |
| MAX_AUDIO_BUFFER_BYTES | 480000 | Max cumulative audio buffer per session |
| MAX_FILE_SIZE_BYTES | 102400 | Max file size for file tool (100KB) |
| MAX_FILES_PER_SESSION | 20 | Max files per session |
| MAX_TOTAL_BYTES_PER_SESSION | 1048576 | Max total storage per session (1MB) |
| AUDIT_LOG_KEY | (falls back to JWT_SECRET) | HMAC key for audit log signing |
