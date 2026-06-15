# AI-Podcaster

Create podcasts about any subject using ChatGPT and your choice of text-to-speech provider: **ElevenLabs** or **60dB**.

## Requirements

- Python 3
- [`ffmpeg`](https://ffmpeg.org/) on your `PATH` (used to stitch the audio clips together)
- Python packages: `pip install -r requirements.txt`

## Quick Start

```shell
$ ./ai_podcaster.py [INPUT_FILE] [DIALOG_COUNT]
```

Input file should be a text file with a description of the podcast you want to create. Dialog count determines the length of the podcast.

You can run the script without command line arguments and it will ask you to input them instead.

## API Keys

You always need an OpenAI API key (used to write the script), plus a key for whichever
text-to-speech provider you want to use:

```shell
$ export OPENAI_API_KEY=YOUR_OPENAI_API_KEY

# Use one (or both):
$ export ELEVENLABS_API_KEY=YOUR_ELEVENLABS_API_KEY
$ export SIXTYDB_API_KEY=YOUR_60DB_API_KEY
```

## Choosing the TTS provider

Set `TTS_PROVIDER` to either `elevenlabs` or `60db`:

```shell
$ export TTS_PROVIDER=60db        # or: elevenlabs
```

If `TTS_PROVIDER` is unset, the provider is auto-detected from whichever key is
present (60dB is preferred if `SIXTYDB_API_KEY` is set).

- **ElevenLabs** uses its built-in preset voices, picked randomly by speaker gender.
- **60dB** uses the voices on your account (`GET /myvoices`), also picked by gender.
  Make sure you have at least one male and one female voice for best results.

Both providers share the same pipeline, so output is consistent regardless of choice.

## How it works

1. **ChatGPT** (`gpt-3.5-turbo`) writes the podcast script turn by turn, returning each
   line as structured `{speaker, gender, content}` data via function calling.
2. The selected **TTS provider** speaks each line. Every speaker keeps the same voice for
   the whole episode (voices are assigned by gender and cached per speaker):
   - **ElevenLabs** &rarr; legacy SDK, `eleven_monolingual_v1` model, built-in preset voices.
   - **60dB** &rarr; `POST https://api.60db.ai/tts-synthesize` (Bearer auth), voices loaded
     from `GET https://api.60db.ai/myvoices`, audio returned as base64 and decoded to MP3.
3. **ffmpeg** concatenates the per-line clips into a single MP3 in `podcasts/`, alongside
   the plain-text transcript.

Enjoy! (and [buy me a coffee](https://buymeacoffee.com/unconv))
