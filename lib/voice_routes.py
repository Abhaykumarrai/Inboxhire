import os
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from elevenlabs.client import ElevenLabs
from lib.auth_utils import get_current_user

router = APIRouter()
elevenlabs_client = ElevenLabs(api_key=os.environ["ELEVENLABS_API_KEY"])

class SpeakRequest(BaseModel):
    text: str
    language_code: str | None = None  # e.g. "hi" for Hindi, "en" for English

@router.post("/api/voice/speak")
def speak(data: SpeakRequest, user: dict = Depends(get_current_user)):
    kwargs = {
        "text": data.text,
        "voice_id": os.environ["ELEVENLABS_VOICE_ID"],
        "model_id": "eleven_flash_v2_5",
        "output_format": "mp3_44100_128",
    }
    if data.language_code:
        kwargs["language_code"] = data.language_code

    audio_stream = elevenlabs_client.text_to_speech.stream(**kwargs)
    return StreamingResponse(audio_stream, media_type="audio/mpeg")
