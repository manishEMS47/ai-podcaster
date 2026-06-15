#!/usr/bin/env python3

import subprocess
import random
import base64
import openai
import requests
import time
import json
import sys
import os

# ---------------------------------------------------------------------------
# TTS providers
#
# Two backends are supported behind a common interface so the rest of the
# pipeline (transcript generation, ffmpeg concat, cleanup) is identical no
# matter which one is used:
#
#   provider.name                      -> human readable label
#   provider.pick_voice(speaker, gender) -> a voice id/name, cached + unique per speaker
#   provider.synthesize(text, voice)   -> raw audio bytes (mp3)
#
# Select with the TTS_PROVIDER env var ("elevenlabs" or "60db"). If unset, the
# provider is auto-detected from whichever API key is present.
# ---------------------------------------------------------------------------


class TTSRateLimitError(Exception):
    """Raised by a provider when the backend reports a rate limit."""


class ElevenLabsProvider:
    name = "ElevenLabs"

    # ElevenLabs' built-in preset voices, grouped by gender.
    VOICE_NAMES = {
        "male": [
            "Adam",
            "Antoni",
            "Arnold",
            "Callum",
            "Charlie",
            "Clyde",
            "Daniel",
            "Ethan",
        ],
        "female": [
            "Bella",
            "Charlotte",
            "Domi",
            "Dorothy",
            "Elli",
            "Emily",
            "Gigi",
            "Grace",
        ],
    }

    def __init__(self, api_key):
        # Imported lazily so the script still runs with only 60dB configured.
        from elevenlabs import generate, set_api_key, RateLimitError

        set_api_key(api_key)
        self._generate = generate
        self._RateLimitError = RateLimitError

        self._pools = {g: list(v) for g, v in self.VOICE_NAMES.items()}
        self._assigned = {}

    def pick_voice(self, speaker, gender):
        if speaker not in self._assigned:
            pool = self._pools.get(gender, [])
            choice = random.choice(pool)
            pool.remove(choice)
            self._assigned[speaker] = choice
        return self._assigned[speaker]

    def synthesize(self, text, voice):
        try:
            return self._generate(
                text=text,
                voice=voice,
                model="eleven_monolingual_v1",
            )
        except self._RateLimitError:
            raise TTSRateLimitError("ElevenLabs ratelimit exceeded!")


class SixtyDBProvider:
    name = "60dB"
    BASE_URL = "https://api.60db.ai"

    def __init__(self, api_key):
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._pools = self._load_voice_pools()
        # Keep the full lists so we can reuse a voice if a gender's pool runs
        # out (60dB accounts often have only a couple of voices).
        self._all = {g: list(v) for g, v in self._pools.items()}
        self._assigned = {}

    def _load_voice_pools(self):
        resp = requests.get(f"{self.BASE_URL}/myvoices", headers=self._headers, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])

        pools = {"male": [], "female": []}
        for voice in data:
            gender = (voice.get("labels") or {}).get("gender", "").lower()
            if gender in pools and voice.get("voice_id"):
                pools[gender].append(voice["voice_id"])

        if not pools["male"] and not pools["female"]:
            sys.exit(
                "ERROR: No 60dB voices found on your account. "
                "Create or clone voices, then check GET /myvoices."
            )
        return pools

    def pick_voice(self, speaker, gender):
        if speaker not in self._assigned:
            pool = self._pools.get(gender, [])
            fallback = self._all.get(gender, [])
            if pool:
                # Prefer a fresh, unique voice for this speaker.
                choice = random.choice(pool)
                pool.remove(choice)
            elif fallback:
                # Pool exhausted: reuse an existing voice of this gender.
                choice = random.choice(fallback)
            else:
                sys.exit(f"ERROR: No 60dB voices available for gender '{gender}'.")
            self._assigned[speaker] = choice
        return self._assigned[speaker]

    def synthesize(self, text, voice):
        resp = requests.post(
            f"{self.BASE_URL}/tts-synthesize",
            headers={**self._headers, "Content-Type": "application/json"},
            json={
                "text": text,
                "voice_id": voice,
                "output_format": "mp3",
            },
            timeout=120,
        )

        if resp.status_code == 429:
            raise TTSRateLimitError("60dB ratelimit exceeded!")
        resp.raise_for_status()

        body = resp.json()
        if not body.get("success", True):
            raise RuntimeError(f"60dB TTS error: {body.get('message')}")

        return base64.b64decode(body["audio_base64"])


