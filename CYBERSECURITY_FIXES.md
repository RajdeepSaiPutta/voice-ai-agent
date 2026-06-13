# Cybersecurity Fix Report

Scope: static review of `/home/rajdeep/voice-ai-agent` application code and docs. This report does not include the contents of `.env`; `.gitignore` already excludes `.env`.

## Executive Summary

The largest risks are unauthenticated access to the live voice WebSocket, unauthenticated REST metadata endpoints, automatic LLM tool execution, unbounded resource usage, and sensitive data leakage through logs and third-party API calls. The project contains useful security modules, but several are optional, unused, or not connected to authorization decisions.

## Findings And Fixes

### 1. Critical: WebSocket Authentication Is Optional

Evidence:
- `app.py:353-373` accepts `/ws/voice`, creates a session, and only verifies a JWT if the client supplies one.
- Anonymous clients can still send audio or text, trigger STT, LLM, TTS, web search, file tools, and calendar tools.

Impact:
- Anyone who can reach the service can consume paid API quota, generate files in session storage, use web search, and interact with the assistant.
- This is especially risky because the default server host is `0.0.0.0` in `voicr/config.py:32`.

How to fix:
- Require authentication before accepting work on `/ws/voice`.
- Reject missing tokens with a close code before creating persistent session state.
- Bind token subject to the session and use it in rate limiting and authorization.
- Add an explicit development-only switch such as `AUTH_REQUIRED=false`, defaulting to required in all non-local deployments.

### 2. Critical: REST Endpoints Are Unauthenticated

Evidence:
- `app.py:100-107` exposes `/api/health`.
- `app.py:110-122` exposes `/api/sessions` with session IDs, states, creation times, and message counts.

Impact:
- Attackers can enumerate active sessions, monitor usage, and time attacks against live conversations.
- Health data reveals whether the app is in mock or live mode.

How to fix:
- Require authentication for `/api/sessions`.
- Restrict `/api/health` to a minimal public response, for example only `{ "status": "ok" }`.
- Move detailed operational health to an authenticated admin endpoint.

### 3. Critical: LLM Tool Calls Execute Without User Authorization

Evidence:
- `app.py:241-242` and `app.py:543-544` execute model-selected tool calls directly.
- `mcp_router.py:21-28` exposes file and calendar write/delete tools.
- The prompt says to confirm destructive actions, but the code does not enforce confirmation.

Impact:
- Prompt injection, model error, or malicious user text can cause file writes, calendar changes, or web searches without explicit user approval.
- Role settings in `auth.py:16-35` include `tools_enabled`, but these settings are not enforced before tool execution.

How to fix:
- Add a server-side policy gate before every tool call.
- Require explicit user confirmation for mutating tools such as `fs_write_file`, `calendar_create_event`, and `calendar_delete_event`.
- Enforce role capabilities from `AuthManager.get_role_config()`, especially `tools_enabled`.
- Use an allowlist per role and per session, not just the model's selected function name.

### 4. High: JWT Secret Falls Back To A Random Runtime Value

Evidence:
- `voicr/auth.py:12` uses `os.getenv("JWT_SECRET", secrets.token_urlsafe(32))`.

Impact:
- If `JWT_SECRET` is missing, all issued tokens become invalid on restart.
- In multi-process or multi-instance deployments, each process may use a different secret.
- This can hide deployment misconfiguration instead of failing closed.

How to fix:
- Require `JWT_SECRET` in live mode and fail startup if missing or too short.
- Enforce at least 256 bits of entropy.
- Keep generated secrets in the deployment secret manager or local `.env`, not in code.

### 5. High: Tokens Can Be Passed In URLs

Evidence:
- `voicr/auth.py:63-67` extracts JWTs from the `token` query parameter.

Impact:
- Query parameters are often captured by browser history, proxy logs, reverse proxy access logs, analytics tools, and screenshots.

How to fix:
- Prefer `Authorization: Bearer ...` for REST and a secure WebSocket subprotocol or first authenticated message for WebSockets.
- Do not accept tokens in query strings in production.
- Redact token-like parameters from all access logs if query tokens remain for local development.

### 6. High: Audio Buffer Can Grow Without A Total Session Limit

Evidence:
- `app.py:415-422` checks each audio chunk against `MAX_AUDIO_SIZE_BYTES`.
- `app.py:424-441` appends chunks to `session.audio_buffer` until silence processing runs.
- There is no check on the cumulative buffer size before STT.

