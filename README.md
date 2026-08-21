# Video Localizer

An open-source Windows desktop tool that turns an English video into a new video with burned-in Simplified Chinese subtitles.

## What It Does

```text
video.mp4 -> faster-whisper timestamps -> batched AI translation -> FFmpeg -> video_zh-subbed.mp4
```

Each translated subtitle keeps the time range of its source cue. Translation requests use numbered JSON entries, and the response is validated before FFmpeg renders the result.

## Features

- Windows GUI: choose an input video and output MP4
- `faster-whisper` speech recognition with timestamps
- English-to-Simplified-Chinese translation through an OpenAI-compatible API
- Batched translation with JSON id validation and retries
- FFmpeg hard-subtitle rendering with H.264 video and copied audio
- Command-line entry point for automation

This repository contains only the core video-localization workflow. It does not include the separate publishing-materials generator, personal API credentials, model caches, or media files.

## Download The Portable Windows Build

For users who only want to run the application, download `VideoLocalizer-v1.0.0-win64.zip` from the repository's GitHub Releases page. Extract it and double-click `VideoLocalizer.exe`; Python and FFmpeg do not need to be installed separately.

The portable build still requires the user to enter their own API Key, API URL, and model. The Whisper model is downloaded on first use and is not included in the release archive.

## Requirements

- Python 3.11 or 3.12
- FFmpeg in `PATH` (or the `imageio-ffmpeg` fallback dependency)
- Internet access on first run to download a Whisper model
- An OpenAI-compatible chat completion endpoint and API key

## Install

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in local values. Never commit `.env` or a real API key.

```dotenv
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://your-compatible-endpoint/v1
OPENAI_TRANSLATION_MODEL=your-model
```

## Run The GUI

```powershell
python src/app.py
```

Choose the input video and an output path ending in `.mp4`, then click **开始翻译**. The original video is not modified.

## Run From The Command Line

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:OPENAI_BASE_URL = "https://your-compatible-endpoint/v1"
$env:OPENAI_TRANSLATION_MODEL = "your-model"
python src/localize_video.py input\example.mp4 -o output\example_zh-subbed.mp4
```

## Build Windows EXE

```powershell
.\build_exe.ps1
```

The executable is generated under `dist\VideoLocalizer.exe`. Runtime configuration remains external.

## Limitations

- The public workflow currently assumes English speech and Simplified Chinese output.
- Hard subtitles require video re-encoding.
- Translation quality depends on the Whisper model and API model.
- Process only videos you own or are authorized to translate and redistribute.
