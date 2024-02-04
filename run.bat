@echo off
cd "%~dp0"

ffmpeg -L 2> NUL > NUL
if errorlevel 1 (
  echo Install ffmpeg first
  echo   winget install ffmpeg
  exit 1
)

if not exist .venv\scripts\activate (
  echo Creating virtualenv
  python -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements-windows.txt
) else (
  call .venv\Scripts\activate.bat
)

:: If pytorch fail to use CUDA on Windows, you may have to run:
::   nvcc --version
:: to determine the current CUDA version, then use something like:
::   pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
:: or better, read:
:: https://pytorch.org/#:~:text=Aid%20to%20Ukraine.-,INSTALL%20PYTORCH,-Select%20your%20preferences

python main.py "$@"
