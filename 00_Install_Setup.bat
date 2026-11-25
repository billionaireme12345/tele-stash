@echo off
setlocal
cd /d "%~dp0"

echo [1/4] Checking Python and FFmpeg...
where py >nul 2>&1 || (echo ERROR: Python 3.10+ not found. & pause & exit /b 1)
where ffprobe >nul 2>&1 || echo WARNING: ffprobe/ffmpeg not found in PATH. Timestamp seeking requires FFmpeg.

echo [2/4] Creating virtual environment (venv-wzgram)...
if not exist "venv-wzgram" py -m venv venv-wzgram
call "venv-wzgram\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo [3/4] Creating .env from template...
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env - please fill in TG_API_ID, TG_API_HASH, and TG_CHANNEL_ID.
)

echo [4/4] Installing Stash UI plugin...
set "PLUGIN_DIR=%USERPROFILE%\.stash\plugins"
if not exist "%PLUGIN_DIR%" mkdir "%PLUGIN_DIR%"
copy /Y "stash_plugin\telegram_stream_override.js" "%PLUGIN_DIR%\" >nul
copy /Y "stash_plugin\telegram_stream_override.yml" "%PLUGIN_DIR%\" >nul

echo.
echo Setup complete! Edit .env, reload plugins in Stash, and run Start_All.bat.
pause