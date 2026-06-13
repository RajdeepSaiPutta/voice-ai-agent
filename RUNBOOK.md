# voicr execution guide

## Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- A modern web browser (Chrome, Firefox, Edge)
- Internet connection (for API calls, or use mock mode offline)

## Step 1: Navigate to project

```bash
cd /home/rajdeep/voice-ai-agent
```

## Step 2: Create virtual environment

```bash
python -m venv venv
source venv/bin/activate
```

On Windows use `venv\Scripts\activate` instead.

## Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

## Step 4: Generate JWT secret

```bash
openssl rand -base64 32
```

Copy the output string.

## Step 5: Create .env file

```bash
cp .env.example .env
```

Open .env in any text editor and paste your JWT secret:

```
JWT_SECRET=paste-the-string-you-just-generated
```

## Step 6: Get API keys (optional)

The app runs in mock mode without any API keys. If you want real responses:

### Groq (STT + LLM)

1. Go to https://console.groq.com
2. Create a free account
3. Go to API Keys and click Generate
4. Copy the key starting with gsk_
5. Add to .env: `GROQ_API_KEY=gsk_your_key_here`

### Google AI Studio (TTS)

1. Go to https://aistudio.google.com/apikey
2. Sign in with your Google account
3. Click Create API Key
4. Copy the key starting with AIza
5. Add to .env: `GOOGLE_AI_API_KEY=AIza_your_key_here`

### ElevenLabs (alternative TTS, optional)

1. Go to https://elevenlabs.io
2. Create a free account
3. Go to Profile Settings and copy your API Key
4. Add to .env: `ELEVENLABS_API_KEY=sk_your_key_here`

## Step 7: Create fallback audio directory

```bash
mkdir -p assets/audio
```

If you have fallback MP3 files, place them in assets/audio with these names:
- one_moment_please.mp3
- could_not_hear.mp3
- processing_error.mp3
- response_ready_text_only.mp3
- technical_difficulty.mp3

If you do not have these files the app will still work, it just cannot play fallback audio on errors.

## Step 8: Start the server

```bash
python app.py
```

You should see:

```
Starting voicr [MOCK MODE]
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Or if you set API keys:

```
Starting voicr [LIVE MODE]
```

## Step 9: Open the dashboard

Open your browser and go to:

```
http://localhost:8000
```

## Step 10: Use the app

### Voice mode

1. Click the microphone button to start
2. Allow microphone access when prompted
3. Speak a sentence
4. Wait for silence detection (0.8s after you stop talking)
5. The app processes your speech and responds with audio

### Text mode

1. Type a message in the text input field
2. Press Enter or click Send
3. The agent responds with text and audio

### Interrupt

While the agent is speaking, click the Interrupt button or start speaking to cut it off.

## Troubleshooting

### "ModuleNotFoundError" when starting

Make sure you activated the virtual environment:

```bash
source venv/bin/activate
```

Then reinstall:

```bash
pip install -r requirements.txt
```

### Microphone not working

- Make sure you are using HTTP or localhost (microphones require a secure context in some browsers)
- Check browser permissions for microphone access
- Try a different browser

### No audio response

- If in mock mode, the generated audio is a sine wave (test tone)
- If in live mode, check that GOOGLE_AI_API_KEY is set correctly
- Check the server terminal for error messages

### WebSocket connection fails

- Make sure the server is running on port 8000
- Check that no other process is using port 8000: `lsof -i :8000`
- If behind a proxy, make sure WebSocket upgrade is supported

### Port already in use

Change the port in .env:

```
PORT=8001
```

Then open http://localhost:8001 instead.

## Running in background

To keep the server running after closing the terminal:

```bash
nohup python app.py > voicr.log 2>&1 &
```

To stop it:

```bash
pkill -f "python app.py"
```

## Checking health

```bash
curl http://localhost:8000/api/health
```

Returns:

```json
{"status": "healthy", "mock_mode": true, "active_sessions": 0, "version": "1.0.0"}
```

## Checking active sessions

```bash
curl http://localhost:8000/api/sessions
```
