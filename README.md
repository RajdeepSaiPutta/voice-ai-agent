# voicr 
A voice-based AI assistant. You speak, it listens, thinks, and talks back. 

## What is this
Voicr is a real-time voice assistant that runs in your browser. You press a button, speak into your mic, and it: 1. Converts your speech to text (speech-to-text). 2. Sends that text to an AI model to get a response (large language model). 3. Converts the response back to speech (text-to-speech). 4. Plays the audio in your browser. It can also search the web, manage a calendar, and read/write files for you. 

## How it works
When you open the app, your browser connects to the server through a websocket. When you press the mic button, your browser captures audio from your microphone and streams it to the server as raw audio data. The server: - Checks if you are allowed to connect (authentication). - Checks if you are sending too many messages (rate limiting). - Converts your speech to text using Groq's whisper model. - Checks if you are trying to inject harmful instructions (injection detection). - Removes personal information like emails and phone numbers from logs (PII redaction). - Sends your text to Groq's Llama 3.3 70b model for a response. - If the AI wants to use a tool (like web search), it calls that tool and sends the result back to the AI. - Converts the AI's text response to audio using Edge-tts (Microsoft's free TTS service). - Streams the audio back to your browser. The whole process happens in seconds. You can also type messages instead of speaking. 

## The tools
The AI has access to these tools: - **Web search** - Searches the internet for current information using Google Gemini, falls back to DuckDuckGo. - **File read/write/list** - Saves and reads notes in a private session folder. - **Calendar** - Creates, lists, and deletes calendar events (stored in memory per session). 

## The pipeline
The system goes through these states: ``` IDLE -> LISTENING -> PROCESSING_STT -> PROCESSING_LLM -> PROCESSING_TTS -> SPEAKING | TOOL_CALLING (optional) ``` If you speak while the AI is talking, it interrupts (barge-in) and starts listening again. 

## Project structure
``` app.py Main server file. Handles websocket connections and runs the pipeline. index.html The dashboard UI. One file, no build step. Dark and light mode. requirements.txt Python packages needed to run the project. .env Your API keys and settings (not committed to git). .env.example Template for .env. voicr/ Python package with all the logic. config.py Loads settings from .env and environment variables. state.py Defines the pipeline states (IDLE, LISTENING, etc.) and session data. api_clients.py Connects to Groq (speech-to-text, LLM) and Gemini/Edge-tts (text-to-speech). auth.py Handles login with JWT tokens and role-based permissions (user, admin, readonly). rate_limiter.py Prevents too many requests from one person. pii.py Finds and hides personal information like emails and phone numbers. audit.py Writes tamper-proof logs of everything that happens. sanitizer.py Validates audio, text, and file paths before processing. security.py Detects suspicious behavior like repeated failed logins. prompts.py Builds the system prompt that tells the AI how to behave. tools_schemas.py Defines what tools the AI can call (7 tools total). tools_fs.py The file system tool. Keeps each session in its own folder. tools_calendar.py The calendar tool. Stores events in memory. mcp_router.py Decides which tool to run and checks if you are allowed to use it. guardrails.py Checks the AI's response for problems before sending it to you. injection.py Catches attempts to trick the AI with harmful instructions. circuit_breaker.py Stops calling an external service if it keeps failing. fallback.py Plays pre-recorded audio when something goes wrong. data/ Stored here at runtime. sessions/ Each conversation gets its own folder for files. audit/ Daily log files with cryptographic signatures. assets/audio/ Pre-recorded error messages (MP3 files). ``` 

