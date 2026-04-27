$ROOT = "C:\Users\jvben\Desktop\EXHANGE BACKTET DATAS"
$UIFILE = "$ROOT\_ui\backtest_ui.py"
$OUT = "$ROOT\_ui\ui_stdout.log"
$ERR = "$ROOT\_ui\ui_stderr.log"

Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue |
ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

Get-CimInstance Win32_Process |
Where-Object { $_.CommandLine -like "*backtest_ui.py*" } |
ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Remove-Item $OUT,$ERR -Force -ErrorAction SilentlyContinue

$PYEXE = (& py -3 -c "import sys; print(sys.executable)").Trim()

Start-Process -FilePath $PYEXE -ArgumentList @("-u", "$UIFILE") -WorkingDirectory $ROOT -RedirectStandardOutput $OUT -RedirectStandardError $ERR

Start-Sleep -Seconds 3

try {
    Invoke-WebRequest "http://127.0.0.1:8787/health" -UseBasicParsing | Out-Null
    Start-Process "http://127.0.0.1:8787/"
    Write-Host "READY: http://127.0.0.1:8787/"
} catch {
    Write-Host "UI FAILED"
    Write-Host "
STDOUT:"
    Get-Content $OUT -Tail 80 -ErrorAction SilentlyContinue
    Write-Host "
STDERR:"
    Get-Content $ERR -Tail 200 -ErrorAction SilentlyContinue
    pause
}