Impact:
- A client can send many chunks below the per-chunk limit and grow memory usage.
- The app may send oversized audio to STT, causing cost spikes or failures.

How to fix:
- Enforce a cumulative maximum on `len(session.audio_buffer) + len(audio_chunk)`.
- Close or reset the session after repeated violations.
- Use the role's `max_audio_duration_s` value from `auth.py:16-35`.
- Call `InputSanitizer.validate_audio()` before accepting chunks and before STT.

### 7. High: File Tool Has No Content, File Size, Or File Count Limits

Evidence:
- `voicr/tools_fs.py:44-59` reads and writes arbitrary-size text inside session storage.
- `voicr/tools_schemas.py` does not limit `content`, `filepath`, or directory depth.

Impact:
- A user or injected tool call can fill disk, create huge files, or force large file reads into memory and model context.
- This can become denial of service and increase LLM token usage.

How to fix:
- Enforce maximum file size, maximum write size, maximum files per session, and maximum total bytes per session.
- Reject binary-looking content if the tool is intended for notes only.
- Limit path depth and filename length.
- Return truncated reads with clear metadata instead of full file contents.

### 8. High: Web Search Tool Can Exfiltrate Sensitive Conversation Data

Evidence:
- `mcp_router.py:39-49` sends model-provided `query` to Gemini web search.
- `api_clients.py:263-272` embeds the raw query in an external request.

Impact:
- The model can include private user content, transcripts, notes, or tool output in search queries.
- This sends user data to a third-party service and possibly to search grounding infrastructure.

How to fix:
- Treat web search as an external disclosure boundary.
- Require user confirmation before sending queries that contain conversation content, PII, filenames, or notes.
- Run PII redaction and length limits on search queries.
- Log only redacted search metadata, not full query text.

### 9. Medium: Gemini API Key Is Sent In The URL

Evidence:
- `api_clients.py:247-249` and `api_clients.py:282-284` call Gemini endpoints with `?key=...`.

Impact:
- API keys in URLs can appear in proxy logs, access logs, exception traces, and monitoring tools more easily than header secrets.

How to fix:
- Use an API-key header if the provider endpoint supports it.
- If the provider requires query keys, configure HTTP client and server logs to redact query strings.
- Avoid logging full request URLs on errors.

### 10. Medium: Audit Logs Store Sensitive Tool Arguments

Evidence:
- `voicr/audit.py:37-54` stores full tool arguments in audit entries.
- Tool arguments can include note contents, calendar titles, descriptions, file paths, and search queries.

Impact:
- Audit logs can become a secondary store of private user data.
- Anyone with filesystem access to `data/audit` can read sensitive content.

How to fix:
- Redact or hash sensitive fields before audit logging.
- Store only metadata by default: tool name, success, latency, size, and redacted argument summary.
- Apply filesystem permissions so only the service user can read audit logs.
- Define retention and deletion policies.

### 11. Medium: Audit Log Integrity Hash Is Not Tamper-Proof

Evidence:
- `voicr/audit.py:31-35` hashes each entry with plain SHA-256 and stores a truncated hash.
- The hash is not chained to previous entries and does not use a secret key.

Impact:
- Anyone who can edit the log file can alter an entry and recompute its hash.
- The current hash detects accidental corruption better than malicious tampering.

How to fix:
- Use an HMAC with a secret log-signing key.
- Chain entries by including the previous entry hash.
- Ship logs to append-only external storage where possible.

### 12. Medium: Role-Based Limits Are Defined But Not Enforced

Evidence:
- `voicr/auth.py:16-35` defines `max_sessions`, `max_audio_duration_s`, `tools_enabled`, and `rate_limit_rpm`.
- `app.py` does not use these values when accepting sessions, processing audio, rate limiting, or executing tools.

Impact:
- A `readonly` token still gets the default session behavior and can reach tool execution paths.
- Admin/user limits are documentation rather than enforcement.

How to fix:
- Load role config after token verification and store it in session state.
- Apply `max_sessions` per subject, not only per IP.
- Apply `rate_limit_rpm` to the token subject and/or IP.
- Block tool execution when `tools_enabled` is false.

### 13. Medium: CORS Configuration Can Become Unsafe With Credentials

Evidence:
- `app.py:75-81` enables `allow_credentials=True` and `allow_headers=["*"]`.
- `voicr/config.py:35` loads origins directly from `CORS_ORIGINS`.

Impact:
- If `CORS_ORIGINS` is set broadly, browser clients from untrusted origins may be allowed to interact with credentialed endpoints.
- This becomes more serious once cookie or header authentication is added.