## Getting started 
### Without API keys (mock mode) 
You can run the project without any API keys. It will use fake responses so you can test the UI. ```bash python -m venv venv source venv/bin/activate pip install -r requirements.txt python app.py ``` Open http://localhost:8000 in your browser. 
### With API keys
1. Copy .env.example to .env. 2. Add your API keys to .env. 3. Generate a JWT secret: `openssl rand -base64 32`. 4. Run `python app.py`. You need API keys from: - **Groq** (https://console.groq.com) - for speech-to-text and the language model. - **Google AI** (https://aistudio.google.com) - for text-to-speech and web search. - **ElevenLabs** (optional) - backup text-to-speech (free tier is limited). 
### Environment variables
| Variable | What it does | Default | |----------|-------------|---------| | GROQ_API_KEY | Your Groq API key. Empty means mock mode. | (empty) | | GOOGLE_AI_API_KEY | Your Google AI key for TTS and search. | (empty) | | ELEVENLABS_API_KEY | Backup TTS provider. | (empty) | | JWT_SECRET | Secret for signing login tokens. Required in live mode. Minimum 32 characters. | (empty) | | AUTH_REQUIRED | Set to false for local development. | true | | HOST | Server bind address. | 0.0.0.0 | | PORT | Server port. | 8000 | | LOG_LEVEL | Python log level (debug, info, warning, error). | info | | CORS_ORIGINS | Allowed browser origins, comma-separated. | http://localhost:8000 | | MAX_CONNECTIONS_PER_IP | Max simultaneous connections from one IP. | 999 | | MAX_MESSAGES_PER_MINUTE | Max messages per minute. | 9999 | | MAX_AUDIO_SIZE_BYTES | Max size of one audio chunk. | 2000000 | | MAX_AUDIO_BUFFER_BYTES | Max total audio stored per session. | 2000000 | | MAX_FILE_SIZE_BYTES | Max size for file tool writes. | 102400 | | MAX_FILES_PER_SESSION | Max files one session can create. | 20 | | MAX_TOTAL_BYTES_PER_SESSION | Max total storage per session. | 1048576 | | MAX_FILE_PATH_DEPTH | Max folder nesting depth. | 5 | | TTS_VOICE | Edge-tts voice name. Run `edge-tts --list-voices` to see all options. | en-US-AriaNeural | | GEMINI_TTS_VOICE | Gemini TTS voice name. Options: Kore, Enceladus, Puck, Charon. | Kore | 

## External services
| Service | What it is used for | API key required | |---------|-------------------|-----------------| | Groq | Speech-to-text (whisper) and language model (Llama 3.3 70b) | Yes | | Google Gemini | Text-to-speech and web search | Yes | | Edge-tts | Primary text-to-speech (free, no key needed) | No | | ElevenLabs | Backup text-to-speech (free tier limited) | Optional | | DuckDuckGo | Backup web search (free) | No | ## The TTS chain When the AI responds with text, the system tries to convert it to speech in this order: 1. Edge-tts (free, no API key needed, uses Microsoft's Edge TTS service). 2. Gemini TTS (Google's AI TTS, rate-limited). 3. ElevenLabs (paid service, free tier returns errors). 4. Browser TTS (your browser's built-in speech synthesis as last resort). 

##
Changing the voice Set the `TTS_VOICE` variable in your `.env` file to change the Edge-tts voice: ```bash # List all available voices edge-tts --list-voices # Examples of English voices TTS_VOICE=en-US-AriaNeural # Female (default) TTS_VOICE=en-US-GuyNeural # Male TTS_VOICE=en-US-JennyNeural # Female TTS_VOICE=en-GB-SoniaNeural # Female, British TTS_VOICE=en-GB-RyanNeural # Male, British ``` For Gemini TTS, set `GEMINI_TTS_VOICE`: ```bash GEMINI_TTS_VOICE=Kore # Default GEMINI_TTS_VOICE=Enceladus GEMINI_TTS_VOICE=Puck GEMINI_TTS_VOICE=Charon ``` 

##
The search chain When the AI needs to search the web: 1. Gemini web search (Google's AI with search grounding). 2. DuckDuckGo (free, reliable fallback). 

##
How the browser side works The browser captures microphone audio using the web audio API (script processor node, 16kHz mono). It streams raw audio chunks to the server over a websocket. When audio comes back, it collects chunks in a buffer and plays them in order when the server signals the response is complete. If all TTS fails, it uses the browser's built-in speech synthesis. 

##
How authentication works In live mode, you must send a JWT token in the `sec-websocket-protocol` header. The token contains your role (user, admin, or readonly). Each role has different permissions: - **User** - Can use all tools, up to 3 sessions. - **Admin** - Can use all tools, up to 10 sessions. - **Readonly** - Cannot use any tools, 1 session only. 

##
How security works - Every audio chunk is checked for size and format. - Every text message is checked for prompt injection attempts. - Personal information (emails, phone numbers, credit cards) is automatically removed from logs. - The AI's response is checked for harmful content before being spoken. - All tool calls are logged with tamper-proof signatures. - API keys are never sent to the client, even in error messages. 

##
Running for development ```bash # Set AUTH_REQUIRED=false in .env for easier testing. # Leave GROQ_API_KEY empty for mock mode. python app.py # Open http://localhost:8000 ``` 

##
Running for production ```bash # Set AUTH_REQUIRED=true in .env. # Set a strong JWT_SECRET (at least 32 characters). # Set GROQ_API_KEY and GOOGLE_AI_API_KEY. # Lower rate limits from 9999 to sensible values. python app.py ``` 

##
Architecture details See ARCHITECTURE.md for the full technical breakdown, including the state machine, websocket protocol, security setup, and module responsibilities. 

##
Troubleshooting See RUNBOOK.md for common issues and how to fix them.
