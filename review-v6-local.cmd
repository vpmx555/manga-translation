@echo off
setlocal
"%~dp0venv\Scripts\python.exe" -B "%~dp0deployment\runpod\local_review.py" --start --install-startup %*
if errorlevel 1 (
    echo Local review watcher failed to start.
    pause
    exit /b 1
)
