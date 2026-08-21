$ErrorActionPreference = "Stop"
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoLocalizer `
    --collect-all faster_whisper `
    --collect-all ctranslate2 `
    --collect-all imageio_ffmpeg `
    --collect-all openai `
    --collect-all dotenv `
    src/app.py
