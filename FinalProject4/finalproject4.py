from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
import os
import tempfile
import requests
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

# Load environment variables:
# - .env at repo root (for OPENAI_API_KEY)
# - Final Project/zzzz/twilio.env (for Twilio + CALLBACK_BASE_URL)
# - Final Project/zzzz/openai.env (for OPENAI_API_KEY)
load_dotenv()  # loads .env at project root
load_dotenv("Final Project/zzzz/twilio.env")  # loads your twilio.env
load_dotenv("Final Project/zzzz/openai.env")  # loads your openai.env

app = FastAPI()

# Pydantic model for request validation
class CallRequest(BaseModel):
    to_number: str
    message: str

# Global variable to store the message for the voice endpoint
current_message = ""

@app.get("/")
def read_root():
    return {"message": "FastAPI + Twilio is running 🚀"}

@app.post("/make-call")
def make_call(request: CallRequest):
    """
    Make a Twilio voice call with the specified message
    """
    try:
        # Get Twilio credentials from environment variables
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")
        
        # Validate environment variables
        if not all([account_sid, auth_token, twilio_phone_number]):
            raise HTTPException(
                status_code=500, 
                detail="Missing Twilio credentials. Please set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER environment variables."
            )
        
        # Initialize Twilio client
        client = Client(account_sid, auth_token)
        
        # Store the message globally for the voice endpoint
        global current_message
        current_message = request.message
        
        # Get the base URL for the voice endpoint
        base_url = os.getenv("CALLBACK_BASE_URL", "http://localhost:8000")
        voice_url = f"{base_url}/voice"
        
        # Make the call
        call = client.calls.create(
            to=request.to_number,
            from_=twilio_phone_number,
            url=voice_url,
            method='GET'
        )
        
        return {
            "success": True,
            "call_sid": call.sid,
            "to_number": request.to_number,
            "message": "Call initiated successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error making call: {str(e)}")

@app.get("/voice")
def voice_endpoint():
    """
    TwiML endpoint that Twilio calls first.
    It plays the initial message and then <Gather> speech,
    posting the results to /handle-response.
    """
    try:
        response = VoiceResponse()

        # Initial message
        intro_text = current_message or "Hello. You have a new blood donation request."

        # Speak the message
        response.say(intro_text, voice='alice')

        # Gather speech (Twilio will send SpeechResult; for recording URL it may also include RecordingUrl in some regions)
        # If RecordingUrl is unavailable, we'll fallback to SpeechResult text.
        base_url = os.getenv("CALLBACK_BASE_URL", "http://localhost:8000")
        gather = Gather(
            input="speech",
            action=f"{base_url}/handle-response",
            method="POST",
            timeout=6
        )
        gather.say("Please say if you accept or reject this request.", voice="alice")
        response.append(gather)

        # If no input, we can end politely
        response.say("We did not receive any input. Goodbye.", voice="alice")

        return Response(content=str(response), media_type="application/xml")
    except Exception:
        fail = VoiceResponse()
        fail.say("We are experiencing a system error. Please try again later.", voice="alice")
        return Response(content=str(fail), media_type="application/xml")

