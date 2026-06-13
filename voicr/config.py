"""application configuration loaded from environment variables."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# tts voice settings
TTS_VOICE = os.getenv("TTS_VOICE", "en-US-AriaNeural")
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Kore")

# api keys (empty = mock mode)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GOOGLE_AI_KEY = os.getenv("GOOGLE_AI_API_KEY", "")
ELEVENLABS_KEY = os.getenv("ELEVENLABS_API_KEY", "")

# mock mode when no groq key present
MOCK_MODE = not GROQ_API_KEY

# authentication
AUTH_REQUIRED = os.getenv("AUTH_REQUIRED", "true").lower() == "true"
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 1

if not MOCK_MODE and AUTH_REQUIRED and not JWT_SECRET:
    print("ERROR: JWT_SECRET is required in live mode. Set it in .env or environment.", file=sys.stderr)
    sys.exit(1)

if JWT_SECRET and len(JWT_SECRET) < 32:
    print("ERROR: JWT_SECRET must be at least 32 characters for security.", file=sys.stderr)
    sys.exit(1)

# api endpoints
GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_LLM_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_TTS_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.5-flash-preview-tts:generateContent"
)
GEMINI_SEARCH_URL = (
    "https://generativelanguage.googleapis.com/v1beta/"
    "models/gemini-2.0-flash:generateContent"
)

# server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
CORS_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:8000").split(",")
    if o.strip()
]

# paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
AUDIT_DIR = DATA_DIR / "audit"
ASSETS_DIR = BASE_DIR / "assets" / "audio"

# ensure directories exist
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

# rate limiting
MAX_CONNECTIONS_PER_IP = int(os.getenv("MAX_CONNECTIONS_PER_IP", "999"))
MAX_CONNECTIONS_PER_USER = int(os.getenv("MAX_CONNECTIONS_PER_USER", "999"))
MAX_MESSAGES_PER_MINUTE = int(os.getenv("MAX_MESSAGES_PER_MINUTE", "9999"))
MAX_AUDIO_SIZE_BYTES = int(os.getenv("MAX_AUDIO_SIZE_BYTES", "2000000"))
MAX_AUDIO_BUFFER_BYTES = int(os.getenv("MAX_AUDIO_BUFFER_BYTES", "2000000"))

# file tool limits
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_BYTES", "102400"))
MAX_FILES_PER_SESSION = int(os.getenv("MAX_FILES_PER_SESSION", "20"))
MAX_TOTAL_BYTES_PER_SESSION = int(os.getenv("MAX_TOTAL_BYTES_PER_SESSION", "1048576"))
MAX_FILE_PATH_DEPTH = int(os.getenv("MAX_FILE_PATH_DEPTH", "5"))

# audit log security
AUDIT_LOG_KEY = os.getenv("AUDIT_LOG_KEY", JWT_SECRET)
