@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo       STASH - TELEGRAM: APPLY MATCHES
echo ================================================
echo.
echo WARNING:
echo This reruns the synchronizer in APPLY mode.
echo It saves the Telegram catalogue and Stash snapshots,
echo and inserts every unique high-confidence match found.
echo It does NOT read your manual edits to sync_candidates.csv.
echo Existing scene/message mappings are protected.
echo.

if not exist ".venv-wzgram\Scripts\python.exe" (
    echo ERROR: Python virtual environment not found.
    pause
    exit /b 1
)
if not exist "sync_stash_telegram.py" (
    echo ERROR: sync_stash_telegram.py not found.
    pause
    exit /b 1
)

set /p CONFIRM=Have you reviewed the latest scan and want to apply all unique high-confidence matches? (Y/N): 
if /I not "%CONFIRM%"=="Y" (
    echo Cancelled. No changes made.
    pause
    exit /b 0
)

echo.
echo Running synchronizer in APPLY mode...
".venv-wzgram\Scripts\python.exe" "sync_stash_telegram.py" --apply
if errorlevel 1 (
    echo.
    echo APPLY FAILED. Check the error above.
    pause
    exit /b 1
)

echo.
echo Apply completed. Opening the summary...
if exist "sync_summary.txt" start "" notepad.exe "sync_summary.txt"
pause
endlocal
