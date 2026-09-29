@echo off
if "%~1"=="" (
    python -B "%~dp0_tools\archive_reports.py" --refresh-index
) else (
    python -B "%~dp0_tools\archive_reports.py" --mark-read %*
)
if errorlevel 1 (
    echo Reading status was not updated. Please review the error above.
    pause
    exit /b 1
)
