@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo       STASH - TELEGRAM: SCAN AND REVIEW
echo ================================================
echo.

if not exist ".venv-wzgram\Scripts\python.exe" (
    echo ERROR: Python virtual environment not found.
    echo Expected: %CD%\.venv-wzgram\Scripts\python.exe
    pause
    exit /b 1
)
if not exist "sync_stash_telegram.py" (
    echo ERROR: sync_stash_telegram.py not found in:
    echo %CD%
    pause
    exit /b 1
)

".venv-wzgram\Scripts\python.exe" "sync_stash_telegram.py"
if errorlevel 1 (
    echo.
    echo Scan failed. Check the error above.
    pause
    exit /b 1
)

echo.
echo Scan complete. Opening the candidate report and summary...
if exist "sync_candidates.csv" start "" "sync_candidates.csv"
if exist "sync_summary.txt" start "" notepad.exe "sync_summary.txt"
echo.
echo Review sync_candidates.csv before deciding whether to apply.
pause
endlocal
