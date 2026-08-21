$ErrorActionPreference = "Stop"
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name VideoLocalizer --collect-all faster_whisper --collect-all ctranslate2 --collect-all imageio_ffmpeg src/app.py
