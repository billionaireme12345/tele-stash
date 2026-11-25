@echo off
title Stash Cover Audit

cd /d "%~dp0"

echo ==========================================
echo         STASH COVER AUDIT
echo ==========================================
echo.

"%~dp0.venv-wzgram\Scripts\python.exe" "%~dp0stash_cover_audit.py"

echo.
pause