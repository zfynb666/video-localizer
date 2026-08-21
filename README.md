# Video Localizer

An open-source Windows desktop tool that transcribes a video, optionally translates its subtitles, and burns the selected target language into a new video.

## What It Does

```text
video.mp4 -> faster-whisper timestamps -> batched AI translation -> FFmpeg -> video_zh-subbed.mp4
```

Each translated subtitle keeps the time range of its source cue. Translation requests use numbered JSON entries, and the response is validated before FFmpeg renders the result.

## Features

- Windows GUI with input video and output directory selection
- Selectable Whisper models: `tiny`, `base`, `small`, `medium`, `large-v3`, and `turbo`
- Selectable source and target languages; matching languages skip AI translation
- Separate OpenAI-compatible Base URL, API Key, and AI translation model settings
- Saved local configuration, real-time progress, logs, and cancellation
- Configurable batched translation with bounded parallel requests
- JSON id validation, missing-entry recovery, and retries for failed batches
- Automatic resume from saved source subtitles and validated translation batch checkpoints
- FFmpeg hard-subtitle rendering with H.264 video and copied audio

This repository contains only the core video-localization workflow. It does not include the separate publishing-materials generator, personal API credentials, model caches, or media files.

## Download The Portable Windows Build

For users who only want to run the application, download the newest `VideoLocalizer-*-win64.zip` from the repository's GitHub Releases page. Extract it and double-click `VideoLocalizer.exe`; Python and FFmpeg do not need to be installed separately.

The portable build still requires the user to enter their own API Key, API URL, and model. The Whisper model is downloaded on first use and is not included in the release archive.

## Requirements

- Python 3.11 or 3.12 for source usage
- FFmpeg in `PATH` (or the `imageio-ffmpeg` fallback dependency)
- Internet access on first run to download a Whisper model
- An OpenAI-compatible chat completion endpoint and API key

## Install

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The desktop application saves its settings to a local `config.json` next to the executable. Use `config.example.json` only as a template and never commit a real API key.

## Run The GUI

```powershell
python src/app.py
```

Choose an input video and output directory, select the Whisper model and languages, fill in the AI interface settings, then click **开始处理**. The original video is not modified.

If a run is interrupted, selecting the same video with the same processing settings resumes from local records under `data/jobs`. Completed Whisper transcription and validated translation batches are not requested again.

`Whisper 模型` controls speech recognition speed and accuracy. `AI 接口设置 -> 模型` is the AI model used to translate the subtitle text. `每批条数` defaults to `50`, and `并发请求` defaults to `3`; reduce concurrency if your provider returns rate-limit or temporary-unavailable errors.

## Build Windows EXE

```powershell
.\build_exe.ps1
```

The executable is generated under `dist\VideoLocalizer.exe`. Runtime configuration remains external.

## Limitations

- Available languages are the options shown in the desktop application.
- Hard subtitles require video re-encoding.
- Translation quality depends on the Whisper model and API model.
- Process only videos you own or are authorized to translate and redistribute.