How to fix:
- Keep a strict production allowlist of exact HTTPS origins.
- Fail startup if `allow_credentials=True` is combined with wildcard origins.
- Use environment-specific CORS settings.

### 14. Medium: Rate Limiting Is Per IP And In-Memory Only

Evidence:
- `app.py:392-404` rate-limits using `session.client_ip`.
- `voicr/rate_limiter.py` stores buckets in process memory.

Impact:
- NATed users share limits, while attackers can bypass limits through distributed IPs.
- Limits reset on restart and do not work across multiple workers or instances.
- `MAX_CONNECTIONS_PER_IP` exists in `config.py:50` but is not enforced.

How to fix:
- Rate-limit by authenticated subject plus IP.
- Enforce connection limits on connect.
- Use Redis or another shared store for production deployments.
- Add separate limits for messages, audio bytes, STT requests, LLM requests, TTS requests, and tool calls.

### 15. Medium: Tool Argument Parsing Errors Can Crash A Pipeline Turn

Evidence:
- `mcp_router.py:35-37` assumes every tool call has valid JSON arguments.
- There is no local exception handling around malformed JSON in the router.

Impact:
- A malformed model tool call can raise an exception and fail the user turn.
- Repeated malformed calls can create noisy errors and denial-of-service symptoms.

How to fix:
- Catch `JSONDecodeError`, missing keys, and invalid argument types per tool call.
- Return a structured tool error instead of raising.
- Validate arguments against the JSON schemas server-side before calling a tool.

### 16. Medium: Filesystem Path Containment Should Use Path APIs

Evidence:
- `voicr/tools_fs.py:22-24` checks containment with `str(resolved).startswith(str(session_dir.resolve()))`.

Impact:
- String prefix checks are fragile and can be wrong for similarly named sibling paths.
- The current sanitizer reduces the practical risk, but path containment should not depend on string matching.

How to fix:
- Use `resolved.relative_to(session_dir.resolve())` or `Path.is_relative_to()` and reject on `ValueError`.
- Validate paths before sanitizing so malicious input is rejected instead of silently transformed.
- Reject symlinks if the storage directory can be modified outside this tool.

### 17. Low: Input Sanitizer Is Not Applied Consistently

Evidence:
- `InputSanitizer.validate_audio()` exists in `voicr/sanitizer.py:14-34`.
- The WebSocket audio path only checks chunk size in `app.py:415-422`.

Impact:
- Odd-length PCM, silence padding, and undersized chunks are not rejected at the WebSocket boundary.
- This can waste processing and make downstream behavior less predictable.

How to fix:
- Apply audio validation on chunks and cumulative buffers.
- Return generic validation errors to clients and detailed reasons only to redacted internal logs.

### 18. Low: Public Dashboard Has No Security Headers

Evidence:
- `app.py:93-97` serves `index.html` directly.
- No middleware sets headers such as Content Security Policy, `X-Content-Type-Options`, or `Referrer-Policy`.

Impact:
- Browser-side hardening is missing.
- The app loads Google Fonts, so CSP should explicitly define allowed external resources.

How to fix:
- Add security header middleware.
- Use a CSP that allows the app origin, WebSocket endpoint, required Google Fonts domains, and no inline scripts if the UI is later refactored.
- Add `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, and appropriate frame protections.

### 19. Low: Dependency Versions Are Not Pinned

Evidence:
- `requirements.txt` uses lower bounds such as `fastapi>=0.115.0` and `httpx>=0.27.0`.

Impact:
- Builds are not reproducible.
- A future dependency release can introduce breaking behavior or a new vulnerability without a code change.

How to fix:
- Pin exact versions in a lock file.
- Regularly update with a dependency scanner such as `pip-audit`, Dependabot, or Renovate.
- Separate direct dependency constraints from the fully resolved production lock.

## Recommended Fix Order

1. Require WebSocket authentication and protect `/api/sessions`.
2. Enforce role-based authorization before tool execution.
3. Add cumulative resource limits for audio, sessions, files, and tool calls.
4. Add confirmation gates for mutating tools and external web search.
5. Redact logs and audit entries.
6. Harden deployment defaults: required `JWT_SECRET`, strict CORS, security headers, and pinned dependencies.

## Notes

- `.env` is already listed in `.gitignore`; keep it that way.
- This review did not modify application code.
- Dynamic testing, dependency vulnerability scanning, and deployment/proxy review would likely find additional environment-specific issues.
