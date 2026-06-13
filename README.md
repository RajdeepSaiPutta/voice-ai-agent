# voicr

a voice-based ai assistant. you speak, it listens, thinks, and talks back.

## what is this

voicr is a real-time voice assistant that runs in your browser. you press a button, speak into your mic, and it:

1. converts your speech to text (speech-to-text)
2. sends that text to an ai model to get a response (large language model)
3. converts the response back to speech (text-to-speech)
4. plays the audio in your browser

it can also search the web, manage a calendar, and read/write files for you.

## how it works

when you open the app, your browser connects to the server through a websocket. when you press the mic button, your browser captures audio from your microphone and streams it to the server as raw audio data. the server:

- checks if you are allowed to connect (authentication)
- checks if you are sending too many messages (rate limiting)
- converts your speech to text using groq's whisper model
- checks if you are trying to inject malicious instructions (injection detection)
- removes personal information like emails and phone numbers from logs (pii redaction)
- sends your text to groq's llama 3.3 70b model for a response
- if the ai wants to use a tool (like web search), it calls that tool and feeds the result back to the ai
- converts the ai's text response to audio using edge-tts (microsoft's free tts service)
- streams the audio back to your browser

the whole thing happens in seconds. you can also type messages instead of speaking.

## the tools

the ai has access to these tools:

- **web search** - searches the internet for current information using google gemini, falls back to duckduckgo
- **file read/write/list** - saves and reads notes in a private session folder
- **calendar** - creates, lists, and deletes calendar events (stored in memory per session)

## the pipeline

the system goes through these states:

```
IDLE -> LISTENING -> PROCESSING_STT -> PROCESSING_LLM -> PROCESSING_TTS -> SPEAKING
                                     |
                               TOOL_CALLING (optional)
```

if you speak while the ai is talking, it interrupts (barge-in) and starts listening again.

## project structure

```
app.py                  main server file. handles websocket connections and runs the pipeline.
index.html              the dashboard ui. one file, no build step. dark and light mode.
requirements.txt        python packages needed to run the project.
.env                    your api keys and settings (not committed to git).
.env.example            template for .env.

voicr/                  python package with all the logic.
  config.py             loads settings from .env and environment variables.
  state.py              defines the pipeline states (IDLE, LISTENING, etc.) and session data.
  api_clients.py        connects to groq (speech-to-text, llm) and gemini/edge-tts (text-to-speech).
  auth.py               handles login with jwt tokens and role-based permissions (user, admin, readonly).
  rate_limiter.py       prevents too many requests from one person.
  pii.py                finds and hides personal information like emails and phone numbers.
  audit.py              writes tamper-proof logs of everything that happens.
  sanitizer.py          validates audio, text, and file paths before processing.
  security.py           detects suspicious behavior like repeated failed logins.
  prompts.py            builds the system prompt that tells the ai how to behave.
  tools_schemas.py      defines what tools the ai can call (7 tools total).
  tools_fs.py           the file system tool. keeps each session in its own folder.
  tools_calendar.py     the calendar tool. stores events in memory.
  mcp_router.py         decides which tool to run and checks if you are allowed to use it.
  guardrails.py         checks the ai's response for problems before sending it to you.
  injection.py          catches attempts to trick the ai with malicious instructions.
  circuit_breaker.py    stops calling an external service if it keeps failing.
  fallback.py           plays pre-recorded audio when something goes wrong.

data/                   stored here at runtime.
  sessions/             each conversation gets its own folder for files.
  audit/                daily log files with cryptographic signatures.

assets/audio/           pre-recorded error messages (mp3 files).
```

## getting started

### without api keys (mock mode)

you can run the project without any api keys. it will use fake responses so you can test the ui.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

open http://localhost:8000 in your browser.

### with api keys

1. copy .env.example to .env
2. add your api keys to .env
3. generate a jwt secret: `openssl rand -base64 32`
4. run `python app.py`

