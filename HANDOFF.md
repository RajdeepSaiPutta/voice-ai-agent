# voicr - Complete Project Handoff

## What This Project Is

voicr is a real-time voice AI assistant. You speak into your mic, it transcribes your speech (STT), sends it to an LLM for a response, converts the response back to speech (TTS), and plays it back. It also has tool-calling capabilities: web search, calendar management, and file system operations.

**Location:** `/home/rajdeep/voice-ai-agent/`
**Run:** `python app.py` (serves on port 8000)
**Dashboard:** Open `http://localhost:8000` in browser
**Environment:** Python 3.14, miniconda env `llm125`

---

## Architecture Overview

```
Browser (index.html)
  |-- WebSocket connection to /ws/voice
  |-- Captures mic audio via ScriptProcessorNode (16kHz mono PCM)
  |-- Sends raw PCM chunks over WS
  |-- Receives JSON messages (state, transcript, llm_response, errors)
  |-- Receives binary audio (MP3/WAV from TTS) or tts_fallback for browser TTS

Server (app.py + voicr/)
  |-- FastAPI + WebSocket endpoint
  |-- Pipeline: LISTENING -> PROCESSING_STT -> PROCESSING_LLM -> [TOOL_CALLING] -> PROCESSING_TTS -> SPEAKING
  |-- Security layer: auth, rate limiting, injection detection, PII redaction, audit logs
```

### External APIs
- **Groq** (api.groq.com): STT (whisper-large-v3-turbo) + LLM (llama-3.3-70b-versatile)
- **Google Gemini** (generativelanguage.googleapis.com): TTS (gemini-2.5-flash-preview-tts) + Web Search (gemini-2.0-flash with googleSearch grounding)
- **edge-tts** (local, free): Primary TTS using Microsoft Edge's free TTS service, no API key needed
- **ElevenLabs** (api.elevenlabs.io): Last-resort TTS fallback (currently broken - free tier returns 402)
- **DuckDuckGo** (via `ddgs` package): Fallback web search when Gemini search fails
- **Browser Web Speech API**: Last-resort TTS fallback when all server TTS fails

### TTS Chain (in order)
1. edge-tts (free, no API key, `en-US-AriaNeural` voice)
2. Gemini TTS (gemini-2.5-flash-preview-tts, rate-limited)
3. ElevenLabs (free tier broken, returns 402)
4. Browser Web Speech API (via `tts_fallback` message)

### Search Chain (in order)
1. Gemini Web Search (gemini-2.0-flash with googleSearch grounding)
2. DuckDuckGo (via `ddgs` package, free, reliable)

### Key Design Decisions
- Groq for STT+LLM (fast, cheap), Gemini for TTS+search, edge-tts as primary TTS
- `AUTH_REQUIRED=false` for local dev (set `.env`)
- Rate limits set to 9999 to effectively disable them for testing
- Circuit breakers: 5 failures threshold, 30s recovery per external service
- Browser Web Speech API as automatic fallback when server TTS fails
- Tool failures return error results to LLM instead of crashing pipeline
- API keys redacted from ALL exception strings sent to clients and logged

---

## File Structure

```
voice-ai-agent/
  app.py                    # Main FastAPI server, WebSocket handler, pipeline orchestration
  index.html                # Single-page dashboard UI (dark/light mode, mic toggle, browser TTS)
  requirements.txt          # 7 pinned dependencies (includes ddgs, edge-tts)
  .env                      # API keys and config (AUTH_REQUIRED=false)
  .env.example              # Template for env vars
  HANDOFF.md                # This file

  voicr/
    __init__.py
    config.py               # All config from env vars, API URLs, security validation
    state.py                # PipelineState enum (9 states), SessionState class
    api_clients.py          # Groq STT/LLM, Gemini TTS, edge-tts, ElevenLabs, Gemini/DDG web search, pcm16_to_wav()
    auth.py                 # JWT auth, RBAC (user/admin/readonly), tool policy, role configs
    rate_limiter.py         # Token bucket rate limiter, subject-based limiting, connection tracking
    security.py             # Anomaly detection (failed auth, injection attempts, messages/min)
    audit.py                # HMAC-signed chained audit logs, PII redaction, sensitive field redaction
    pii.py                  # PII detection/redaction (email, phone, SSN, credit card, IP)
    sanitizer.py            # Input validation (audio size/format, text length, filepath sanitization)
    injection.py            # Prompt injection detection (pattern matching + special char ratio)
    guardrails.py           # Output validation (forbidden patterns, blacklisted phrases, word limit)
    circuit_breaker.py      # Circuit breaker pattern (closed/open/half_open states)
    fallback.py             # Pre-recorded fallback audio streaming for error states
    prompts.py              # System prompt builder with datetime injection
    tools_schemas.py        # 7 MCP-compliant tool schemas for Groq LLM
    tools_fs.py             # File system tool (sandboxed to data/sessions/{session_id}/)
    tools_calendar.py       # Calendar tool (in-memory event store per session)
    mcp_router.py           # Tool call dispatcher with policy enforcement, PII redaction on search queries

  assets/audio/             # Pre-recorded fallback MP3s (rate_limit, stt_error, etc.)
  data/
    sessions/               # Per-session file storage (created by fs tools)
    audit/                  # Daily JSONL audit logs with HMAC integrity
```

