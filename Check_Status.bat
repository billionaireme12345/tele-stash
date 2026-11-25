@echo off
setlocal
echo === Service Status ===
netstat -ano | findstr ":9999 " | findstr "LISTENING" >nul && echo [OK] Stash (9999) is running || echo [DOWN] Stash (9999) is NOT running
netstat -ano | findstr ":8767 " | findstr "LISTENING" >nul && echo [OK] WZGram Bridge (8765) is running || echo [DOWN] WZGram Bridge (8767) is NOT running
netstat -ano | findstr ":9998 " | findstr "LISTENING" >nul && echo [OK] Reverse Proxy (9998) is running || echo [DOWN] Reverse Proxy (9998) is NOT running
echo.
echo === Bridge Health ===
curl.exe -s http://127.0.0.1:8765/health
echo.
pause