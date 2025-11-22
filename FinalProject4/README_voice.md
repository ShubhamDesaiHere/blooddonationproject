# Blood Donation Voice API

FastAPI application that integrates Twilio voice calls with ElevenLabs AI voice for blood donation requests.

## Features

- **Twilio Voice Integration**: Handles incoming calls from donors
- **ElevenLabs TTS**: Generates natural-sounding speech from text
- **Speech Recognition**: Processes donor responses using Twilio's speech-to-text
- **Dynamic Audio Generation**: Creates unique audio files for each interaction

## Setup

1. **Install Dependencies**:
   ```bash
   pip install -r requirements_voice.txt
   ```

2. **Environment Variables**:
   Create a `.env` file with:
   ```
   TWILIO_ACCOUNT_SID=your_twilio_account_sid
   TWILIO_AUTH_TOKEN=your_twilio_auth_token
   ELEVENLABS_API_KEY=your_elevenlabs_api_key
   CALLBACK_BASE_URL=https://your-ngrok-url.ngrok-free.app
   ```

3. **Run the Application**:
   ```bash
   python voice_api.py
   ```

## API Endpoints

### `/voice` (POST)
- **Purpose**: Twilio webhook when donor answers call
- **Action**: 
  - Generates greeting: "Hello, thank you for being a blood donor. Are you available to donate today?"
  - Converts to speech using ElevenLabs
  - Plays audio and waits for speech input
- **Response**: TwiML with `<Play>` and `<Gather>`

### `/handle-response` (POST)
- **Purpose**: Process donor's speech response
- **Logic**:
  - If donor says "yes" → "Thank you for agreeing to donate. We will contact you with details."
  - If donor says "no" → "No worries, thank you for your time."
  - Other responses → "I didn't catch that. Thank you for your time."
- **Response**: TwiML with `<Play>` and `<Hangup>`

### `/static/tts/{filename}` (GET)
- **Purpose**: Serve generated audio files
- **Access**: Public URL for Twilio to play audio

## Twilio Configuration

1. **Phone Number Setup**:
   - Buy a Twilio phone number
   - Set webhook URL: `https://your-ngrok-url.ngrok-free.app/voice`
   - HTTP method: POST

2. **Voice Settings**:
   - Input: Speech
   - Language: English (US)
   - Timeout: 10 seconds

## ElevenLabs Configuration

- **Voice ID**: `21m00Tcm4TlvDq8ikWAM` (default)
- **Settings**:
  - Stability: 0.5
  - Similarity Boost: 0.75

## File Structure

```
├── voice_api.py          # Main FastAPI application
├── requirements_voice.txt # Python dependencies
├── env_example.txt       # Environment variables template
├── static/
│   └── tts/             # Generated audio files
└── README_voice.md      # This file
```

## Usage Flow

1. **Donor receives call** → Twilio calls `/voice`
2. **Greeting played** → ElevenLabs generates "Hello, thank you for being a blood donor..."
3. **Donor responds** → Speech sent to `/handle-response`
4. **Response processed** → ElevenLabs generates appropriate reply
5. **Call ends** → TwiML `<Hangup>`

## Testing

1. **Start the server**: `python voice_api.py`
2. **Test endpoints**:
   - GET `/` → Health check
   - POST `/voice` → Simulate Twilio webhook
   - POST `/handle-response` → Test response processing

## Error Handling

- **TTS Failures**: Falls back to Twilio's built-in TTS
- **Missing Files**: Returns 404 for non-existent audio
- **API Errors**: Graceful degradation with fallback responses

## Security Notes

- Store API keys in environment variables
- Use HTTPS for production (ngrok provides this)
- Validate Twilio webhook signatures in production
- Implement rate limiting for TTS generation