---

## All Bugs Found and Fixed During Development

### 1. Audio Pipeline: Raw PCM vs WAV
**Bug:** Browser sends raw PCM int16 data, but Groq STT expects WAV file format.
**Fix:** Added `pcm16_to_wav()` in `api_clients.py` that wraps raw PCM with proper RIFF/WAVE headers before sending to Groq.

### 2. Silence Detection Too Aggressive
**Bug:** Silence threshold of 0.01 and 3s timeout cut off speech too early.
**Fix:** Lowered threshold to 0.008, increased silence wait to 4s, minimum streaming time to 6s.

### 3. Audio Validation Too Strict
**Bug:** `validate_audio()` checked only first 1000 bytes for non-zero samples with threshold 0, rejecting valid quiet speech.
**Fix:** Removed content/silence check entirely. Now only validates size and even byte count.

### 4. Rate Limiting Blocking Audio Chunks
**Bug:** Role configs had `rate_limit_rpm: 20` for user role. Audio chunks arrive ~20/sec, hitting the limit instantly.
**Fix:** Set all role configs to `rate_limit_rpm: 9999`. Also set anomaly detector `messages_per_minute: 9999`.

### 5. Buffer Overflow with Short Buffers
**Bug:** Audio buffer limit was 480KB, too small for extended speech.
**Fix:** Increased to 2MB. Also increased `MAX_AUDIO_SIZE_BYTES` to 2MB.

### 6. ScriptProcessorNode Not Firing
**Bug:** `createScriptProcessor` connected but not to `audioCtx.destination`, causing unreliable callback firing.
**Fix:** Connected processor to `audioCtx.destination` to keep it active.

### 7. Blob Wrapping for Audio Playback
**Bug:** `new Blob(data)` instead of `new Blob([data])` caused invalid audio.
**Fix:** Changed to `new Blob([audioQ.shift()],{type:'audio/mpeg'})`.

### 8. TTS Model Name Wrong
**Bug:** Using `gemini-2.0-flash` for TTS which doesn't support audio output.
**Fix:** Changed to `gemini-2.5-flash-preview-tts` with `response_modalities: ["AUDIO"]` and `speech_config`.

### 9. ElevenLabs Free Tier
**Bug:** ElevenLabs key on free tier returns 402.
**Status:** Not fixable without paid key. Falls back to browser TTS.

### 10. Gemini TTS Rate Limited
**Bug:** Gemini TTS returns 429 consistently.
**Status:** Likely API quota issue. Falls back to edge-tts (primary) and browser TTS.

### 11. Browser TTS Not Speaking
**Bug:** `speakText()` gated by `muted` flag at function entry, but volume was also set to 0 when muted.
**Fix:** Removed volume hack. Function returns early if muted. Added `voiceschanged` listener for voice preloading.

### 12. WebSocket Disconnect Crash
**Bug:** `RuntimeError: Cannot call "receive" once a disconnect message has been received` when client disconnects.
**Fix:** Wrapped `websocket.receive()` in try/except RuntimeError, wrapped initial `send_json` in try/except, added RuntimeError to outer except clause.

### 13. Second LLM Call Missing Circuit Breaker
**Bug:** Tool-call follow-up LLM call had no circuit breaker or error handling, causing unhandled PIPELINE_ERROR.
**Fix:** Added `llm_breaker.call()` wrapper matching the first call.

