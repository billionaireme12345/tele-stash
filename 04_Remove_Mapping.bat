@echo off
setlocal
cd /d "%~dp0"
title Stash Telegram - Remove Mapping

echo ==========================================
echo       STASH - REMOVE TELEGRAM MAPPING
echo ==========================================
echo.
if not exist ".venv-wzgram\Scripts\python.exe" (
    echo ERROR: Python virtual environment not found.
    pause
    exit /b 1
)

".venv-wzgram\Scripts\python.exe" -c "import sqlite3; db=sqlite3.connect('bridge.sqlite3'); scene=input('Enter Stash Scene ID to unmap: ').strip(); row=db.execute('SELECT scene_id,message_id,confidence FROM scene_telegram_map WHERE scene_id=?',(scene,)).fetchone(); print('\nCurrent mapping:', row if row else 'NOT FOUND'); confirm=input('\nRemove this mapping only? (y/N): ').strip().lower() if row else 'n'; db.execute('DELETE FROM scene_telegram_map WHERE scene_id=?',(scene,)) if confirm=='y' else None; db.commit() if confirm=='y' else None; print('\nMapping removed. Scene and Telegram catalogue are preserved.' if confirm=='y' else '\nCancelled. Nothing changed.'); db.close()"

echo.
pause
