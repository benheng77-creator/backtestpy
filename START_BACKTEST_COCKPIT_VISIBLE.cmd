@echo off
title BACKTEST COCKPIT SERVER - KEEP THIS WINDOW OPEN
cd /d "C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS"
echo ========================================
echo BACKTEST COCKPIT SERVER
echo KEEP THIS WINDOW OPEN
echo URL: http://127.0.0.1:8787/
echo ========================================
py -3 -u "C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS\_ui\SAFE_BACKTEST_COCKPIT.py"
echo.
echo SERVER EXITED. COPY THE ERROR ABOVE.
pause
