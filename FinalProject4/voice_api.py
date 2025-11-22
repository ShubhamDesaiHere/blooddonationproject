from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
import requests
import os
import uuid
import time
from datetime import datetime
from pathlib import Path
import json
from dotenv import load_dotenv

app = FastAPI(title="Blood Donation Voice API")

# Load environment variables from common locations
try:
    load_dotenv()
    # Also load the Flask project's env if present
    load_dotenv("Final Project/zzzz/twilio.env", override=False)
except Exception:
    pass

# Environment variables
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
CALLBACK_BASE_URL = os.getenv("CALLBACK_BASE_URL", "http://localhost:8000")
# Retell AI (read at request time to reflect updated env)
def get_retell_env():
    return {
        'api_key': os.getenv("RETELL_API_KEY", ""),
        'agent_id': os.getenv("RETELL_AGENT_ID", ""),
        'sip_domain': os.getenv("RETELL_SIP_DOMAIN", ""),
        'sip_user': os.getenv("RETELL_SIP_USER", ""),
        'sip_pass': os.getenv("RETELL_SIP_PASS", ""),
    }

# ElevenLabs configuration
ELEVENLABS_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # Default voice ID
ELEVENLABS_API_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"

# Create TTS directory if it doesn't exist
TTS_DIR = Path("static/tts")
TTS_DIR.mkdir(parents=True, exist_ok=True)

def generate_tts_audio(text: str, filename: str = None) -> str:
    """
    Generate TTS audio using ElevenLabs API and save to static/tts/
    Returns the public URL of the generated audio file
    """
    if not filename:
        timestamp = int(time.time())
        filename = f"tts_{timestamp}_{uuid.uuid4().hex[:8]}.mp3"
    
    file_path = TTS_DIR / filename
    
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }
    
    payload = {
        "text": text,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }
    
    try:
        response = requests.post(ELEVENLABS_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        
        # Save the audio file
        with open(file_path, "wb") as f:
            f.write(response.content)
        
        # Return public URL
        return f"{CALLBACK_BASE_URL}/static/tts/{filename}"
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {str(e)}")

@app.get("/")
async def root():
    return {"message": "Blood Donation Voice API", "status": "running"}

@app.post("/retell-voice")
async def retell_voice_webhook(request: Request):
    """
    Twilio webhook that connects the call to Retell AI's realtime voice agent using Twilio Media Streams.
    This uses <Connect><Stream> to a Retell-managed WebSocket URL.
    Required env vars: RETELL_API_KEY, RETELL_AGENT_ID
    """
    env = get_retell_env()
    if not env['api_key'] or not env['agent_id']:
        tw = VoiceResponse()
        tw.say("Retell voice agent is not configured. Please set RETELL API key and Agent ID.")
        tw.hangup()
        return Response(content=str(tw), media_type="application/xml")

    # Retell Twilio stream URL pattern (check latest docs if needed)
    # Query-string auth is used because Twilio <Stream> cannot set custom headers
    retell_ws = (
        f"wss://api.retellai.com/v1/twilio/stream?agent_id={env['agent_id']}"
        f"&api_key={env['api_key']}"
    )

    tw = VoiceResponse()
    conn = Connect()
    conn.append(Stream(url=retell_ws))
    tw.append(conn)
    return Response(content=str(tw), media_type="application/xml")

@app.post("/retell-sip")
async def retell_sip_webhook(request: Request):
    """
    Alternative webhook that connects the call to a SIP termination (e.g., Retell/ElevenLabs PSTN).
    Set RETELL_SIP_DOMAIN to the termination host, e.g. "eleven-labs-connection.pstn.twilio.com".
    If RETELL_SIP_USER and RETELL_SIP_PASS are provided, they will be used in the SIP URI as userinfo.
    """
    env = get_retell_env()
    if not env['sip_domain']:
        tw = VoiceResponse()
        tw.say("S I P termination is not configured. Please set the S I P domain.")
        tw.hangup()
        return Response(content=str(tw), media_type="application/xml")

    if env['sip_user'] and env['sip_pass']:
        sip_uri = f"sip:{env['sip_user']}:{env['sip_pass']}@{env['sip_domain']}"
    elif env['sip_user']:
        sip_uri = f"sip:{env['sip_user']}@{env['sip_domain']}"
    else:
        sip_uri = f"sip:{env['sip_domain']}"

    tw = VoiceResponse()
    # Use Dial+Sip for PSTN/SIP interconnect
    dial = tw.dial()
    dial.sip(sip_uri)
    return Response(content=str(tw), media_type="application/xml")

@app.post("/voice")
async def handle_voice_call(request: Request):
    """
    Twilio webhook when a donor answers a call
    Generates greeting message and plays it to the donor
    """
    try:
        # Generate greeting message
        greeting_text = "Hello, thank you for being a blood donor. Are you available to donate today?"
        
        # Generate TTS audio
        audio_url = generate_tts_audio(greeting_text)
        
        # Create TwiML response
        twiml = VoiceResponse()
        twiml.play(audio_url)
        twiml.gather(
            input="speech",
            action="/handle-response",
            speech_timeout="auto",
            timeout=10
        )
        
        # If no input, say goodbye
        twiml.say("Thank you for your time. Goodbye.")
        twiml.hangup()
        
        return Response(content=str(twiml), media_type="application/xml")
        
    except Exception as e:
        # Fallback response if TTS fails
        twiml = VoiceResponse()
        twiml.say("Hello, thank you for being a blood donor. Are you available to donate today?")
        twiml.gather(
            input="speech",
            action="/handle-response",
            speech_timeout="auto",
            timeout=10
        )
        twiml.say("Thank you for your time. Goodbye.")
        twiml.hangup()
        
        return Response(content=str(twiml), media_type="application/xml")

@app.post("/handle-response")
async def handle_donor_response(request: Request):
    """
    Process the donor's speech response
    """
    try:
        # Parse form data from Twilio
        form_data = await request.form()
        speech_result = form_data.get("SpeechResult", "").lower().strip()
        
        # Determine response based on speech input
        if "yes" in speech_result or "yeah" in speech_result or "sure" in speech_result:
            response_text = "Thank you for agreeing to donate. We will contact you with details."
        elif "no" in speech_result or "not" in speech_result or "can't" in speech_result:
            response_text = "No worries, thank you for your time."
        else:
            response_text = "I didn't catch that. Thank you for your time."
        
        # Generate TTS audio for response
        audio_url = generate_tts_audio(response_text)
        
        # Create TwiML response
        twiml = VoiceResponse()
        twiml.play(audio_url)
        twiml.hangup()
        
        return Response(content=str(twiml), media_type="application/xml")
        
    except Exception as e:
        # Fallback response
        twiml = VoiceResponse()
        twiml.say("Thank you for your time. Goodbye.")
        twiml.hangup()
        
        return Response(content=str(twiml), media_type="application/xml")

@app.get("/static/tts/{filename}")
async def serve_audio(filename: str):
    """
    Serve audio files from static/tts/ directory
    """
    file_path = TTS_DIR / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    
    return Response(
        content=file_path.read_bytes(),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f"inline; filename={filename}"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

