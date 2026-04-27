@echo off
cd /d "C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS"
echo STARTING UI FROM %CD%
py -3 "C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS\_ui\backtest_ui.py" 1> "C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS\_ui\ui_stdout.log" 2> "C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS\_ui\ui_stderr.log"
echo EXITCODE=%ERRORLEVEL%>>"C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS\_ui\ui_stderr.log"
pause