you need api keys from:
- **groq** (https://console.groq.com) - for speech-to-text and the language model
- **google ai** (https://aistudio.google.com) - for text-to-speech and web search
- **elevenlabs** (optional) - backup text-to-speech (free tier is limited)

### environment variables

| variable | what it does | default |
|----------|-------------|---------|
| GROQ_API_KEY | your groq api key. empty means mock mode. | (empty) |
| GOOGLE_AI_API_KEY | your google ai key for tts and search. | (empty) |
| ELEVENLABS_API_KEY | backup tts provider. | (empty) |
| JWT_SECRET | secret for signing login tokens. required in live mode. min 32 chars. | (empty) |
| AUTH_REQUIRED | set to false for local development. | true |
| HOST | server bind address. | 0.0.0.0 |
| PORT | server port. | 8000 |
| LOG_LEVEL | python log level (debug, info, warning, error). | info |
| CORS_ORIGINS | allowed browser origins, comma-separated. | http://localhost:8000 |
| MAX_CONNECTIONS_PER_IP | max simultaneous connections from one ip. | 999 |
| MAX_MESSAGES_PER_MINUTE | max messages per minute. | 9999 |
| MAX_AUDIO_SIZE_BYTES | max size of one audio chunk. | 2000000 |
| MAX_AUDIO_BUFFER_BYTES | max total audio stored per session. | 2000000 |
| MAX_FILE_SIZE_BYTES | max size for file tool writes. | 102400 |
| MAX_FILES_PER_SESSION | max files one session can create. | 20 |
| MAX_TOTAL_BYTES_PER_SESSION | max total storage per session. | 1048576 |
| MAX_FILE_PATH_DEPTH | max folder nesting depth. | 5 |
| TTS_VOICE | edge-tts voice name. run `edge-tts --list-voices` to see all options. | en-US-AriaNeural |
| GEMINI_TTS_VOICE | gemini tts voice name. options: Kore, Enceladus, Puck, Charon. | Kore |

## external services

| service | what it is used for | api key required |
|---------|-------------------|-----------------|
| groq | speech-to-text (whisper) and language model (llama 3.3 70b) | yes |
| google gemini | text-to-speech and web search | yes |
| edge-tts | primary text-to-speech (free, no key needed) | no |
| elevenlabs | backup text-to-speech (free tier limited) | optional |
| duckduckgo | backup web search (free) | no |

## the tts chain

when the ai responds with text, the system tries to convert it to speech in this order:

1. edge-tts (free, no api key needed, uses microsoft's edge tts service)
2. gemini tts (google's ai tts, rate-limited)
3. elevenlabs (paid service, free tier returns errors)
4. browser tts (your browser's built-in speech synthesis as last resort)

## changing the voice

set the `TTS_VOICE` variable in your `.env` file to change the edge-tts voice:

```bash
# list all available voices
edge-tts --list-voices

# examples of english voices
TTS_VOICE=en-US-AriaNeural    # female (default)
TTS_VOICE=en-US-GuyNeural     # male
TTS_VOICE=en-US-JennyNeural   # female
TTS_VOICE=en-GB-SoniaNeural   # female, british
TTS_VOICE=en-GB-RyanNeural    # male, british
```

for gemini tts, set `GEMINI_TTS_VOICE`:

```bash
GEMINI_TTS_VOICE=Kore         # default
GEMINI_TTS_VOICE=Enceladus
GEMINI_TTS_VOICE=Puck
GEMINI_TTS_VOICE=Charon
```

## the search chain

when the ai needs to search the web:

1. gemini web search (google's ai with search grounding)
2. duckduckgo (free, reliable fallback)

## how the browser side works

the browser captures microphone audio using the web audio api (scriptprocessor node, 16khz mono). it streams raw audio chunks to the server over a websocket. when audio comes back, it accumulates chunks in a buffer and plays them in sequence when the server signals the response is complete. if all tts fails, it uses the browser's built-in speech synthesis.

## how authentication works

in live mode, you must send a jwt token in the `sec-websocket-protocol` header. the token contains your role (user, admin, or readonly). each role has different permissions:

- **user** - can use all tools, up to 3 sessions
- **admin** - can use all tools, up to 10 sessions
- **readonly** - cannot use any tools, 1 session only

## how security works

- every audio chunk is checked for size and format
- every text message is checked for prompt injection attempts
- personal information (emails, phone numbers, credit cards) is automatically removed from logs
- the ai's response is checked for harmful content before being spoken
- all tool calls are logged with tamper-proof signatures
- api keys are never sent to the client, even in error messages

## running for development

```bash
# set AUTH_REQUIRED=false in .env for easier testing
# leave GROQ_API_KEY empty for mock mode
python app.py
# open http://localhost:8000
```

## running for production

```bash
# set AUTH_REQUIRED=true in .env
# set a strong JWT_SECRET (at least 32 characters)
# set GROQ_API_KEY and GOOGLE_AI_API_KEY
# lower rate limits from 9999 to sensible values
python app.py
```

## architecture details

see ARCHITECTURE.md for the full technical breakdown including the state machine, websocket protocol, security architecture, and module responsibilities.

## troubleshooting

see RUNBOOK.md for common issues and how to fix them.