### 14. PIPELINE_ERROR Hiding Real Error
**Bug:** Generic "An internal error occurred" message hid the actual exception.
**Fix:** Now sends `f"Error: {str(exc)[:200]}"` in the error message. Also wrapped fallback audio in try/except.

### 15. No Manual Stop Recording
**Bug:** User couldn't end recording manually; had to wait for silence detection.
**Fix:** Mic button now toggles. Click again sends `stop_recording` action to server, which processes buffered audio immediately.

### 16. Variable Name Bug in TTS Fallback Path
**Bug:** `websocket` used instead of `session.websocket` in TTS fallback code path.
**Fix:** Changed to `session.websocket`.

### 17. Comments Not Lowercase
**Bug:** Some code comments were capitalized.
**Fix:** All comments converted to lowercase.

### 18. Em Dashes in Codebase
**Bug:** Unicode em dashes in text.
**Fix:** Replaced with regular dashes or removed.

### 19. Project Name Consistency
**Bug:** Project was named "vortex" then "voicy" before settling on "voicr".
**Fix:** Renamed everywhere to "voicr" (all lowercase).

### 20. Groq 400 Bad Request on Tool Calls (TWO root causes)
**Bug:** When the LLM returns `tool_calls`, the pipeline appends an assistant message with `content: ""` (empty string) alongside `tool_calls`, executes the tools, appends tool result messages, then makes a second LLM call. Groq rejects this with 400.
**Root cause 1:** Assistant messages with `tool_calls` included `content: ""` instead of omitting content entirely. Groq requires either `content: null` or no `content` field at all when `tool_calls` is present.
**Root cause 2:** Tool result messages were missing the required `name` field. Groq requires `"name": tool_name` on every `role: "tool"` message.
**Fix in `app.py` line 361:** Changed assistant message construction to only include `content` if it's non-empty:
```python
assistant_msg = {"role": "assistant", "tool_calls": choice["tool_calls"]}
if choice.get("content"):
    assistant_msg["content"] = choice["content"]
```
**Fix in `mcp_router.py`:** Added `"name": tool_name` to ALL tool result messages (lines 69, 83, 99, 133, 164, 184, 196).

### 21. API Key Leakage in Error Messages
**Bug:** Exception messages from httpx contained full URLs with API keys (e.g., `https://generativelanguage.googleapis.com/...?key=SECRET`). These were sent to clients via WebSocket `{"type":"error","message":f"Error: {str(exc)[:200]}"}` and also logged.
**Fix:** Added `_redact()` function in `app.py` (line 70) and `_redact_exception()` in `api_clients.py` (line 36). Both use regex to strip `?key=...` and `?token=...` from any string. Applied to:
- All exception strings sent to clients via WebSocket error messages
- All server-side log messages containing exceptions
- All `resp.text` logged from failed API calls

### 22. DuckDuckGo Search Fallback
**Bug:** Gemini web search returns 429 (rate limited) consistently. No fallback search available.
**Fix:** Added `duckduckgo_search()` function in `api_clients.py` using the `ddgs` package. Unified `web_search()` function tries Gemini first, falls back to DuckDuckGo on any failure. Note: the `duckduckgo_search` package was renamed to `ddgs`.

### 23. Tool Execution Crashes Pipeline
**Bug:** When `gemini_web_search` or any generic tool throws an exception, the entire pipeline crashes with an unhandled error.
**Fix:** Wrapped tool execution in `mcp_router.py` with try/except blocks. Tool failures now return error results to the LLM (e.g., `{"error": "Web search is temporarily unavailable. Answer based on your training data.", "success": False}`) instead of crashing the pipeline.