def make_provider():
    provider = (os.getenv("TTS_PROVIDER") or "").strip().lower()
    sixtydb_key = os.getenv("SIXTYDB_API_KEY")
    elevenlabs_key = os.getenv("ELEVENLABS_API_KEY")

    # Auto-detect from whichever key is present when TTS_PROVIDER is unset.
    if not provider:
        provider = "60db" if sixtydb_key else "elevenlabs"

    if provider in ("60db", "sixtydb", "60", "sixty"):
        if not sixtydb_key:
            sys.exit("ERROR: TTS_PROVIDER=60db but SIXTYDB_API_KEY is not set.")
        return SixtyDBProvider(sixtydb_key)

    if provider in ("elevenlabs", "eleven", "11labs", "el"):
        if not elevenlabs_key:
            sys.exit("ERROR: TTS_PROVIDER=elevenlabs but ELEVENLABS_API_KEY is not set.")
        return ElevenLabsProvider(elevenlabs_key)

    sys.exit(f"ERROR: Unknown TTS_PROVIDER '{provider}'. Use 'elevenlabs' or '60db'.")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

print("## AI-Podcaster by Unconventional Coding ##\n")

# OpenAI imports environment variable OPENAI_API_KEY by default

if len(sys.argv) > 1:
    with open(sys.argv[1]) as f:
        podcast_description = f.read().strip()
else:
    podcast_description = input("What is the podcast about?\n")
    print()

if len(sys.argv) > 2:
    dialog_count = int(sys.argv[2])
else:
    dialog_count = int(input("How many dialogs do you want? [5]\n") or "5")

if not os.path.exists("dialogs"):
    os.mkdir("dialogs")

if not os.path.exists("podcasts"):
    os.mkdir("podcasts")

messages = [
    {
        "role": "system",
        "content": "You are a podcast generator. Generate the dialog for a podcast based on the description given by the user.",
    },
    {
        "role": "user",
        "content": podcast_description,
    }
]

podcast_id = f"{time.time()}"

def generate_dialog(number_of_dialogs, debug=False):
    transcript_file_name = f"podcasts/podcast{podcast_id}.txt"
    transcript_file = open(transcript_file_name, "w")

    dialogs = []

    for _ in range(0, number_of_dialogs):
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            functions=[
                {
                    "name": "add_dialog",
                    "description": "Add dialog to the podcast",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "speaker": {
                                "type": "string",
                                "description": "The name of the speaker"
                            },
                            "gender": {
                                "type": "string",
                                "description": "The gender of the speaker (male of female)"
                            },
                            "content": {
                                "type": "string",
                                "description": "The content of the speech"
                            }
                        },
                        "required": ["speaker", "gender", "content"]
                    }
                }
            ],
            function_call={
                "name": "add_dialog",
                "arguments": ["speaker", "gender", "content"]
            }
        )

        message = response.choices[0].message # type: ignore

        if debug:
            print(f"response: {response}")
            print(f"usage: {dict(response).get('usage')}\n")
            print(f"dump_json: {response.model_dump_json(indent=2)}\n")
            print(f"message: {message}\n")

        messages.append(message)

        function_call = message.function_call
        arguments = json.loads(function_call.arguments)

        transcript_file.write(arguments['speaker'] + " says: " + arguments['content'] + "\n")

        dialogs.append(arguments)

    transcript_file.close()
    return (dialogs, transcript_file_name)


dialog_files = []
concat_file = open("concat.txt", "w")

print("Generating transcript")

dialogs, transcript_file_name = generate_dialog(dialog_count)

provider = make_provider()

print(f"Generating audio with {provider.name}")
try:
    for i, dialog in enumerate(dialogs):
        voice = provider.pick_voice(dialog["speaker"], dialog["gender"].lower())
        audio = provider.synthesize(dialog["content"], voice)

        filename = f"dialogs/dialog{i}.mp3"
        concat_file.write("file " + filename + "\n")
        dialog_files.append(filename)

        with open(filename, "wb") as audio_file:
            audio_file.write(audio)
except TTSRateLimitError as error:
    print(f"ERROR: {error}")

concat_file.close()

podcast_file = f"podcasts/podcast{podcast_id}.mp3"

print("Concatenating audio")
subprocess.run(f"ffmpeg -f concat -safe 0 -i concat.txt -c copy {podcast_file}", shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

os.unlink("concat.txt")

for file in dialog_files:
    os.unlink(file)

print("\n## Podcast is ready! ##")
print("Audio: " + podcast_file)
print("Transcript: " + transcript_file_name)