@app.post("/handle-response")
async def handle_response(request: Request):
    """
    Twilio posts here after <Gather> completes.
    Steps:
      1) Get RecordingUrl (if provided) or fallback to SpeechResult.
      2) If audio available, download and send to OpenAI Whisper (transcribe + detect language).
      3) Send the transcribed text to GPT to craft a donor-friendly reply
        - Greet donor with their name and blood group (mock variables for now)
        - Ask if they accept the blood donation request
        - Respond in the same language detected by Whisper (with Marathi bias)
      4) Return TwiML <Say> with GPT response back to Twilio.
    """
    try:
        form = await request.form()
        recording_url = (form.get("RecordingUrl") or "").strip()
        speech_result = (form.get("SpeechResult") or "").strip()

        # Mock donor info (SET THESE FROM YOUR DB/CONTEXT)
        donor_name = "Sham"         # <-- TODO: Replace with donor's actual name
        blood_group = "O+"          # <-- TODO: Replace with donor's actual blood group

        # OpenAI API key
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            # Return friendly TTS if key missing
            vr = VoiceResponse()
            vr.say("Configuration error. OpenAI API key is not set.", voice="alice")
            return Response(content=str(vr), media_type="application/xml")

        # Prepare variables for transcription
        transcribed_text = None
        detected_language = None

        # Try to transcribe via Whisper if we have a recording
        if recording_url:
            # Twilio recording files may need extension (.mp3) to download
            audio_url_candidates = [
                f"{recording_url}.mp3",
                f"{recording_url}.wav",
                recording_url
            ]

            audio_content = None
            last_err = None

            # Twilio media may require HTTP basic auth; include credentials if needed
            account_sid = os.getenv("TWILIO_ACCOUNT_SID")
            auth_token = os.getenv("TWILIO_AUTH_TOKEN")
            auth_tuple = (account_sid, auth_token) if account_sid and auth_token else None

            for url in audio_url_candidates:
                try:
                    r = requests.get(url, auth=auth_tuple, timeout=20)
                    if r.ok and r.content:
                        audio_content = r.content
                        break
                except Exception as ex:
                    last_err = ex

            if audio_content:
                # Save to temp file and send to Whisper
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as tmp:
                    tmp.write(audio_content)
                    tmp.flush()
                    tmp_path = tmp.name

                try:
                    # Whisper transcription (verbose_json to get language)
                    with open(tmp_path, "rb") as f:
                        resp = requests.post(
                            "https://api.openai.com/v1/audio/transcriptions",
                            headers={"Authorization": f"Bearer {openai_api_key}"},
                            files={
                                "file": (os.path.basename(tmp_path), f, "audio/mpeg")
                            },
                            data={
                                "model": "whisper-1",
                                "response_format": "verbose_json"
                            },
                            timeout=60
                        )
                    if resp.ok:
                        data = resp.json()
                        transcribed_text = (data.get("text") or "").strip()
                        detected_language = (data.get("language") or "").strip()  # e.g. "en", "hi", "mr"
                except Exception:
                    # Fall back to SpeechResult
                    pass
                finally:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass

        # Fallback to SpeechResult text if we don't have transcription
        if not transcribed_text:
            transcribed_text = speech_result or ""
            # Heuristic language fallback (very light)
            detected_language = detected_language or ("mr" if any(w in transcribed_text.lower() for w in ["ho", "nahi", "namaskar", "krupaya", "ahe", "ahet"]) else "hi" if any(w in transcribed_text.lower() for w in ["haan", "nahi", "namaste", "kripya"]) else "en")

        # Final fallback if still nothing
        if not transcribed_text:
            transcribed_text = "No input detected."
            detected_language = detected_language or "en"

        # Build prompt for GPT: respond in same language with Marathi bias
        # The assistant should greet donor with their name and blood group, and ask for accept/reject clearly.
        system_prompt = "You are a polite hospital virtual agent for blood donation calls."

        user_prompt = f"""
Donor language code (ISO-1 guess): {detected_language}
Donor name: {donor_name}
Donor blood group: {blood_group}
Donor said: "{transcribed_text}"

Instructions:
- If the detected language is Marathi (mr) OR language is uncertain/ambiguous, RESPOND IN MARATHI.
- If the detected language is clearly Hindi (hi), respond in Hindi.
- Otherwise, respond in the detected language.
- Start with a friendly greeting including the donor name and mention the blood group need.
- Ask clearly if they ACCEPT or REJECT the blood donation request.
- Keep it concise and easy to understand.

Marathi response style guide (use as template, adapt words naturally):
"नमस्कार {donor_name}. आमच्याकडे {blood_group} रक्ताची तातडीची गरज आहे.
कृपया कळवा — आपण रक्तदान स्वीकारता का? स्वीकारण्यासाठी 'हो' म्हणा, नाकारण्यासाठी 'नाही' म्हणा."

Hindi response style guide:
"नमस्ते {donor_name}. हमें {blood_group} रक्त की तत्काल आवश्यकता है।
कृपया बताएं — क्या आप रक्तदान स्वीकार करते हैं? स्वीकार के लिए 'हाँ', मना करने के लिए 'नहीं' कहें."
"""

        # Call GPT (chat.completions)
        completion = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {openai_api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.5
            },
            timeout=60
        )

        if not completion.ok:
            # If GPT fails, provide default fallback
            vr = VoiceResponse()
            fallback_msg = "Hello. We need your help for a blood donation. Please say accept or reject after the beep."
            vr.say(fallback_msg, voice="alice")
            return Response(content=str(vr), media_type="application/xml")

        reply_text = completion.json()["choices"][0]["message"]["content"].strip()

        # Return TwiML <Say> with GPT's response
        vr = VoiceResponse()
        # Do NOT set TwiML language attr (let TTS speak text even if it's Hindi/Marathi). 'alice' voice is safe default.
        vr.say(reply_text, voice="alice")
        return Response(content=str(vr), media_type="application/xml")

    except Exception as e:
        # Safe error TwiML
        vr = VoiceResponse()
        vr.say("We are experiencing a system error. Please try again later.", voice="alice")
        return Response(content=str(vr), media_type="application/xml")