### 24. Local TTS (edge-tts)
**Bug:** TTS relied entirely on external APIs (Gemini, ElevenLabs) which were rate-limited or broken.
**Fix:** Added `edge-tts` package (Microsoft Edge's free TTS service, no API key needed). `gemini_tts()` in `api_clients.py` now tries edge-tts first, then Gemini, then ElevenLabs. Voice: `en-US-AriaNeural`.

### 25. Audio Playback - Each Chunk Played as Separate File
**Bug:** Browser was playing each 4096-byte WebSocket binary chunk as a separate `<audio>` element, resulting in only the first word playing from each chunk.
**Fix:** Added `audioBuffer` array. Each binary chunk is accumulated in `audioBuffer`. On `tts_complete`, all chunks are merged into a single Blob via `flushAudio()` and queued for sequential playback.

### 26. Groq 400 on First LLM Call (Malformed Tool Calls)
**Bug:** `llama-3.3-70b-versatile` on Groq sometimes generates malformed XML-style function calls like `<function=web_search {"query": "..."}>` instead of proper JSON `tool_calls`. This is a model-side bug that triggers on certain phrasings ("What is...", "Tell me about...", "How old is...", etc.).
**Fix:** In `app.py`, when the first `groq_llm()` call returns a 400 error, the pipeline retries WITHOUT tools and with an enhanced system prompt: "Answer directly and in full detail. Do NOT use tools." Increased `max_tokens` to 1000 on retries.

### 27. Short/Repeated Responses (Retry Prompt Bug)
**Bug:** All retry paths (first call failure, second call failure, tool call failure) used `build_system_prompt()` which says "Keep responses SHORT (1-3 sentences, under 80 words)". The model obeyed this instruction even on detailed questions, producing short or repeated answers.
**Fix:** Changed all retry system prompts to: "Answer directly and in full detail. Do NOT use or mention any tools. Do NOT output function calls or tool syntax. Just give a complete, helpful answer from your knowledge. Be thorough - the user wants detailed information." Increased `max_tokens` to 1000 on retries.

### 28. `_clean_response` Insufficient
**Bug:** The LLM sometimes outputs text like `{web_search}`, `</function>`, `<function=web_search {"query":"..."}>`, or other XML-like artifacts in its response text.
**Fix:** Added regex patterns to `_clean_response()` in `app.py` to strip: `{web_search.query="..."}`, `{function_name {args}}`, `{bare_tool_name}`, `<function=name ...>`, `</function>`, ```function...```, and all remaining XML/HTML-like tags.

---

## Known Bugs (Not Yet Fixed)

### B1. Client-Side TTS Playback Broken
**Symptom:** Server successfully generates audio via edge-tts (verified: 25KB+ returned), sends chunks over WebSocket, but browser plays nothing. No audio heard.
**Root cause:** The `audioBuffer`/`flushAudio`/`playNext` JavaScript code in `index.html` (lines 325-341) has issues:
1. `playNext()` has a redundant check: `audioQ.shift instanceof Function?audioQ.shift():audioQ.shift()` - the `instanceof Function` check is always true for arrays and does nothing useful.
2. `stopCurrentAudio()` (line 341) pauses current audio but does NOT clear `audioQ` or reset `playing` flag, so queued audio continues playing after "stop".
3. Potential race condition: if `flushAudio()` is called while `playNext()` is already playing, the new blob gets queued but may not trigger playback if `playing` is already true.
**Location:** `index.html` lines 325-341

### B2. Response Truncation
**Symptom:** Long answers get cut off mid-sentence. The retry path uses `max_tokens=1000` which may not be enough for detailed answers about complex topics.
**Root cause:** The `groq_llm()` default `max_tokens` is 200 (line 223 of `api_clients.py`). While retries use 1000, the model's system prompt says "Keep responses SHORT" so the initial call rarely exceeds 200 tokens. But when the retry prompt says "answer in full detail", 1000 tokens may still be insufficient.
**Location:** `api_clients.py` line 223 (`max_tokens: int = 200`), `app.py` retry paths (`max_tokens=1000`)

### B3. Gemini TTS Consistent 429
**Symptom:** `_gemini_tts()` in `api_clients.py` returns 429 on every attempt.
**Root cause:** User's Google AI API key has exceeded quota for gemini-2.5-flash-preview-tts.
**Impact:** Low - edge-tts is now primary. Only relevant if edge-tts also fails.
**Location:** `api_clients.py` lines 252-284

### B4. ElevenLabs Free Tier Broken
**Symptom:** ElevenLabs returns 402 Payment Required.
**Root cause:** Free tier doesn't include API access.
**Impact:** Low - only relevant as last-resort TTS fallback.
**Location:** `api_clients.py` lines 287-300

### B5. STT Mishearing
**Symptom:** Groq STT transcribes English speech as gibberish or foreign language text.
**Possible causes:**
- Background noise being captured
- The `language: "en"` parameter may not be sufficient
- Audio quality issues from ScriptProcessorNode
- Short audio chunks sent before enough speech collected
**Location:** `api_clients.py` line 216 (`language: "en"`)

---

## Security Architecture

### Authentication (`auth.py`)
- JWT tokens via `sec-websocket-protocol` header (NOT query params)
- Three roles: `user`, `admin`, `readonly`
- Role-based tool access via `allowed_tools` list
- Mutating tools: `fs_write_file`, `calendar_create_event`, `calendar_delete_event`
- `AUTH_REQUIRED=false` in `.env` for local dev

### Rate Limiting (`rate_limiter.py`)
- Token bucket per-IP (configurable via `MAX_CONNECTIONS_PER_IP`)
- Subject-based RPM limiting (currently set to 9999 for testing)
- Connection tracking with cleanup on disconnect
- Bypass when limits >= 999

### Input Validation (`sanitizer.py`)
- Audio: size limits (640 bytes min, 960KB max), even byte count check
- Text: 1000 char max, null byte stripping, control char removal
- Filepaths: traversal prevention, restricted character set

### Injection Detection (`injection.py`)
- Pattern matching for common injection phrases
- Special character ratio check (>40% = blocked)

### PII Protection (`pii.py`, `audit.py`)
- Email, phone, SSN, credit card, IP detection
- Redacted before logging and before web search queries
- Audit logs: HMAC-SHA256 chained entries, sensitive field redaction

### API Key Redaction
- `_redact()` in `app.py` strips `?key=...` and `?token=...` from any string
- `_redact_exception()` in `api_clients.py` does the same for exception messages
- Applied to ALL error messages sent to clients and ALL server-side logs
- Pattern: `re.compile(r"([?&](?:key|token|api_key|apikey)=[^&]*)", re.IGNORECASE)`

### Security Headers (`app.py` middleware)
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY
- CSP: default-src 'self', script-src 'self' 'unsafe-inline'
- HSTS: max-age=31536000

### Anomaly Detection (`security.py`)
- Failed auth per IP (threshold: 5)
- Injection attempts per session (threshold: 3)
- Messages per minute (threshold: 9999 for testing)

---

## Critical Context for Next Developer

### The Groq 400 Was on the FIRST Call, Not the Second
The key discovery was that `llama-3.3-70b-versatile` on Groq generates malformed XML-style function calls (`<function=web_search {"query": "..."}>`) for certain user phrasings. This is NOT a code bug - it's a model-side issue. The retry-without-tools pattern works around it.

### The Short Response Bug Was in the Retry Prompts
All retry paths used `build_system_prompt()` which instructs "Keep responses SHORT (1-3 sentences)". The model obeyed this even on detailed questions. The fix was to use a different, stronger prompt on retries.

### API Key Leakage Was via httpx Exception Messages
URLs like `https://generativelanguage.googleapis.com/...?key=SECRET` were embedded in `str(exc)` from httpx HTTPStatusError objects and sent to clients via WebSocket error messages. The fix was regex-based redaction applied to ALL exception strings.

### edge-tts Package Renaming
The `duckduckgo_search` Python package was renamed to `ddgs`. The `ddgs` import works. `edge-tts` itself works fine with `import edge_tts`.

### groq_llm() max_tokens Parameter
The `groq_llm()` function accepts `max_tokens` parameter (default 200). Retries use 1000. This parameter is passed directly to the Groq API payload.

### Model: llama-3.3-70b-versatile on Groq
- Known to generate malformed tool calls for certain prompt phrasings
- Requires `name` field on tool result messages
- Requires assistant messages with `tool_calls` to NOT have `content: ""`
- Supports multi-turn tool calling when format is correct

### Two LLM Call Paths
There are TWO identical code paths in `app.py`:
1. **`process_pipeline()`** (line 232) - triggered by voice input (audio -> STT -> LLM -> tools -> TTS)
2. **Text input handler** (line 703) - triggered by typed text (LLM -> tools -> TTS, no STT)

Both have the same retry logic and the same bugs.

---

## What Needs To Be Done Next

### Priority 1: Fix Client-Side TTS Playback (Critical)
The server generates audio fine. The browser doesn't play it.

**What to fix in `index.html` lines 325-341:**
1. Remove the `audioQ.shift instanceof Function?audioQ.shift():audioQ.shift()` redundancy - just use `audioQ.shift()`
2. Fix `stopCurrentAudio()` to clear `audioQ`, reset `playing=false`, and clear `audioChunks`
3. Verify that `flushAudio()` correctly creates a single Blob from accumulated chunks and triggers `playNext()`
4. Test: after `tts_complete`, `audioQ` should have one Blob, `playNext()` should create an Audio element and play it

### Priority 2: Fix Response Truncation
**What to fix:**
- Increase `max_tokens` in `groq_llm()` default from 200 to something higher (e.g., 1024)
- Or increase the retry `max_tokens` from 1000 to 2048
- Or both

### Priority 3: Rewrite HANDOFF.md (This File)
The current file is mostly up to date but could be better organized. The section above "Known Bugs" should replace the old "BROKEN" sections.

### Priority 4: Production Hardening
- Set `AUTH_REQUIRED=true` and configure proper `JWT_SECRET`
- Lower rate limits from 9999 to sensible values
- Add proper logging to file (not just stdout)
- Add reconnection logic on the client with exponential backoff
- Migrate from deprecated ScriptProcessorNode to AudioWorkletNode

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | "" | Groq API key. Empty = mock mode |
| `GOOGLE_AI_API_KEY` | "" | Google AI API key for Gemini TTS and search |
| `ELEVENLABS_API_KEY` | "" | ElevenLabs API key for fallback TTS |
| `JWT_SECRET` | "" | JWT signing secret (min 32 chars, required in live mode) |
| `AUTH_REQUIRED` | "true" | Set to "false" for local dev |
| `HOST` | "0.0.0.0" | Server bind host |
| `PORT` | 8000 | Server port |
| `LOG_LEVEL` | "info" | Python logging level |
| `CORS_ORIGINS` | "http://localhost:8000" | Comma-separated allowed origins |
| `MAX_CONNECTIONS_PER_IP` | 999 | Max WebSocket connections per IP |
| `MAX_MESSAGES_PER_MINUTE` | 9999 | Rate limit per subject |
| `MAX_AUDIO_SIZE_BYTES` | 2000000 | Max single audio chunk |
| `MAX_AUDIO_BUFFER_BYTES` | 2000000 | Max cumulative audio buffer per session |
| `MAX_FILE_SIZE_BYTES` | 102400 | Max file size for fs tool |
| `MAX_FILES_PER_SESSION` | 20 | Max files per session |
| `MAX_TOTAL_BYTES_PER_SESSION` | 1048576 | Max total storage per session |
| `MAX_FILE_PATH_DEPTH` | 5 | Max directory nesting depth |

---

## Running the Project

```bash
cd /home/rajdeep/voice-ai-agent
pip install -r requirements.txt
python app.py
# Open http://localhost:8000
```

**Mock mode:** If `GROQ_API_KEY` is empty, the app runs in mock mode with simulated STT/LLM/TTS responses. Good for UI development without API keys.

---

## Key Code Locations for Common Tasks

| Task | File | Lines |
|------|------|-------|
| Add new tool | `voicr/tools_schemas.py` + `voicr/mcp_router.py` | entire files |
| Change LLM model | `voicr/api_clients.py` | 223 (`groq_llm` function) |
| Change STT model | `voicr/api_clients.py` | 207 (`groq_stt` function) |
| Modify system prompt | `voicr/prompts.py` | entire file |
| Adjust rate limits | `voicr/auth.py` | ROLES dict (line 14) |
| Adjust silence detection | `index.html` | 367-374 (proc.onaudioprocess) |
| Add new pipeline state | `voicr/state.py` | PipelineState enum (line 14) |
| Modify security rules | `voicr/injection.py`, `voicr/guardrails.py` | pattern lists |
| Add fallback audio | `voicr/fallback.py` + `assets/audio/` | FALLBACK_AUDIO_FILES dict |
| Fix TTS playback | `index.html` | 325-341 (audioBuffer/flushAudio/playNext) |
| Fix response length | `voicr/api_clients.py` line 223, `app.py` retry paths | max_tokens values |
| Fix API key leakage | `app.py` line 70, `voicr/api_clients.py` line 36 | _redact functions |
| Fix tool call format | `app.py` lines 361-363, `voicr/mcp_router.py` lines 69/83/99/etc. | assistant msg format, tool name field |
