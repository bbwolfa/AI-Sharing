@echo off
python -B "%~dp0_tools\reader_server.py" --open-browser
if errorlevel 1 (
    echo The report reader could not start. Please review the error above.
    pause
    exit /b 1
)
