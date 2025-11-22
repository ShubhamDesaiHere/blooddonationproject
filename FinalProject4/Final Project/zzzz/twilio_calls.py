import os
from typing import Optional

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import Response

try:
    from twilio.rest import Client
    from twilio.twiml.voice_response import VoiceResponse, Gather
except Exception as import_error:  # pragma: no cover
    Client = None  # type: ignore
    VoiceResponse = None  # type: ignore
    Gather = None  # type: ignore


app = FastAPI(title="Donor Call Service", version="1.0.0")


def _get_env_variable(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def make_call(recipient_number: str, status_callback_url: Optional[str] = None) -> str:
    """
    Initiate a voice call to the given recipient number using Twilio.

    The call will fetch TwiML instructions from the /voice endpoint.

    Args:
        recipient_number: Destination phone number in E.164 format (e.g., +15551234567)
        status_callback_url: Optional URL to receive call status callbacks.

    Returns:
        The Twilio Call SID of the initiated call.

    Raises:
        HTTPException: If Twilio credentials are missing or the call fails.
        ValueError: If required environment variables are missing.
    """

    # Required Twilio credentials and caller ID
    account_sid = _get_env_variable("TWILIO_ACCOUNT_SID")
    auth_token = _get_env_variable("TWILIO_AUTH_TOKEN")
    twilio_phone_number = _get_env_variable("TWILIO_PHONE_NUMBER")

    # Publicly accessible base URL where this FastAPI app is served
    # For local development, expose via a tunnel (e.g., ngrok) and set CALLBACK_BASE_URL
    base_url = _get_env_variable("CALLBACK_BASE_URL")

    if Client is None:
        raise HTTPException(status_code=500, detail="Twilio SDK is not installed.")

    instruction_url = f"{base_url.rstrip('/')}/voice"

    try:
        client = Client(account_sid, auth_token)
        call = client.calls.create(
            to=recipient_number,
            from_=twilio_phone_number,
            url=instruction_url,
            status_callback=status_callback_url,
            status_callback_method="POST" if status_callback_url else None,
        )
        return call.sid
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Failed to initiate call: {error}")


@app.post("/voice")
@app.get("/voice")
def voice_instructions() -> Response:
    """Provide TwiML instructions for the initial call flow with DTMF gather."""
    if VoiceResponse is None or Gather is None:
        raise HTTPException(status_code=500, detail="Twilio SDK is not installed.")

    response = VoiceResponse()
    gather = Gather(action="/gather", method="POST", input="dtmf", num_digits=1, timeout=5)
    gather.say(
        "Hello, this is the Blood Donation System. Blood is urgently needed. "
        "Press 1 to accept, Press 2 to decline.",
        voice="alice",
        language="en-US",
    )
    response.append(gather)
    # Fallback if no input was received
    response.say("We did not receive any input. Goodbye.", voice="alice", language="en-US")
    twiml_xml = str(response)
    return Response(content=twiml_xml, media_type="application/xml")


@app.post("/gather")
@app.get("/gather")
def handle_gather(Digits: Optional[str] = Form(default=None)) -> Response:  # noqa: N803 (Twilio sends 'Digits')
    """Handle DTMF input from donor and respond accordingly with TwiML."""
    if VoiceResponse is None:
        raise HTTPException(status_code=500, detail="Twilio SDK is not installed.")

    response = VoiceResponse()

    if Digits == "1":
        response.say(
            "Thank you for agreeing to donate. The hospital will contact you soon.",
            voice="alice",
            language="en-US",
        )
    elif Digits == "2":
        response.say(
            "Thank you for your time. We understand.",
            voice="alice",
            language="en-US",
        )
    else:
        response.say("Sorry, I didn't get that. Goodbye.", voice="alice", language="en-US")

    return Response(content=str(response), media_type="application/xml")


def example_usage() -> None:
    """Example usage demonstrating how to call a donor number using make_call."""
    donor_number = os.getenv("TEST_DONOR_NUMBER")
    if not donor_number:
        print(
            "Set TEST_DONOR_NUMBER (e.g., +15551234567) to try example usage, "
            "and ensure CALLBACK_BASE_URL points to your public FastAPI URL."
        )
        return
    try:
        call_sid = make_call(donor_number)
        print(f"Call initiated. SID: {call_sid}")
    except Exception as error:
        print(f"Failed to make example call: {error}")


if __name__ == "__main__":
    # Optional: run an example call if TEST_DONOR_NUMBER is set
    example_usage()
    # To run the API server: uvicorn twilio_calls:app --host 0.0.0.0 --port 8000


